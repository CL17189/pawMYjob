from __future__ import annotations

import argparse
import json
import logging

from .pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one pawMYjob incremental crawl")
    parser.add_argument("--resume", help="Markdown or PDF resume path")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run_pipeline(resume=args.resume, send_report=not args.no_email), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

