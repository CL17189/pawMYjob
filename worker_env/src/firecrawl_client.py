# worker_env/src/firecrawl_client.py
import os
import time
import json
import requests
from typing import List, Dict, Optional, Any
from pathlib import Path
from .utils import save_json, JDS_DIR
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]  # firecrawl_client.py -> src -> worker_env -> pawMYjob
dotenv_path = ROOT_DIR / ".env"

load_dotenv(dotenv_path)  # 读取 .env 文件到 os.environ
FIRECRAWL_KEY = os.getenv("FIRECRAWL_API_KEY")

BASE = "https://api.firecrawl.dev/v1"  # Firecrawl v1 base URL
DEFAULT_TIMEOUT_MS = 60000

# simple mapping for user convenience (slug -> example geoId if you have one)
EU_COUNTRIES = {
    "sweden": {"label": "Sweden"},
    "denmark": {"label": "Denmark"},
    "norway": {"label": "Norway"},
    "finland": {"label": "Finland"},
    "germany": {"label": "Germany"},
}


def _headers():
    if not FIRECRAWL_KEY:
        raise RuntimeError("FIRECRAWL_API_KEY is not set in environment.")
    return {"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"}


def _backoff_sleep(attempt: int):
    # exponential backoff with jitter
    base = min(60, (2 ** attempt))
    jitter = base * 0.2
    sleep_for = base + (jitter * (0.5 - os.urandom(1)[0] / 255.0))
    time.sleep(sleep_for)


def scrape_url(url: str, output_format: str = "markdown", timeout_ms: int = DEFAULT_TIMEOUT_MS,
               mobile: bool = False, screenshot: bool = False, max_retries: int = 4) -> Dict[str, Any]:
    """
    Call Firecrawl /scrape endpoint to convert a single URL into LLM-ready content.
    Returns parsed JSON response (typically contains markdown, html, links, screenshot metadata etc).
    Handles 429 with exponential backoff.
    """
    endpoint = f"{BASE}/scrape"
    payload = {
        "url": url,
        "options": {
            "format": output_format,  # 'markdown' | 'html' | 'raw' etc.
            "timeout": timeout_ms,
            "mobile": mobile,
            "screenshot": screenshot,
        }
    }
    headers = _headers()
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=(timeout_ms/1000.0) + 10)
            if resp.status_code == 429:
                if attempt > max_retries:
                    return {"error": "rate_limited", "status_code": 429, "body": resp.text}
                _backoff_sleep(attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            # for 5xx maybe retry a few times
            status = getattr(e.response, "status_code", None)
            if status and 500 <= status < 600 and attempt <= max_retries:
                _backoff_sleep(attempt)
                continue
            return {"error": f"http_error: {str(e)}", "status_code": status, "body": getattr(e.response, "text", None)}
        except Exception as e:
            if attempt <= max_retries:
                _backoff_sleep(attempt)
                continue
            return {"error": f"exception: {str(e)}"}


def search_firecrawl(query: str, country: Optional[str] = None, limit: int = 50,
                     scrape_each_result: bool = False, output_format: str = "markdown") -> List[Dict]:
    """
    Call Firecrawl /search endpoint to get search results (and optionally scrape content for each result).
    Returns list of job dicts: {url, title, snippet, raw (if scraped), fetched_at}
    """
    endpoint = f"{BASE}/search"
    headers = _headers()
    payload = {"q": query, "limit": limit}
    if country:
        payload["country"] = country
    # attempt search
    try:
        r = requests.post(endpoint, headers=headers, json=payload, timeout=30)
        if r.status_code == 429:
            # give caller chance to retry (here we simply return empty list)
            return []
        r.raise_for_status()
        res = r.json()
    except Exception as e:
        return [{"error": f"search_failed: {str(e)}"}]

    items = res.get("items") or []
    out: List[Dict] = []
    for item in items:
        url = item.get("url")
        title = item.get("title") or item.get("headline") or item.get("name")
        snippet = item.get("snippet") or item.get("excerpt")
        job = {"url": url, "title": title, "snippet": snippet, "fetched_at": time.strftime("%Y%m%dT%H%M%SZ")}
        if scrape_each_result and url:
            scraped = scrape_url(url, output_format=output_format)
            job["raw"] = scraped
        out.append(job)
    return out


def build_linkedin_search_url(keywords: str, geo_id: Optional[str] = None, origin: str = "JOBS_HOME_LOCATION_HISTORY",
                              currentJobId: Optional[str] = None) -> str:
    """
    Construct a LinkedIn jobs search URL with given keywords and optional geoId.
    If geo_id is None, this will omit the geoId param.
    """
    from urllib.parse import quote_plus
    q = quote_plus(keywords)
    base = f"https://www.linkedin.com/jobs/search/?keywords={q}&origin={origin}"
    if geo_id:
        base += f"&geoId={quote_plus(geo_id)}"
    if currentJobId:
        base += f"&currentJobId={quote_plus(currentJobId)}"
    return base


def scrape_linkedin_search(country_slug: str, keywords: str = "data engineer", linkedin_geo_id: Optional[str] = None,
                           output_format: str = "markdown") -> List[Dict]:
    """
    Build LinkedIn search URL (or multiple variants) and ask Firecrawl to scrape the page.
    Returns a list with a single job-listing-page result (Firecrawl returns page markdown plus subpages).
    """
    url = build_linkedin_search_url(keywords, geo_id=linkedin_geo_id)
    scraped = scrape_url(url, output_format=output_format)
    # Firecrawl may return a 'pages' array or 'markdown' - keep raw response under 'raw'
    # We will try to parse a best-effort list of individual job links if available in the returned 'links'
    result = {
        "url": url,
        "title": f"LinkedIn search: {keywords} ({country_slug})",
        "country": country_slug,
        "raw": scraped,
        "fetched_at": time.strftime("%Y%m%dT%H%M%SZ")
    }
    return [result]


def search_jobsite_for_country(country_slug: str, query: str = "data engineer",
                               posted_after: Optional[str] = None,
                               linkedin_geo_id: Optional[str] = None,
                               prefer_search_endpoint: bool = True,
                               scrape_each_result: bool = True) -> List[Dict]:
    """
    High-level function to get job postings for a given country.
    Strategy:
      1) If prefer_search_endpoint -> try Firecrawl /search with query+country.
      2) If search returns no useful items, fall back to scraping LinkedIn search page for the keywords.
      3) For each found item, optionally run /scrape to get full markdown/text.
    Returns a list of job dicts: {url, title, snippet, raw, country, fetched_at}
    """
    jobs: List[Dict] = []

    # 1) try search endpoint
    if prefer_search_endpoint:
        try:
            results = search_firecrawl(query=query, country=country_slug, limit=100, scrape_each_result=scrape_each_result)
            if results and any("url" in r for r in results):
                # annotate and save
                for r in results:
                    r.setdefault("country", country_slug)
                jobs.extend(results)
        except Exception as e:
            jobs.append({"error": f"search_exception: {str(e)}", "country": country_slug})

    # 2) fallback to scraping LinkedIn search URL if no results or caller prefers
    if not jobs:
        try:
            linkedin_results = scrape_linkedin_search(country_slug=country_slug, keywords=query, linkedin_geo_id=linkedin_geo_id)
            jobs.extend(linkedin_results)
        except Exception as e:
            jobs.append({"error": f"linkedin_scrape_failed: {str(e)}", "country": country_slug})

    # persist run dump
    run_stamp = time.strftime("%Y%m%dT%H%M%SZ")
    out_path = JDS_DIR / f"{country_slug}_{run_stamp}.json"
    try:
        save_json(jobs, out_path)
    except Exception:
        # best-effort fallback to writing raw json
        Path(JDS_DIR).mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

    return jobs


def fetch_all_countries(query: str = "data engineer", linkedin_geo_map: Optional[Dict[str, str]] = None) -> Dict[str, List[Dict]]:
    """
    Convenience wrapper to fetch jobs for all EU_COUNTRIES (or a subset).
    linkedin_geo_map: optional dict mapping country_slug -> linkedin geoId (string)
    """
    print(ROOT_DIR)
    all_jobs: Dict[str, List[Dict]] = {}
    for c in EU_COUNTRIES.keys():
        geo = None
        if linkedin_geo_map and c in linkedin_geo_map:
            geo = linkedin_geo_map[c]
        try:
            all_jobs[c] = search_jobsite_for_country(country_slug=c, query=query, linkedin_geo_id=geo)
        except Exception as e:
            all_jobs[c] = [{"error": str(e)}]
    return all_jobs
