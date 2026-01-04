# worker_env/src/langgraph_flow.py
import argparse, os
from .firecrawl_client import fetch_all_countries
from .parse_md import parse_resume_md
from .matcher import match_all
from .renderer import persist_run_result, render_html_for_run
from .utils import now_iso

def run_flow(resume_md_path: str, query: str="data engineer"):
    ts = now_iso()
    meta = {"timestamp": ts, "query": query, "status": "started"}
    prof = parse_resume_md(resume_md_path)
    profile_text = prof["raw"]
    # 2. fetch jobs
    jobs = fetch_all_countries(query=query)
    # 3. match (matcher expects profile dict)
    matches = match_all(jobs, prof)
    # 4. persist and render
    meta.update({"status": "finished"})
    run_path = persist_run_result(matches, meta)
    html = render_html_for_run(run_path)
    return {"run_json": str(run_path), "html": html}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", required=True, help="path to resume.md")
    parser.add_argument("--query", default="data engineer")
    args = parser.parse_args()
    out = run_flow(args.resume, args.query)
    print(out)
