"""Backward-compatible entry point for the refactored incremental pipeline."""

import argparse
import json

from .pipeline import run_pipeline


def run_flow(resume_md_path: str, query: str = "data engineer"):
    # Query is retained for old callers; new searches are configured in config/searches.json.
    return run_pipeline(resume=resume_md_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", required=True)
    parser.add_argument("--query", default="data engineer")
    args = parser.parse_args()
    print(json.dumps(run_flow(args.resume, args.query), ensure_ascii=False, indent=2))
