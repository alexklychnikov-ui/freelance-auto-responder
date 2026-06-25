"""Debug milestone stages fill only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import (
    _fill_offer_stages,
    _fill_price,
    _select_milestone_payment,
    kwork_offer_form_url,
)
from src.adapters.kwork_stages import plan_offer_stages
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings
from src.responses.prepared_store import PreparedResponseStore


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    settings = get_settings()
    item = next(i for i in PreparedResponseStore(settings.prepared_responses_dir).list_all() if i.project_id == pid)
    stages = plan_offer_stages(int(item.price), item.project)
    print("plan", stages)
    browser = get_browser_client(settings)
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        print("price fill", _fill_price(browser, str(item.price)))
        browser.wait_ms(500)
        print("select", _select_milestone_payment(browser))
        browser.wait_ms(1000)
        vis = browser.evaluate(
            """
            () => ({
              stageInputs: document.querySelectorAll('.stages__stage-price-input').length,
              firstVisible: Boolean(document.querySelector('.stages__stage-price-input')?.offsetParent),
              ta1: Boolean(document.querySelector('textarea[name="stageTitle-1"]')),
              editor1: document.querySelector('textarea[name="stageTitle-1"]')
                ?.closest('.trumbowyg-box')?.querySelector('.trumbowyg-editor')?.textContent?.slice(0,40),
            })
            """
        )
        print("vis", vis)
        result = _fill_offer_stages(browser, stages)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        browser.wait_ms(3000)
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
