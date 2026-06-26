"""Prepare fill then reopen form — check if stages persist in draft."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import (
    KworkAdapter,
    _is_milestone_payment_selected,
    _read_stages_from_dom,
    kwork_offer_form_url,
)
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings
from src.responses.prepared_store import PreparedResponseStore

SNAP_JS = """
() => {
  const items = [...document.querySelectorAll('.offer-payment-type__item')];
  const milestone = items.find((el) =>
    /по мере выполнения задач/i.test((el.textContent || '').replace(/\\s+/g, ' '))
  );
  const offer = document.querySelector('.offer-custom')?.__vue__;
  const parent = document.querySelector('.stages')?.__vue__;
  const wrap = document.querySelector('.custom-kwork-offer__wrapper')?.__vue__;
  const prices = [...document.querySelectorAll('.stages__stage-price-input')]
    .filter((i) => i.offsetParent).map((i) => i.value);
  const titles = [...document.querySelectorAll('.stages__stage .trumbowyg-editor')]
    .filter((e) => e.offsetParent).map((e) => (e.textContent || '').trim().slice(0, 60));
  return {
    milestoneActive: Boolean(milestone?.classList.contains('active')),
    stagePrices: prices,
    stageTitles: titles,
    localStages: parent?.localStages,
    offerStages: offer?.stages,
    paymentKeys: Object.keys(offer || {}).filter((k) => /pay|stage|draft|type/i.test(k)).slice(0, 30),
    wrapKeys: Object.keys(wrap || {}).filter((k) => /pay|stage|draft|request/i.test(k)).slice(0, 30),
    requestData: wrap?.requestData ? {
      payment_type: wrap.requestData.payment_type ?? wrap.requestData.paymentType,
      stages: wrap.requestData.stages,
    } : null,
  };
}
"""


def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else "3205065"
    settings = get_settings()
    item = next(
        i
        for i in PreparedResponseStore(settings.prepared_responses_dir).list_all()
        if i.project_id == project_id
    )
    browser = get_browser_client(settings)
    try:
        adapter = KworkAdapter(
            source_key=item.source_key,
            listing_url=item.url,
            browser=browser,
            auto_login=False,
        )
        print("=== prepare ===")
        result = adapter.prepare_response(
            project_id,
            item.response_text,
            item.price,
            delivery_days=item.delivery_days,
            order_title=item.title,
            project=item.project,
        )
        print("success:", result.success)
        print("message:", (result.message or "")[:500])
        print("after_prepare:", json.dumps(browser.evaluate(SNAP_JS), ensure_ascii=False, indent=2))
        print("dom:", json.dumps(_read_stages_from_dom(browser), ensure_ascii=False, indent=2))

        if hasattr(browser, "save_storage_state"):
            browser.save_storage_state()
        browser.wait_ms(3000)

        print("\n=== reopen same session (no milestone click) ===")
        browser.navigate(kwork_offer_form_url(project_id))
        browser.wait_ms(6000)
        snap = browser.evaluate(SNAP_JS)
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        print("milestone_selected_fn:", _is_milestone_payment_selected(browser))
        print("dom:", json.dumps(_read_stages_from_dom(browser), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
