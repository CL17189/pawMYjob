"""One-time headed login bootstrap for environments where the user can see a browser."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import STATE_PATH


def save_state_from_cdp(cdp_url: str, target: Path) -> None:
    """Attach to a manually launched regular Chrome and export its current cookies."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise RuntimeError("CDP Chrome has no browser context")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
        input("请确认 Chrome 中已经成功登录 LinkedIn，然后按 Enter 导出登录态...\n")
        context.storage_state(path=str(target))
        print(f"登录态已保存到 {target}")
        # Do not close the manually controlled Chrome process.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--url", default="https://www.linkedin.com/login")
    parser.add_argument("--channel", default=None, help="Use installed Chrome, e.g. --channel chrome")
    parser.add_argument("--cdp-url", default=None, help="Attach to manually launched Chrome, e.g. http://127.0.0.1:9222")
    args = parser.parse_args()
    target = Path(args.state_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if args.cdp_url:
        save_state_from_cdp(args.cdp_url, target)
        return
    with sync_playwright() as playwright:
        launch_options = {"headless": False}
        if args.channel:
            launch_options["channel"] = args.channel
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context()
        page = context.new_page()
        page.goto(args.url)
        print("请在打开的浏览器中完成登录；成功进入 LinkedIn 后回到终端按 Enter。")
        input()
        context.storage_state(path=str(target))
        print(f"登录态已保存到 {target}")
        browser.close()


if __name__ == "__main__":
    main()
