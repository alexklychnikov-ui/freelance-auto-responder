from __future__ import annotations

import json
import sys

from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

PROJECT_ID = sys.argv[1] if len(sys.argv) > 1 else "3204847"


def main() -> None:
    settings = get_settings()
    browser = get_browser_client(settings)
    try:
        browser.navigate(f"https://kwork.ru/new_offer?project={PROJECT_ID}")
        browser.wait_ms(5000)
        click = browser.evaluate(
            """() => {
              const root = document.querySelector('.duration-select');
              const toggle = root?.querySelector('.vs__dropdown-toggle');
              if (toggle) { toggle.click(); return true; }
              return false;
            }"""
        )
        browser.wait_ms(3000)
        info = browser.evaluate(
            """() => ({
              url: location.href,
              durationSelect: Boolean(document.querySelector('.duration-select')),
              multiselect: document.querySelectorAll('.multiselect').length,
              vsSearch: [...document.querySelectorAll('input.vs__search')].map((i) => ({
                ph: i.placeholder,
                cls: (i.className || '').slice(0, 40),
                parent: (i.closest('[class]')?.className || '').slice(0, 80),
              })),
              durationHtml: (document.querySelector('.duration-select')?.outerHTML || '').slice(0, 1200),
              labels: [...document.querySelectorAll('label,span,div')]
                .map((e) => (e.textContent || '').replace(/\\s+/g, ' ').trim())
                .filter((t) => /срок/i.test(t))
                .slice(0, 8),
              selects: [...document.querySelectorAll('select')].map((s) => ({
                cls: (s.className || '').slice(0, 50),
                opts: [...s.options].slice(0, 12).map((o) => o.textContent.trim()),
              })),
              options: [...document.querySelectorAll('.duration-select__dropdown .vs__dropdown-option, .duration-select .vs__dropdown-option, [id^=vs3__listbox] .vs__dropdown-option')]
                .map((o) => (o.textContent || '').replace(/\\s+/g, ' ').trim())
                .filter(Boolean)
                .slice(0, 20),
              selectedDuration: (document.querySelector('.duration-select__selected-option')?.value || '').trim(),
              durationSearch: (document.querySelector('.duration-select input.vs__search')?.value || '').trim(),
              descTa: (document.querySelector('textarea[name="description"]')?.value || '').slice(0, 120),
            })"""
        )
        if isinstance(info, dict):
            info["clicked"] = bool(click)
        vue = browser.evaluate(
            """() => {
              const root = document.querySelector('.duration-select');
              const vm = root?.__vue__;
              const offer = document.querySelector('.offer-custom')?.__vue__;
              return {
                vmKeys: vm ? Object.keys(vm.$data || {}).slice(0, 20) : [],
                vmData: vm ? JSON.stringify(vm.$data).slice(0, 400) : '',
                offerKeys: offer ? Object.keys(offer.$data || {}).slice(0, 30) : [],
                offerDuration: offer?.duration ?? offer?.durationDays ?? offer?.selectedDuration ?? null,
              };
            }"""
        )
        if isinstance(info, dict) and isinstance(vue, dict):
            info["vue"] = vue
        print(json.dumps(info, ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
