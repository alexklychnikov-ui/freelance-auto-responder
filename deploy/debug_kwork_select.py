"""Inspect and pick kwork select on offer form."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

KWORK_SEL = 'input.vs__search[placeholder="Выберите кворк"]'
DEADLINE_SEL = 'input.vs__search[placeholder="Срок выполнения"]'


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        page = browser._ensure_page()
        has_deadline_text = page.evaluate(
            """() => (document.body.innerText || '').includes('Срок выполнения')"""
        )
        print("has_deadline_text", has_deadline_text)
        print("placeholders", page.evaluate(
            """() => [...document.querySelectorAll('input.vs__search')]
              .map(el => el.getAttribute('placeholder') || '')"""
        ))
        kwork = page.locator(KWORK_SEL).first
        print("kwork count", kwork.count())
        if kwork.count():
            kwork.scroll_into_view_if_needed()
            page.locator("#vs1__combobox, .vs__dropdown-toggle").first.click(force=True, timeout=5000)
            page.wait_for_timeout(800)
            opts = page.evaluate(
                """() => [...document.querySelectorAll('.vs__dropdown-option')]
                  .map(el => (el.textContent||'').replace(/\\s+/g,' ').trim())"""
            )
            print("kwork options", json.dumps(opts[:20], ensure_ascii=False))
            if opts:
                page.locator(".vs__dropdown-option").first.click()
                page.wait_for_timeout(1000)
        print("placeholders after kwork", page.evaluate(
            """() => [...document.querySelectorAll('input.vs__search')]
              .map(el => el.getAttribute('placeholder') || '')"""
        ))
        dl = page.locator(DEADLINE_SEL)
        print("deadline count", dl.count())
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
