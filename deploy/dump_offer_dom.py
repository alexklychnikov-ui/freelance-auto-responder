"""Dump title/price/deadline DOM on new_offer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork_auth import is_logged_in
from src.adapters.kwork import kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const all = [...document.querySelectorAll('input, textarea, [contenteditable="true"]')];
  return {
    fields: all.map((el) => ({
      tag: el.tagName,
      type: el.type,
      id: el.id,
      name: el.name,
      ph: el.placeholder,
      max: el.maxLength,
      val: (el.value || el.textContent || '').slice(0, 40),
      cls: String(el.className || '').slice(0, 80),
    })),
    labels: [...document.querySelectorAll('label, .form-field__name, p, span, div')]
      .filter((el) => /название заказа|стоимость|срок выполнения/i.test(norm(el.textContent)))
      .slice(0, 15)
      .map((el) => ({ tag: el.tagName, text: norm(el.textContent).slice(0, 50) })),
  };
}
"""


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate("https://kwork.ru/")
        browser.wait_ms(1000)
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        print("logged", is_logged_in(browser))
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
