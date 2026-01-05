from playwright.sync_api import sync_playwright

STATE_PATH = "../stored_data/linkedin_state.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://www.linkedin.com/login")

    print("请手动完成 LinkedIn 登录，然后不要关闭浏览器")
    input("登录完成后，按 Enter 保存登录状态...")

    context.storage_state(path=STATE_PATH)
    print(f"登录态已保存到 {STATE_PATH}")

    browser.close()
