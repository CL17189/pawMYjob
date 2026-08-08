"""Central configuration for the local-first job pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _path_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT_DIR / path


DATA_DIR = _path_env("DATA_DIR", ROOT_DIR / "worker_env" / "stored_data")
DB_PATH = _path_env("DB_PATH", DATA_DIR / "pawmyjob.sqlite3")
STATE_PATH = _path_env("PLAYWRIGHT_STATE_PATH", DATA_DIR / "linkedin_state.json")
RESUME_PATH = _path_env("RESUME_PATH", DATA_DIR / "resume.md")
RESUME_PDF_PATH = _path_env("RESUME_PDF_PATH", DATA_DIR / "resume.pdf")
ARTIFACTS_DIR = _path_env("ARTIFACTS_DIR", DATA_DIR / "artifacts")
SNAPSHOTS_DIR = _path_env("SNAPSHOTS_DIR", DATA_DIR / "snapshots")
SEARCH_CONFIG_PATH = _path_env("SEARCH_CONFIG_PATH", ROOT_DIR / "config" / "searches.json")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def load_searches(path: Path | None = None) -> list[dict[str, Any]]:
    """Load user-defined country/role searches. The file is intentionally editable."""
    path = path or SEARCH_CONFIG_PATH
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("searches", [])
    if not isinstance(payload, list):
        raise ValueError("Search configuration must be a JSON list or {\"searches\": [...]}")
    return [item for item in payload if isinstance(item, dict) and item.get("query")]


def save_searches(searches: list[dict[str, Any]], path: Path | None = None) -> None:
    path = path or SEARCH_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(searches, ensure_ascii=False, indent=2), encoding="utf-8")

