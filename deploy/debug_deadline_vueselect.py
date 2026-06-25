"""Debug vue-select deadline on new_offer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

SEL = 'input.vs__search[placeholder="Срок выполнения"]'


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    days = sys.argv[2] if len(sys.argv) > 2 else "10"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        page = browser._ensure_page()
        placeholders = page.evaluate(
            """() => [...document.querySelectorAll('input.vs__search')]
              .map(el => el.getAttribute('placeholder') || '')"""
        )
        print("placeholders", placeholders)
        loc = page.locator(SEL).first
        print("visible", loc.count())
        loc.click()
        page.wait_for_timeout(500)
        opts_before = page.evaluate(
            """() => [...document.querySelectorAll('.vs__dropdown-option')]
              .map(el => (el.textContent||'').trim())"""
        )
        print("opts_before", opts_before)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        loc.press_sequentially(days, delay=80)
        page.wait_for_timeout(1000)
        opts_after = page.evaluate(
            """() => ({
              options: [...document.querySelectorAll('.vs__dropdown-option')]
                .map(el => (el.textContent||'').trim()),
              active: [...document.querySelectorAll('.vs__dropdown-option--highlight')]
                .map(el => (el.textContent||'').trim()),
              html: document.querySelector('.vs__dropdown-menu')?.innerHTML?.slice(0,500),
            })"""
        )
        print("opts_after", json.dumps(opts_after, ensure_ascii=False, indent=2))
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        read = page.evaluate(
            """() => ({
              input: document.querySelector('input.vs__search[placeholder="Срок выполнения"]')?.value,
              selected: document.querySelector('.vs__selected-options')?.textContent,
              allVs: [...document.querySelectorAll('.v-select, .vs--single')].map(el => el.textContent?.slice(0,80)),
            })"""
        )
        print("read", json.dumps(read, ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
