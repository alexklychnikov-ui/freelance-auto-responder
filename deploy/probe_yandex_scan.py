"""One-off probe: what Yandex cab/orders returns on VPS."""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters.yandex_uslugi import LISTING_EXTRACTOR_JS, parse_listing_from_html
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings


def main() -> None:
    settings = get_settings()
    url = (
        "https://uslugi.yandex.ru/cab/orders?"
        "completely_moderated=1&rubric=4479&rubric=4772&rubric=4781&rubric=4789&type=new"
    )
    storage = settings.yandex_storage_state
    print("storage:", storage, "exists:", os.path.exists(storage or ""))

    browser = get_browser_client(settings, storage_state_path=storage)
    try:
        browser.navigate(url)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(4000)
        cur = browser.evaluate("() => location.href")
        title = browser.evaluate("() => document.title")
        print("url:", cur)
        print("title:", title)
        snap = browser.snapshot() or ""
        print("html_len:", len(snap))
        uuids = re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            snap,
            re.I,
        )
        print("uuid_in_html:", len(uuids))
        order_links = re.findall(
            r'href=["\']([^"\']*/order/[^"\']+)["\']',
            snap,
            re.I,
        )
        print("order_hrefs:", len(order_links))
        if order_links[:5]:
            print("sample:", order_links[:5])
        low = snap.lower()
        for kw in (
            "нет заказ",
            "ничего не найден",
            "пуст",
            "заказов пока нет",
            "войдите",
            "passport",
            "исполнител",
        ):
            if kw in low:
                print("keyword_found:", kw)
        js = browser.evaluate(LISTING_EXTRACTOR_JS)
        print("js_cards:", len(js) if isinstance(js, list) else js)
        fb = parse_listing_from_html(snap)
        print("fallback_cards:", len(fb))
        if fb[:2]:
            for c in fb[:2]:
                print("card:", c.get("project_id"), c.get("title", "")[:80])
        # try without rubric filters
        plain = "https://uslugi.yandex.ru/cab/orders?type=new"
        browser.navigate(plain)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(4000)
        snap2 = browser.snapshot() or ""
        fb2 = parse_listing_from_html(snap2)
        print("plain_url_cards:", len(fb2), "html_len:", len(snap2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
