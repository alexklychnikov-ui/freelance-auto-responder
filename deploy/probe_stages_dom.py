"""Probe Kwork offer stages DOM."""
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
  const addBtns = [...document.querySelectorAll('a, button, span, p, .stages a, .stages button, .stages span')].filter((el) => {
    const own = (el.childNodes.length === 1 && el.childNodes[0].nodeType === 3)
      ? el.textContent : [...el.childNodes].filter(n => n.nodeType===3).map(n=>n.textContent).join('');
    return /добавить задачу/i.test((own || el.textContent || '').replace(/\\s+/g, ' '));
  }).map((el) => ({
    tag: el.tagName,
    cls: el.className?.toString?.().slice(0, 120),
    text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
    childOnly: el.children.length === 0,
  }));
  const stages = [...document.querySelectorAll('[class*="stage"]')].slice(0, 20).map((el) => ({
    tag: el.tagName,
    cls: el.className?.toString?.().slice(0, 100),
  }));
  const ta = document.querySelector('textarea[name="stageTitle-1"]');
  const hasJQ = Boolean(window.jQuery || window.$);
  let trumbowyg = false;
  if (ta && hasJQ) {
    const $ = window.jQuery || window.$;
    trumbowyg = typeof $(ta).trumbowyg === 'function';
  }
  return { addBtns, stagesHtml: document.querySelector('.stages')?.innerHTML?.slice(0, 2500), stagesCount: document.querySelectorAll('.stages__stage-price-input').length, hasJQ, trumbowyg };
}
"""


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        _select_milestone_payment(browser)
        browser.wait_ms(1000)
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
