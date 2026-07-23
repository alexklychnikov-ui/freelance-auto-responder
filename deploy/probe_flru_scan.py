"""Probe FL.ru session and listing on VPS."""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters.flru import LISTING_EXTRACTOR_JS, parse_listing_from_html
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings


def main() -> None:
    settings = get_settings()
    url = "https://www.fl.ru/projects/?kind=1"
    storage = settings.flru_storage_state
    print("storage:", storage, "exists:", os.path.exists(storage or ""))

    browser = get_browser_client(settings, storage_state_path=storage)
    try:
        browser.navigate(url)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(3500)
        cur = browser.evaluate("() => location.href")
        title = browser.evaluate("() => document.title")
        print("url:", cur)
        print("title:", title)
        is_login = "login" in (cur or "").lower()
        print("is_login:", is_login)
        snap = browser.snapshot() or ""
        print("html_len:", len(snap))
        js = browser.evaluate(LISTING_EXTRACTOR_JS)
        print("js_cards:", len(js) if isinstance(js, list) else js)
        fb = parse_listing_from_html(snap)
        print("fallback_cards:", len(fb))
        if fb[:2]:
            for c in fb[:2]:
                print("sample:", c.get("project_id"), (c.get("title") or "")[:60])
        low = snap.lower()
        for kw in ("войти", "login", "smartcaptcha", "мои отклики", "чаты"):
            if kw in low:
                print("keyword:", kw)
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
