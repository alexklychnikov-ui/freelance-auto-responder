"""Probe Vue stage model and autosave."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import (
    TRIGGER_OFFER_AUTOSAVE_JS,
    _fill_stage_title,
    _select_milestone_payment,
    kwork_offer_form_url,
)
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

PROBE = """
() => {
  const row = document.querySelector('.stages__stage');
  const chain = [];
  let el = row;
  for (let i = 0; i < 12 && el; i++) {
    const v = el.__vue__ || el.__vueParentComponent;
    if (v) {
      const data = v.$data || v.data || v.setupState || {};
      const keys = Object.keys(data).slice(0, 12);
      chain.push({ tag: el.tagName, cls: (el.className || '').toString().slice(0, 60), keys });
    }
    el = el.parentElement;
  }
  return chain;
}
"""


def main() -> None:
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url("3204427"))
        browser.wait_ms(5000)
        _select_milestone_payment(browser)
        browser.wait_ms(1000)
        _fill_stage_title(browser, 1, "Тестовая задача один")
        browser.wait_ms(500)
        print("vue", json.dumps(browser.evaluate(PROBE), ensure_ascii=False, indent=2))
        browser.evaluate(TRIGGER_OFFER_AUTOSAVE_JS)
        browser.wait_ms(12000)
        browser.navigate(kwork_offer_form_url("3204427"))
        browser.wait_ms(5000)
        _select_milestone_payment(browser)
        browser.wait_ms(1000)
        print(
            "after",
            browser.evaluate(
                """
                () => document.querySelector('.stages__stage .trumbowyg-editor')?.textContent?.trim()
                """
            ),
        )
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
