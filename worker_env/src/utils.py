# src/utils.py
import os, json, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "stored_data"
RUNS_DIR = DATA_DIR / "runs"



def now_iso():
    #return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    #替换过时方法
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def save_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
    
    
def build_up_json():
    """
    output:[
      {
        "country": "sweden",
        "query": "data engineer",
        "jobs": [ {...}, {...} ]
      },
    """
    all_jobs = []
    for file in DATA_DIR.glob("linkedin_jobs_*.json"):
        parts = file.stem.split("_")
        if len(parts) >= 4:
            country = parts[2]
            query = parts[3]
            jobs = load_json(file)
            all_jobs.append({
                "country": country,
                "query": query,
                "jobs": jobs
            })
    return all_jobs
