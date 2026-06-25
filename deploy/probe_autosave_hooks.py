"""Find autosave hooks on offer form."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

JS = """
() => {
  const pick = (vm) => Object.keys(vm || {}).filter((k) => /save|draft|auto|stage|change/i.test(k)).slice(0, 40);
  const offer = document.querySelector('.offer-custom')?.__vue__;
  const wrap = document.querySelector('.custom-kwork-offer__wrapper')?.__vue__;
  const stages = document.querySelector('.stages')?.__vue__;
  return { offer: pick(offer), wrap: pick(wrap), stages: pick(stages) };
}
"""


def main() -> None:
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url("3204427"))
        browser.wait_ms(5000)
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
