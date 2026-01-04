# src/app.py
from flask import Flask, render_template, send_file, abort
from pathlib import Path
import json
from .utils import RUNS_DIR

app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / "templates"))

@app.route("/")
def index():
    runs = []
    for p in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        d = json.load(open(p, "r", encoding="utf-8"))
        meta = d.get("meta", {})
        runs.append({"run_id": meta.get("run_id"), "timestamp": meta.get("timestamp"), "path": p.name})
    return render_template("index.html", runs=runs)

@app.route("/results/<run_id>")
def show_run(run_id):
    p = RUNS_DIR / f"{run_id}.json"
    if not p.exists():
        abort(404)
    data = json.load(open(p, "r", encoding="utf-8"))
    # front-end 可切换 view=table|board|country，通过 query param 控制（前端 JS）
    return render_template("results.html", data=data)

if __name__ == "__main__":
    app.run(debug=True, port=8000)
