"""Small SQLite persistence layer for jobs, snapshots, scores and application status."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .config import DB_PATH


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  total_scraped INTEGER DEFAULT 0,
  new_jobs INTEGER DEFAULT 0,
  delta_jobs INTEGER DEFAULT 0,
  eligible_jobs INTEGER DEFAULT 0,
  scored_jobs INTEGER DEFAULT 0,
  generated_jobs INTEGER DEFAULT 0,
  searches_json TEXT NOT NULL DEFAULT '[]',
  error TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  natural_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL DEFAULT 'linkedin',
  source_job_id TEXT,
  url TEXT,
  title TEXT,
  company_name TEXT,
  company_url TEXT,
  country TEXT,
  location TEXT,
  query TEXT,
  workplace_type TEXT,
  employment_type TEXT,
  description TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  first_seen_run INTEGER,
  last_seen_run INTEGER,
  description_hash TEXT,
  language_required INTEGER NOT NULL DEFAULT 0,
  language_optional INTEGER NOT NULL DEFAULT 0,
  citizenship_or_security_required INTEGER NOT NULL DEFAULT 0,
  senior INTEGER NOT NULL DEFAULT 0,
  tags_json TEXT NOT NULL DEFAULT '[]',
  screening_reason TEXT,
  stage TEXT NOT NULL DEFAULT 'all',
  language_score REAL,
  skill_score REAL,
  fit_score REAL,
  average_score REAL,
  score_details_json TEXT,
  resume_tex_path TEXT,
  resume_pdf_path TEXT,
  cover_letter_tex_path TEXT,
  processed_at TEXT,
  application_status TEXT NOT NULL DEFAULT 'new',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_stage ON jobs(stage);
CREATE INDEX IF NOT EXISTS idx_jobs_country_query ON jobs(country, query);
CREATE TABLE IF NOT EXISTS observations (
  run_id INTEGER NOT NULL,
  job_id INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  PRIMARY KEY (run_id, job_id),
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "skill_score" not in existing:
            conn.execute("ALTER TABLE jobs ADD COLUMN skill_score REAL")


def create_run(searches: list[dict[str, Any]]) -> int:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs(started_at,status,searches_json) VALUES(?,?,?)",
            (utc_now(), "started", json.dumps(searches, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str = "finished", **counts: Any) -> None:
    allowed = {"total_scraped", "new_jobs", "delta_jobs", "eligible_jobs", "scored_jobs", "generated_jobs", "error"}
    values = {key: counts.get(key, 0) for key in allowed if key != "error"}
    values["error"] = counts.get("error")
    assignments = ", ".join(f"{key}=?" for key in [*values.keys(), "finished_at", "status"])
    params = [*values.values(), utc_now(), status, run_id]
    with connect() as conn:
        conn.execute(f"UPDATE runs SET {assignments} WHERE id=?", params)


def _natural_key(job: dict[str, Any]) -> str:
    source_id = str(job.get("job_id") or job.get("source_job_id") or "").strip()
    if source_id:
        return f"linkedin:{source_id}"
    url = str(job.get("url") or "").strip()
    if url:
        return "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()
    fallback = "|".join(str(job.get(k) or "") for k in ("country", "query", "title", "company_name", "description"))
    return "hash:" + hashlib.sha1(fallback.encode("utf-8")).hexdigest()


def upsert_jobs(jobs: Iterable[dict[str, Any]], run_id: int) -> tuple[list[int], list[int], list[int]]:
    """Return (all ids observed, delta ids, genuinely new ids)."""
    init_db()
    now = utc_now()
    all_ids: list[int] = []
    delta_ids: list[int] = []
    new_ids: list[int] = []
    with connect() as conn:
        for job in jobs:
            key = _natural_key(job)
            description = str(job.get("description") or job.get("text") or job.get("raw") or "")
            description_hash = hashlib.sha1(description.encode("utf-8")).hexdigest()
            existing = conn.execute("SELECT * FROM jobs WHERE natural_key=?", (key,)).fetchone()
            fields = {
                "source": job.get("source", "linkedin"), "source_job_id": job.get("job_id"),
                "url": job.get("url") or (f"https://www.linkedin.com/jobs/view/{job.get('job_id')}" if job.get("job_id") else None),
                "title": job.get("title") or job.get("job_title"), "company_name": job.get("company_name"),
                "company_url": job.get("company_url"), "country": job.get("country"), "location": job.get("location") or job.get("meta"),
                "query": job.get("query"), "workplace_type": job.get("workplace_type"), "employment_type": job.get("employment_type"),
                "description": description, "raw_json": json.dumps(job, ensure_ascii=False), "last_seen": now,
                "last_seen_run": run_id, "description_hash": description_hash, "updated_at": now,
            }
            if existing is None:
                columns = ["natural_key", *fields.keys(), "first_seen", "first_seen_run", "created_at"]
                values = [key, *fields.values(), now, run_id, now]
                cur = conn.execute(f"INSERT INTO jobs({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
                job_id = int(cur.lastrowid)
                delta_ids.append(job_id)
                new_ids.append(job_id)
            else:
                job_id = int(existing["id"])
                changed = existing["description_hash"] != description_hash or not existing["processed_at"]
                conn.execute(
                    "UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) + (", processed_at=NULL, score_details_json=NULL, language_score=NULL, skill_score=NULL, fit_score=NULL, average_score=NULL, resume_tex_path=NULL, resume_pdf_path=NULL, cover_letter_tex_path=NULL" if changed else "") + " WHERE id=?",
                    [*fields.values(), job_id],
                )
                if changed:
                    delta_ids.append(job_id)
            all_ids.append(job_id)
            conn.execute("INSERT OR REPLACE INTO observations(run_id,job_id,observed_at) VALUES(?,?,?)", (run_id, job_id, now))
    return all_ids, delta_ids, new_ids


def get_job(job_id: int) -> sqlite3.Row | None:
    init_db()
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def update_analysis(job_id: int, analysis: dict[str, Any]) -> None:
    columns = {
        "language_required", "language_optional", "citizenship_or_security_required", "senior",
        "tags_json", "screening_reason", "stage", "language_score", "skill_score", "fit_score", "average_score",
        "score_details_json", "resume_tex_path", "resume_pdf_path", "cover_letter_tex_path", "processed_at",
    }
    updates = {k: v for k, v in analysis.items() if k in columns}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    with connect() as conn:
        conn.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=?", [*updates.values(), job_id])


def update_stage(job_id: int, stage: str) -> None:
    if stage not in {"all", "review", "selected"}:
        raise ValueError("stage must be all, review or selected")
    with connect() as conn:
        conn.execute("UPDATE jobs SET stage=?,updated_at=? WHERE id=?", (stage, utc_now(), job_id))


def update_application_status(job_id: int, status: str) -> None:
    allowed = {"new", "applied", "rejected", "interview_rejected", "offer"}
    if status not in allowed:
        raise ValueError(f"status must be one of {sorted(allowed)}")
    with connect() as conn:
        conn.execute("UPDATE jobs SET application_status=?,updated_at=? WHERE id=?", (status, utc_now(), job_id))


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("raw_json", "tags_json", "score_details_json"):
        raw = item.pop(key, None)
        try:
            item[key.removesuffix("_json")] = json.loads(raw) if raw else ({} if key == "raw_json" else [])
        except json.JSONDecodeError:
            item[key.removesuffix("_json")] = raw
    return item


def list_jobs(stage: str = "all", date_range: str = "all", country: str = "", query: str = "") -> list[dict[str, Any]]:
    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if stage != "all":
        clauses.append("stage=?"); params.append(stage)
    if date_range in {"today", "7days"}:
        zone = ZoneInfo(os.getenv("TZ", "UTC"))
        local_now = datetime.now(zone)
        # Match LinkedIn's r86400 semantics: a rolling 24-hour window, not local midnight.
        local_start = local_now - timedelta(hours=24) if date_range == "today" else local_now - timedelta(days=7)
        clauses.append("first_seen >= ?")
        params.append(local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    if country:
        clauses.append("lower(country)=lower(?)"); params.append(country)
    if query:
        clauses.append("lower(query)=lower(?)"); params.append(query)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM jobs" + where + " ORDER BY first_seen DESC, id DESC", params).fetchall()
    return [_row_to_job(row) for row in rows]


def latest_runs(limit: int = 10) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def stats() -> dict[str, int]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) total, SUM(stage='review') review, SUM(stage='selected') selected, SUM(application_status='applied') applied FROM jobs").fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}
