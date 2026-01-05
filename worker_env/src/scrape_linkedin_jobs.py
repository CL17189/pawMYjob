import time
import json
import random
from playwright.sync_api import sync_playwright, TimeoutError

STATE_PATH = "worker_env/stored_data/linkedin_state.json"


def scroll_left_panel(page, rounds=5):
    for _ in range(rounds):
        page.mouse.wheel(0, 3000)
        time.sleep(1)

def scrape_jobs_on_page(page):
    """抓取当前页面的所有职位信息"""
    scroll_left_panel(page, rounds=1)
    job_cards = page.query_selector_all("li[data-occludable-job-id]")
    print(f"发现 {len(job_cards)} 个职位")
    jobs = []

    for idx, card in enumerate(job_cards, 1):
        job_id = card.get_attribute("data-occludable-job-id")
        print(f"[{idx}] 抓取 job_id={job_id}")

        card.click()
        try:
            page.wait_for_selector("#job-details", timeout=8000)
            time.sleep(2)
            description = page.inner_text("#job-details")
        except TimeoutError:
            description = page.inner_text("article")

        job_info = {"job_id": job_id}

        # 职位标题
        job_info["title"] = page.inner_text("h1.t-24.t-bold")

        # 公司信息
        company_el = page.query_selector(".job-details-jobs-unified-top-card__company-name a")
        if company_el:
            job_info["company_name"] = company_el.inner_text()
            job_info["company_url"] = company_el.get_attribute("href")

        # 工作类型 & 职位类型
        workplace_type, employment_type = None, None
        for btn in page.query_selector_all(".job-details-fit-level-preferences button"):
            text = btn.inner_text()
            if "办公" in text or "远程" in text:
                workplace_type = text
            if "全职" in text or "兼职" in text:
                employment_type = text
        job_info["workplace_type"] = workplace_type
        job_info["employment_type"] = employment_type
        
        #位置
        meta_el = page.query_selector("div.t-14.truncate")
        meta_text = meta_el.inner_text() if meta_el else None
        job_info["meta"] = meta_text


        # 职位描述
        job_info["description"] = description

        jobs.append(job_info)
        print("完成抓取")
        time.sleep(1+random.random()*2)  # 随机等待，模拟人类行为

    return jobs

def browse(SEARCH_URL,country_key,pos):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=STATE_PATH)
        page = context.new_page()
        page.goto(SEARCH_URL)
        page.wait_for_timeout(5000)

        all_jobs = []

        while True:
            jobs = scrape_jobs_on_page(page)
            all_jobs.extend(jobs)
            print(f"当前总抓取数: {len(all_jobs)}")
            
            if len(all_jobs) >50:
                print("为保证达到抓取上限，结束抓取")
                break

            # 检查“下一页”按钮是否存在且可点击
            next_button = page.query_selector('button[aria-label="查看下一页"]')
            if next_button and next_button.is_enabled():
                print("点击下一页")
                next_button.click()
                time.sleep(3)  # 等待页面加载
            else:
                print("没有下一页了，结束抓取")
                break

        # 保存结果
        with open(f"worker_env/stored_data/linkedin_jobs_{country_key}_{pos}.json", "w", encoding="utf-8") as f:
            json.dump(all_jobs, f, ensure_ascii=False, indent=4)

        browser.close()


def fetch_all_countries_pos(query : str="data engineer"):
    """
    query: str = "data engineer" or "data engineer|software engineer"
    we will parse the query to get multiple positions if '|' is present
    """
    positions = [q.strip() for q in query.split("|")]
    
    #country={"sweden": "105117694","danmark":"104514075","germany":"101282230"}
    country={"sweden": "105117694"}
    

    for country_key in country.keys():
        for pos in positions:
            search_url = (
                "https://www.linkedin.com/jobs/search/"
                f"?keywords={pos.replace(' ', '%20')}"
                f"&geoId={country[country_key]}"
                "&f_TPR=r86400"
            )
            print(f"开始抓取职位: {country_key,"    ",pos}")
            browse(search_url,country_key,pos)
            