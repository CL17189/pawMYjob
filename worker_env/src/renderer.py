# worker_env/src/renderer.py
from pathlib import Path
from .utils import RUNS_DIR, now_iso, save_json
import json, uuid

def persist_run_result(matches_dict, meta: dict):
    run_id = meta.get("run_id") or uuid.uuid4().hex[:8]
    meta["run_id"] = run_id
    path = RUNS_DIR / f"{run_id}.json"
    obj = {"meta": meta, "matches": matches_dict}
    save_json(obj, path)
    return path

def render_html_for_run(run_json_path: str, out_html_path: str=None):
    data = json.load(open(run_json_path, "r", encoding="utf-8"))
    if out_html_path is None:
        out_html_path = str(Path(run_json_path).with_suffix(".html"))
    html_lines = []
    html_lines.append("<html><head><meta charset='utf-8'><title>Job Matches</title></head><body>")
    meta = data.get("meta", {})
    html_lines.append(f"<h1>Run {meta.get('run_id')} - {meta.get('timestamp')}</h1>")
    for country, jobs in data.get("matches", {}).items():
        html_lines.append(f"<h2>{country} ({len(jobs)})</h2><div style='display:flex;flex-wrap:wrap'>")
        for j in jobs:
            title = j.get("title") or ""
            cat = j.get("category")
            conf = j.get("final_score", 0)
            url = j.get("url","")
            expl = j.get("llm",{}).get("explanation") or j.get("explanation","")
            txt = (j.get("raw") or "")[:600]
            card = f"""
            <div style='width:320px;border:1px solid #ddd;margin:8px;padding:8px;border-radius:6px'>
              <div style='font-weight:600'>{title}</div>
              <div style='font-size:12px;margin:4px 0'><strong>{cat}</strong> · confidence {conf:.1f}%</div>
              <div style='font-size:12px'>{txt}...</div>
              <div style='font-size:12px;color:#444;margin-top:6px'><em>{expl}</em></div>
              <div style='margin-top:6px'><a href="{url}" target="_blank">Open</a></div>
            </div>
            """
            html_lines.append(card)
        html_lines.append("</div>")
    html_lines.append("</body></html>")
    Path(out_html_path).write_text("\n".join(html_lines), encoding="utf-8")
    return out_html_path
