# src/utils.py
import os, json, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "stored_data"
RUNS_DIR = DATA_DIR / "runs"
JDS_DIR = DATA_DIR / "jds"

for p in (DATA_DIR, RUNS_DIR, JDS_DIR):
    p.mkdir(parents=True, exist_ok=True)

def now_iso():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def save_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
