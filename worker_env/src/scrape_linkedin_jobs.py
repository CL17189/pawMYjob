"""Playwright LinkedIn scraper with headed-login bootstrap and headless server mode."""

from __future__ import annotations

import datetime as dt
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from playwright.sync_api import TimeoutError, sync_playwright

from .config import DATA_DIR, STATE_PATH


def _text(page, selector: str) -> str | None:
    element = page.query_selector(selector)
    return element.inner_text().strip() if element else None


def scroll_left_panel(page, rounds: int = 2) -> None:
    for _ in range(rounds):
        page.mouse.wheel(0, 2600)
        time.sleep(0.8)


def scrape_jobs_on_page(page, max_jobs: int = 100) -> list[dict[str, Any]]:
    scroll_left_panel(page)
    cards = page.query_selector_all("li[data-occludable-job-id]")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards[:max_jobs]:
        job_id = card.get_attribute("data-occludable-job-id")
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        try:
            card.click(timeout=5000)
            page.wait_for_timeout(800)
        except Exception:
            continue
        try:
            page.wait_for_selector("#job-details, article", timeout=8000)
        except TimeoutError:
            pass
        description = _text(page, "#job-details") or _text(page, "article") or ""
        company_el = page.query_selector(".job-details-jobs-unified-top-card__company-name a")
        workplace_type = None
        employment_type = None
        for button in page.query_selector_all(".job-details-fit-level-preferences button"):
            value = button.inner_text().strip()
            if any(token in value.lower() for token in ("remote", "hybrid", "on-site", "på plats", "distans")):
                workplace_type = value
            if any(token in value.lower() for token in ("full-time", "part-time", "heltid", "deltid", "contract")):
                employment_type = value
        jobs.append({
            "job_id": job_id,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}",
            "title": _text(page, "h1.t-24.t-bold") or _text(page, "h1") or "",
            "company_name": company_el.inner_text().strip() if company_el else None,
            "company_url": company_el.get_attribute("href") if company_el else None,
            "workplace_type": workplace_type,
            "employment_type": employment_type,
            "meta": _text(page, "div.t-14.truncate") or _text(page, ".job-details-jobs-unified-top-card__primary-description-container"),
            "description": description,
            "scraped_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        time.sleep(1.0 + random.random() * 2.0)
    return jobs


def search_url(search: dict[str, Any]) -> str:
    query = quote_plus(str(search.get("query", "data engineer")))
    url = f"https://www.linkedin.com/jobs/search/?keywords={query}"
    if search.get("geo_id"):
        url += f"&geoId={quote_plus(str(search['geo_id']))}"
    window = search.get("posted_window", search.get("posted_windows", "7days"))
    tpr = {
        "today": "r86400", "24h": "r86400", "1day": "r86400",
        "7days": "r604800", "7d": "r604800", "week": "r604800",
    }.get(str(window).strip().lower())
    if not tpr:
        raise ValueError(f"Unsupported posted_window={window!r}; use today or 7days")
    url += f"&f_TPR={tpr}"
    return url


def browse_search(search: dict[str, Any], state_path: Path = STATE_PATH, headless: bool = True, max_pages: int = 10) -> list[dict[str, Any]]:
    """Scrape one configured search and return raw-ish job records; no DB side effects."""
    if not state_path.exists():
        raise FileNotFoundError(f"Playwright state not found: {state_path}. Run login_and_save_state.py first.")
    all_jobs: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        page.goto(search_url(search), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        for _ in range(max_pages):
            before = len(all_jobs)
            all_jobs.extend(scrape_jobs_on_page(page))
            if len(all_jobs) >= 100:
                break
            next_button = page.query_selector('button[aria-label*="next" i], button[aria-label*="下一页"]')
            if not next_button or not next_button.is_enabled():
                break
            try:
                next_button.click()
                page.wait_for_timeout(3000)
            except Exception:
                break
            if len(all_jobs) == before:
                break
        context.close()
        browser.close()
    # Keep the legacy raw snapshot behavior as well as the new centralized snapshot.
    unique: dict[str, dict[str, Any]] = {}
    for item in all_jobs:
        unique[str(item.get("job_id") or item.get("url") or len(unique))] = item
    all_jobs = list(unique.values())
    country = str(search.get("country", "unknown")).replace("/", "-")
    query = str(search.get("query", "job")).replace("/", "-").replace(" ", "_")
    path = DATA_DIR / f"linkedin_jobs_{country}_{query}_{dt.date.today().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    path.write_text(json.dumps(all_jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    return all_jobs


def fetch_all_countries_pos(query: str = "data engineer") -> None:
    """Legacy wrapper retained for callers from the original project."""
    from .config import load_searches
    searches = [s for s in load_searches() if s.get("query") in query.split("|")]
    for search in searches:
        browse_search(search, state_path=STATE_PATH, headless=True)
