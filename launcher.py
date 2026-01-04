#!/usr/bin/env python3
"""
Launcher: 提供一个本地网页用于输入 OPENAI_API_KEY、FIRECRAWL_API_KEY，上传 resume.md，
并在提交后：
  1) 将 resume.md 存入 worker_env/stored_data/resume.md
  2) 将密钥写入 worker_env/.env (key names OPENAI_API_KEY, FIRECRAWL_API_KEY)
  3) 启动 worker pipeline: uses worker venv python to run module worker_env.src.langgraph_flow
  4) 启动 worker 前端 (worker_env/src/app.py -> localhost:8000)
  5) 在默认浏览器打开结果页面 http://localhost:8000/

使用方法（简短）：
  1) cd /path/to/job_agent
  2) 激活 worker venv（推荐）： source worker_env/.venv/bin/activate    （Windows: worker_env\\.venv\\Scripts\\activate）
  3) python launcher.py
  4) 在打开的页面 (http://localhost:7000) 上传 resume.md 并填入密钥，点击 Submit
"""

import os
import sys
import shutil
import threading
import subprocess
import time
import platform
import webbrowser
from pathlib import Path
from flask import Flask, request, render_template_string, redirect, url_for, send_from_directory

ROOT = Path(__file__).resolve().parent
WORKER_DIR = ROOT / "worker_env"
WORKER_SRC = WORKER_DIR / "src"
STORED_DATA = WORKER_DIR / "stored_data"
ENV_PATH = WORKER_DIR / ".env"

# server ports
LAUNCHER_PORT = 7000
FRONTEND_PORT = 8000

# minimal HTML form
HTML_INDEX = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Job Agent - Initial Setup</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:900px;margin:24px;">
  <h2>Job Agent - Initial Setup</h2>
  <p>Upload your <code>resume.md</code> and enter keys. After submit, pipeline will run and front-end will be available at <strong>http://localhost:8000</strong>.</p>
  <form method="post" enctype="multipart/form-data" action="{{ url_for('submit') }}">
    <label>Resume (.md file):</label><br/>
    <input type="file" name="resume" accept=".md" required><br/><br/>
    <label>OPENAI_API_KEY (optional — enables LLM scoring):</label><br/>
    <input type="text" name="openai" style="width:80%" placeholder="sk-..."><br/><br/>
    <label>FIRECRAWL_API_KEY (optional):</label><br/>
    <input type="text" name="firecrawl" style="width:80%" placeholder="fc-..."><br/><br/>
    <label>Search query (default: data engineer):</label><br/>
    <input type="text" name="query" style="width:50%" value="data engineer"><br/><br/>
    <button type="submit">Submit & Run</button>
  </form>

  <hr/>
  <h4>Current worker env</h4>
  <pre>{{ info }}</pre>
  <p>Logs (tail): <a href="/logs">open logs</a></p>
</body>
</html>
"""

HTML_DONE = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Job Agent - Launched</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;margin:24px;">
  <h2>Pipeline started</h2>
  <p>The worker pipeline has been started and the frontend should be available at <a href="http://localhost:8000" target="_blank">http://localhost:8000</a>.</p>
  <p>Logs are being written to <code>{{ logpath }}</code>. You can keep this window open to see basic status or close it — the worker will run in background.</p>
  <p><a href="/">Back</a></p>
</body>
</html>
"""

app = Flask(__name__)

def ensure_worker_structure():
    """Ensure worker directories exist and minimal package init files exist for -m imports."""
    STORED_DATA.mkdir(parents=True, exist_ok=True)
    (WORKER_DIR / "__init__.py").write_text("") if not (WORKER_DIR / "__init__.py").exists() else None
    (WORKER_SRC / "__init__.py").write_text("") if not (WORKER_SRC / "__init__.py").exists() else None

def detect_worker_python():
    """Return path to worker venv python executable, guessing common locations."""
    # If env var provided, respect it
    env_wp = os.environ.get("WORKER_PY")
    if env_wp:
        return Path(env_wp)

    # common candidate
    candidates = []
    if platform.system() == "Windows":
        candidates = [
            WORKER_DIR / ".venv" / "Scripts" / "python.exe",
            WORKER_DIR / ".venv" / "python.exe",
        ]
    else:
        candidates = [
            WORKER_DIR / ".venv" / "bin" / "python",
            WORKER_DIR / ".venv" / "python",
        ]
    for c in candidates:
        if c.exists():
            return c
    # fallback: system python
    return Path(sys.executable)

def write_env_file(openai_key: str, fire_key: str):
    lines = []
    if openai_key:
        lines.append(f"OPENAI_API_KEY={openai_key}")
    if fire_key:
        lines.append(f"FIRECRAWL_API_KEY={fire_key}")
    # also write WORKER_PY optionally
    wp = detect_worker_python()
    lines.append(f"WORKER_PY={wp}")
    ENV_PATH.write_text("\n".join(lines))
    return ENV_PATH

LOG_DIR = ROOT / "launcher_logs"
LOG_DIR.mkdir(exist_ok=True)
PIPE_LOG = LOG_DIR / "worker_run.log"
FRONTEND_LOG = LOG_DIR / "frontend.log"

def run_worker_process(resume_path: Path, query: str, openai_key: str, fire_key: str):
    """
    Start the worker pipeline using worker venv python.
    Two subprocesses are started:
      1) pipeline: python -m worker_env.src.langgraph_flow --resume <resume> --query "<query>"
      2) frontend: python -m worker_env.src.app (serves on port 8000)
    Both are launched with env vars OPENAI_API_KEY and FIRECRAWL_API_KEY set.
    """
    ensure_worker_structure()
    python_exec = str(detect_worker_python())
    env = os.environ.copy()
    if openai_key:
        env["OPENAI_API_KEY"] = openai_key
    if fire_key:
        env["FIRECRAWL_API_KEY"] = fire_key
    # ensure PYTHONPATH includes worker_env so -m worker_env.src.* works
    env["PYTHONPATH"] = str(WORKER_DIR)
    # run pipeline command
    pipeline_cmd = [
        python_exec, "-m", "worker_env.src.langgraph_flow",
        "--resume", str(resume_path),
        "--query", query
    ]
    frontend_cmd = [
        python_exec, "-m", "worker_env.src.app"
    ]

    # start pipeline and frontend in background, redirect logs
    with open(PIPE_LOG, "ab") as pb, open(FRONTEND_LOG, "ab") as fb:
        # start pipeline
        p1 = subprocess.Popen(pipeline_cmd, env=env, cwd=str(ROOT), stdout=pb, stderr=pb)
        # small delay to let pipeline start
        time.sleep(1.0)
        # start frontend
        p2 = subprocess.Popen(frontend_cmd, env=env, cwd=str(ROOT), stdout=fb, stderr=fb)
    return p1, p2

@app.route("/", methods=["GET"])
def index():
    # show worker env info
    wp = detect_worker_python()
    info = f"Worker venv python: {wp}\\nWorker dir: {WORKER_DIR}\\nStored data dir: {STORED_DATA}\\nEnv file: {ENV_PATH}"
    return render_template_string(HTML_INDEX, info=info)

@app.route("/submit", methods=["POST"])
def submit():
    # handle file upload
    uploaded = request.files.get("resume")
    if not uploaded:
        return "resume.md is required", 400
    fname = "resume.md"
    dest = STORED_DATA / fname
    uploaded.save(str(dest))

    openai_key = request.form.get("openai", "").strip()
    fire_key = request.form.get("firecrawl", "").strip()
    query = request.form.get("query", "data engineer").strip() or "data engineer"

    # write env
    write_env_file(openai_key, fire_key)

    # launch worker (background)
    p1, p2 = run_worker_process(dest, query, openai_key, fire_key)

    # open frontend in browser (allow some startup time)
    def open_browser_later():
        time.sleep(2.0)
        try:
            webbrowser.open(f"http://localhost:{FRONTEND_PORT}", new=2)
        except Exception:
            pass
    threading.Thread(target=open_browser_later, daemon=True).start()

    return render_template_string(HTML_DONE, logpath=str(PIPE_LOG))

@app.route("/logs")
def logs_index():
    files = []
    for f in LOG_DIR.glob("*.log"):
        files.append(f.name)
    html = "<h3>Logs</h3><ul>"
    for fn in files:
        html += f'<li><a href="/logs/{fn}">{fn}</a></li>'
    html += "</ul><p><a href='/'>Back</a></p>"
    return html

@app.route("/logs/<path:fn>")
def logs_file(fn):
    p = LOG_DIR / fn
    if not p.exists():
        return "Not found", 404
    return send_from_directory(str(LOG_DIR), fn)

def main():
    ensure_worker_structure()
    print("Launcher starting. Visit http://localhost:%d in your browser." % LAUNCHER_PORT)
    # run Flask dev server (single-thread ok for local usage)
    app.run(host="127.0.0.1", port=LAUNCHER_PORT, debug=False)

if __name__ == "__main__":
    main()
