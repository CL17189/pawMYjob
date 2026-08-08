"""Daily crawl -> incremental screen -> score -> artifact generation pipeline."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ARTIFACTS_DIR, RESUME_PATH, RESUME_PDF_PATH, SNAPSHOTS_DIR, STATE_PATH, env_bool, load_searches
from .db import create_run, finish_run, get_job, init_db, update_analysis, upsert_jobs
from .gemini_agent import GeminiApplicationAgent, write_application_artifacts
from .job_analysis import classify_job, score_language_fit, score_skill_fit
from .mail_report import send_daily_report
from .parse_md import parse_resume_md
from .scrape_linkedin_jobs import browse_search

log = logging.getLogger(__name__)


def resume_text(path: Path | None = None) -> str:
    path = path or (RESUME_PDF_PATH if RESUME_PDF_PATH.exists() else RESUME_PATH)
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
        except ImportError as exc:
            raise RuntimeError("PDF resume support requires pypdf") from exc
    return parse_resume_md(str(path))["raw"]


def resume_skills(path: Path | None = None) -> list[str]:
    path = path or (RESUME_PDF_PATH if RESUME_PDF_PATH.exists() else RESUME_PATH)
    if path.suffix.lower() == ".pdf":
        return []
    return parse_resume_md(str(path)).get("skills", [])


def _normalise(raw: dict[str, Any], search: dict[str, Any]) -> dict[str, Any]:
    item = dict(raw)
    item.setdefault("country", search.get("country", "unknown"))
    item.setdefault("query", search.get("query", ""))
    item.setdefault("location", search.get("location", ""))
    item.setdefault("source", "linkedin")
    item["search_name"] = search.get("name") or f"{item['country']} · {item['query']}"
    return item


def run_pipeline(resume: str | None = None, searches: list[dict[str, Any]] | None = None, send_report: bool = True) -> dict[str, Any]:
    init_db()
    searches = searches if searches is not None else load_searches()
    searches = [s for s in searches if s.get("enabled", True)]
    run_id = create_run(searches)
    stats: dict[str, Any] = {"run_id": run_id, "date": datetime.now(timezone.utc).date().isoformat(), "total_scraped": 0, "new_jobs": 0, "delta_jobs": 0, "eligible_jobs": 0, "scored_jobs": 0, "generated_jobs": 0}
    try:
        all_raw: list[dict[str, Any]] = []
        for search in searches:
            log.info("Scraping %s", search.get("name") or search.get("query"))
            all_raw.extend(_normalise(item, search) for item in browse_search(search, state_path=STATE_PATH, headless=env_bool("PLAYWRIGHT_HEADLESS", True)))
        stats["total_scraped"] = len(all_raw)
        snapshot_dir = SNAPSHOTS_DIR / stats["date"]
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / f"run-{run_id}.json").write_text(json.dumps(all_raw, ensure_ascii=False, indent=2), encoding="utf-8")
        all_ids, delta_ids, new_ids = upsert_jobs(all_raw, run_id)
        stats["new_jobs"] = len(set(new_ids))
        stats["delta_jobs"] = len(set(delta_ids))
        resume_path = Path(resume) if resume else (RESUME_PDF_PATH if RESUME_PDF_PATH.exists() else RESUME_PATH)
        profile = resume_text(resume_path)
        profile_skills = resume_skills(resume_path)
        agent = GeminiApplicationAgent()
        for job_id in dict.fromkeys(delta_ids):
            row = get_job(job_id)
            if not row:
                continue
            job = dict(row)
            tags = classify_job(job)
            # A manual move to the final column survives a refreshed description.
            if row["stage"] == "selected" and not tags["citizenship_or_security_required"]:
                tags["stage"] = "selected"
            update_analysis(job_id, {**tags, "tags_json": json.dumps(tags["tags_json"], ensure_ascii=False)})
            if tags["citizenship_or_security_required"]:
                update_analysis(job_id, {"processed_at": datetime.now(timezone.utc).isoformat()})
                continue
            stats["eligible_jobs"] += 1
            language_score, language_reason = score_language_fit({**job, **tags}, profile)
            skill_score, skill_reason = score_skill_fit({**job, **tags}, profile, profile_skills)
            details: dict[str, Any] = {"language_reason": language_reason, "skill_reason": skill_reason}
            fit_score = None
            if agent.enabled:
                try:
                    evaluation = agent.evaluate(profile, {**job, **tags})
                    fit_score = float(evaluation.get("fit_score", 0))
                    details["gemini"] = evaluation
                except Exception as exc:
                    details["gemini_error"] = str(exc)
                    log.exception("Gemini evaluation failed for job %s", job_id)
            if fit_score is None:
                # Keep the pipeline useful without a key; the current skill hit/embedding matcher can be added later.
                fit_score = 0.0
            average = round((float(language_score) + skill_score + fit_score) / 3, 2)
            stats["scored_jobs"] += 1
            artifact_fields: dict[str, Any] = {}
            if average > 6 and agent.enabled:
                try:
                    generated = agent.generate_application(profile, {**job, **tags}, job_id)
                    artifact_fields = write_application_artifacts(job_id, generated)
                    stats["generated_jobs"] += 1
                except Exception as exc:
                    details["generation_error"] = str(exc)
                    log.exception("Application generation failed for job %s", job_id)
            update_analysis(job_id, {"language_score": language_score, "skill_score": skill_score, "fit_score": fit_score, "average_score": average, "score_details_json": json.dumps(details, ensure_ascii=False), "processed_at": datetime.now(timezone.utc).isoformat(), **artifact_fields})
        finish_run(run_id, total_scraped=stats["total_scraped"], new_jobs=stats["new_jobs"], delta_jobs=stats["delta_jobs"], eligible_jobs=stats["eligible_jobs"], scored_jobs=stats["scored_jobs"], generated_jobs=stats["generated_jobs"])
        if send_report:
            try:
                stats["email_sent"] = send_daily_report(stats)
            except Exception as exc:
                stats["email_error"] = str(exc)
        return stats
    except Exception as exc:
        finish_run(run_id, status="failed", error=str(exc), total_scraped=stats["total_scraped"], delta_jobs=stats["delta_jobs"])
        raise
