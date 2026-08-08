import json
import csv
import re
from pathlib import Path

# ========= 基础配置 =========
DATA_DIR = Path("worker_env/stored_data")   # 放 json 的目录
OUTPUT_CSV = "linkedin_jobs_2026-01-17.csv"

ROLES = [
    "data engineer",
    "data analyst",
    "full stack developer"
]

CRAWL_DATE = "2026-01-17"
COUNTRY = "sweden"

# ========= 关键词库（可持续扩展） =========
HARD_SKILLS = [
    "python", "sql", "java", "c#", "scala", "r",
    "spark", "hadoop", "airflow",
    "aws", "azure", "gcp",
    "docker", "kubernetes",
    "bi", "power bi", "tableau",
    "machine learning", "ai"
]

SOFT_SKILLS = [
    "kommunikation", "communication",
    "team", "collaboration",
    "agile", "flexible",
    "initiative", "self-driven"
]

EDUCATION_PATTERNS = [
    r"akademisk utbildning",
    r"bachelor",
    r"master",
    r"högskolepoäng",
    r"universitet"
]

EXPERIENCE_PATTERNS = [
    r"erfarenhet",
    r"\d+\s*år",
    r"current experience",
]

LANGUAGE_PATTERNS = [
    r"svenska",
    r"english",
    r"engelska"
]

CITIZENSHIP_PATTERNS = [
    r"svenskt medborgarskap",
    r"citizenship"
]

PUBLIC_SECTOR_PATTERNS = [
    r"myndighet",
    r"offentlig"
]

CITY_PATTERNS = [
    "stockholm", "göteborg", "malmö","uppsala", "västerås",
    "örebro", "linköping", "helsingborg", "jönköping",
    "norrköping", "lund", "umeå", "gävle", "borås",
    "södertälje", "karlstad", "täby", "luleå", "halmstad"
]

# ========= 工具函数 =========
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()

def extract_keywords(text, keywords):
    text = normalize(text)
    return sorted({kw for kw in keywords if kw in text})

def match_patterns(text, patterns):
    text = normalize(text)
    return any(re.search(p, text) for p in patterns)

def extract_cities(text):
    text = normalize(text)
    return sorted({c.title() for c in CITY_PATTERNS if c in text})

def safe_str(value):
    return value.strip() if isinstance(value, str) else ""

# ========= 主处理逻辑 =========
rows = []

for role in ROLES:
    file_pattern = f"linkedin_jobs_sweden_{role}_{CRAWL_DATE}.json"
    file_path = DATA_DIR / file_pattern

    if not file_path.exists():
        print(f"Missing file: {file_path}")
        continue

    with open(file_path, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    for job in jobs:
        desc = job.get("description", "")

        row = {
            "job_id": job.get("job_id"),
            "title": job.get("title"),
            "role_category": role,
            "company_name": job.get("company_name"),
            "company_url": job.get("company_url"),
            "employment_type": safe_str(job.get("employment_type")),
            "workplace_type": safe_str(job.get("workplace_type")),
            "meta": safe_str(job.get("meta")),
            "country": COUNTRY,
            "city": ", ".join(extract_cities(desc)),
            "is_public_sector": match_patterns(desc, PUBLIC_SECTOR_PATTERNS),
            "language_requirement": extract_keywords(desc, ["svenska", "english", "engelska"]),
            "citizenship_requirement": match_patterns(desc, CITIZENSHIP_PATTERNS),
            "education_requirement": match_patterns(desc, EDUCATION_PATTERNS),
            "experience_requirement": match_patterns(desc, EXPERIENCE_PATTERNS),
            "hard_skills": ", ".join(extract_keywords(desc, HARD_SKILLS)),
            "soft_skills": ", ".join(extract_keywords(desc, SOFT_SKILLS)),
            "tech_stack": ", ".join(extract_keywords(desc, HARD_SKILLS)),
            "description_raw": desc,
            "source_file": file_pattern,
            "crawl_date": CRAWL_DATE
        }

        rows.append(row)

# ========= 写 CSV =========
if rows:
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

print(f"Saved {len(rows)} rows to {OUTPUT_CSV}")
