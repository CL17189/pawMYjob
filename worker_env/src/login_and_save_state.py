"""One-time headed login bootstrap for environments where the user can see a browser."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import STATE_PATH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--url", default="https://www.linkedin.com/login")
    args = parser.parse_args()
    target = Path(args.state_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
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
