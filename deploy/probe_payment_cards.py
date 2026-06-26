from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

PROJECT_ID = sys.argv[1] if len(sys.argv) > 1 else "3205065"


def main() -> None:
    settings = get_settings()
    browser = get_browser_client(settings)
    try:
        browser.navigate(f"https://kwork.ru/new_offer?project={PROJECT_ID}")
        browser.wait_ms(5000)

        def snap(label: str) -> None:
            info = browser.evaluate(
                """() => {
                  const items = [...document.querySelectorAll('.offer-payment-type__item')];
                  const milestone = items.find((el) =>
                    /по мере выполнения задач/i.test((el.textContent || '').replace(/\\s+/g, ' '))
                  );
                  const lump = items.find((el) =>
                    /целиком, когда заказ выполнен/i.test((el.textContent || '').replace(/\\s+/g, ' '))
                  );
                  const stageRows = [...document.querySelectorAll('.stages__list .stages__stage')]
                    .filter((r) => r.offsetParent);
                  return {
                    items: items.map((el) => ({
                      cls: String(el.className || '').slice(0, 120),
                      text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                      checked: Boolean(el.querySelector('input:checked')),
                      aria: el.getAttribute('aria-checked'),
                    })),
                    milestoneCls: milestone ? String(milestone.className) : null,
                    lumpCls: lump ? String(lump.className) : null,
                    stageRows: stageRows.length,
                    stagePrices: [...document.querySelectorAll('.stages__stage-price-input')]
                      .filter((i) => i.offsetParent).length,
                    hasStagesBlock: Boolean(document.querySelector('.stages')),
                  };
                }"""
            )
            print(f"=== {label} ===")
            print(json.dumps(info, ensure_ascii=False, indent=2))

        snap("before_click")

        browser.evaluate(
            """
            () => {
              const item = [...document.querySelectorAll('.offer-payment-type__item')].find((el) =>
                /по мере выполнения задач/i.test((el.textContent || '').replace(/\\s+/g, ' '))
              );
              if (!item) return false;
              item.click();
              const input = item.querySelector('input[type="radio"], input[type="checkbox"]');
              if (input) input.click();
              return true;
            }
            """
        )
        browser.wait_ms(2000)
        snap("after_js_click")

        if hasattr(browser, "_ensure_page"):
            page = browser._ensure_page()
            item = page.locator(".offer-payment-type__item").filter(
                has_text="По мере выполнения задач"
            ).first
            if item.count() > 0:
                item.click(force=True)
                browser.wait_ms(2000)
        snap("after_pw_click")
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
