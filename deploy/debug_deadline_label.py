"""Find deadline vue-select by label."""
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
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const hits = [...document.querySelectorAll('label, span, div, p, h4, h5')]
    .filter((el) => /^Срок выполнения$/i.test(norm(el.textContent)))
    .slice(0, 5)
    .map((el) => {
      const root = el.closest('.form-field, .field, .form-item, .offer-form__row, div') || el.parentElement;
      const vs = root?.querySelector('.v-select, .vs--single, .multiselect');
      const inp = root?.querySelector('input.vs__search, input');
      return {
        tag: el.tagName,
        rootCls: String(root?.className || '').slice(0, 80),
        vsCls: String(vs?.className || '').slice(0, 80),
        inpPh: inp?.getAttribute('placeholder'),
        inpVal: inp?.value,
        rootText: norm(root?.textContent || '').slice(0, 120),
      };
    });
  return hits;
}
"""


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
