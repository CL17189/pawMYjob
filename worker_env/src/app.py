from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from .config import ARTIFACTS_DIR, SEARCH_CONFIG_PATH, save_searches, load_searches
from .db import get_job, latest_runs, list_jobs, stats, update_application_status, update_stage
from .pipeline import run_pipeline

APP_DIR = Path(__file__).resolve().parents[1]
app = Flask(__name__, template_folder=str(APP_DIR / "templates"), static_folder=str(APP_DIR / "static"))
_run_lock = threading.Lock()
_run_state = {"running": False, "last_result": None, "error": None}


@app.get("/")
def index():
    return render_template("index.html", stats=stats(), searches=load_searches(), run_state=_run_state)


@app.get("/api/jobs")
def jobs_api():
    return jsonify(list_jobs(
        stage=request.args.get("stage", "all"), date_range=request.args.get("range", "all"),
        country=request.args.get("country", ""), query=request.args.get("query", ""),
    ))


@app.post("/api/jobs/<int:job_id>/stage")
def stage_api(job_id: int):
    body = request.get_json(silent=True) or {}
    try:
        update_stage(job_id, body.get("stage", "review"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "job": get_job(job_id) is not None})


@app.post("/api/jobs/<int:job_id>/status")
def status_api(job_id: int):
    body = request.get_json(silent=True) or {}
    try:
        update_application_status(job_id, body.get("status", "new"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.get("/api/searches")
def searches_get():
    return jsonify(load_searches())


@app.post("/api/searches")
def searches_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, list):
        return jsonify({"error": "Expected a JSON list of search definitions"}), 400
    for item in payload:
        if not isinstance(item, dict) or not item.get("query") or not item.get("country"):
            return jsonify({"error": "Every search needs country and query"}), 400
    save_searches(payload, SEARCH_CONFIG_PATH)
    return jsonify({"ok": True, "searches": payload})


@app.get("/api/runs")
def runs_api():
    return jsonify({"runs": latest_runs(), "state": _run_state})


@app.post("/api/run")
def run_api():
    if _run_lock.locked():
        return jsonify({"error": "A run is already in progress"}), 409

    def worker():
        with _run_lock:
            _run_state.update(running=True, error=None)
            try:
                _run_state["last_result"] = run_pipeline()
            except Exception as exc:
                _run_state["error"] = str(exc)
            finally:
                _run_state["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "message": "Pipeline started"}), 202


@app.get("/artifacts/<int:job_id>/<name>")
def artifact(job_id: int, name: str):
    if name not in {"resume.tex", "resume.pdf", "cover_letter.tex"}:
        abort(404)
    target = (ARTIFACTS_DIR / str(job_id) / name).resolve()
    if ARTIFACTS_DIR.resolve() not in target.parents or not target.exists():
        abort(404)
    return send_file(target, as_attachment=True, download_name=name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
