"""Dump localStages Vue model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import _select_milestone_payment, kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

JS = """
() => {
  const parent = document.querySelector('.stages')?.__vue__;
  const row = document.querySelector('.stages__stage')?.__vue__;
  return {
    localStages: parent?.localStages,
    titleLocal: row?.titleLocal,
    priceLocal: row?.priceLocal,
  };
}
"""


def main() -> None:
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url("3204427"))
        browser.wait_ms(5000)
        _select_milestone_payment(browser)
        browser.wait_ms(1000)
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
