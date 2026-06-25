"""Read milestone stages from offer form."""
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
  const rows = [...document.querySelectorAll('.stages__list .stages__stage')].map((row) => {
    const ta = row.querySelector('textarea[name^="stageTitle-"]');
    const ed = ta?.closest('.trumbowyg-box')?.querySelector('.trumbowyg-editor');
    const hidden = row.querySelector('.stages__stage-text');
    const title = (ed?.textContent || hidden?.textContent || ta?.value || '')
      .replace(/<[^>]+>/g, ' ')
      .replace(/\\s+/g, ' ')
      .trim();
    const price = (row.querySelector('.stages__stage-price-input')?.value || '')
      .replace(/\\s/g, '');
    return { name: ta?.name || null, title, price };
  });
  const payment = [...document.querySelectorAll('label, div, span')].find((el) =>
    /по мере выполнения|целиком.*заказ/i.test((el.textContent || '').replace(/\\s+/g, ' '))
  )?.textContent?.replace(/\\s+/g, ' ').trim().slice(0, 80);
  return {
    rows: rows.map(({ name, title }) => ({ name, title })),
    prices: rows.map((r) => r.price).filter(Boolean),
    paymentHint: payment || null,
  };
}
"""


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        _select_milestone_payment(browser)
        browser.wait_ms(800)
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
