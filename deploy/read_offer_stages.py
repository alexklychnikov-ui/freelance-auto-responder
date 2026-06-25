"""Read milestone stages from offer form."""
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
  const rows = [...document.querySelectorAll('textarea[name^="stageTitle-"]')].map((ta) => ({
    name: ta.name,
    title: (ta.value || '').replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim(),
  }));
  const prices = [...document.querySelectorAll('.stages__stage-price-input')].map((inp) =>
    (inp.value || '').replace(/\\s/g, '')
  );
  const payment = [...document.querySelectorAll('label, div, span')].find((el) =>
    /по мере выполнения|целиком.*заказ/i.test((el.textContent || '').replace(/\\s+/g, ' '))
  )?.textContent?.replace(/\\s+/g, ' ').trim().slice(0, 80);
  return { rows, prices, paymentHint: payment || null };
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
