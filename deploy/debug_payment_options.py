"""Dump payment type options on offer form."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import _fill_price, kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const hits = [...document.querySelectorAll('label, div, button, span, input')]
    .filter((el) => /целиком|по мере|частями/i.test(norm(el.textContent || el.value || '')))
    .slice(0, 20)
    .map((el) => ({
      tag: el.tagName,
      type: el.type,
      id: el.id,
      name: el.name,
      text: norm(el.textContent || el.value || '').slice(0, 100),
      visible: Boolean(el.offsetParent),
      cls: String(el.className || '').slice(0, 80),
    }));
  return hits;
}
"""


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        _fill_price(browser, "35000")
        browser.wait_ms(500)
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
