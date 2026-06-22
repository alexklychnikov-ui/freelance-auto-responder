"""Verify Kwork offer draft persists after prepare (same storage session)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import KworkAdapter, kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings
from src.responses.prepared_store import PreparedResponseStore

READ_BACK_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const desc =
    document.querySelector('textarea[name="description"]') ||
    document.querySelector('textarea.v-textarea') ||
    document.querySelector('.wants-offer__description textarea') ||
    document.querySelector('textarea');
  const priceInput =
    document.querySelector('#offer-custom-price') ||
    document.querySelector('input[name="price"]') ||
    document.querySelector('.wants-offer__price input') ||
    document.querySelector('input[type="number"]');
  const titleInput = [...document.querySelectorAll('input[type="text"], input:not([type])')].find((inp) =>
    /название заказа/i.test(inp.getAttribute('placeholder') || '')
  ) || null;
  return {
    url: location.href,
    descLen: (desc?.value || '').length,
    descPreview: (desc?.value || '').slice(0, 80),
    price: priceInput?.value || '',
    title: titleInput?.value || '',
  };
}
"""


def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else "3202784"
    settings = get_settings()
    store = PreparedResponseStore(settings.prepared_responses_dir)
    items = [i for i in store.list_all() if i.project_id == project_id]
    if not items:
        print("no prepared item")
        sys.exit(1)
    item = items[0]

    browser = get_browser_client(settings)
    try:
        adapter = KworkAdapter(
            source_key=item.source_key,
            listing_url="",
            browser=browser,
            dry_run_submit=True,
            kwork_credentials=settings.kwork_credentials(),
            auto_login=settings.kwork_auto_login,
        )
        print("=== prepare ===")
        result = adapter.prepare_response(
            project_id,
            item.response_text,
            item.price,
            delivery_days=item.delivery_days,
            order_title=item.title,
        )
        print("result:", result)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(4000)
        print("readback after fill:", browser.evaluate(READ_BACK_JS))

        if hasattr(browser, "save_storage_state"):
            browser.save_storage_state()

        offer_url = kwork_offer_form_url(project_id)
        print("=== reopen same session ===")
        browser.navigate(offer_url)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(4000)
        print("readback after reopen:", browser.evaluate(READ_BACK_JS))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
