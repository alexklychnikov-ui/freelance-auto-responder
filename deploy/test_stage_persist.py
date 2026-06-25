"""Fill stages, autosave, reload, read back."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import (
    TRIGGER_OFFER_AUTOSAVE_JS,
    _fill_offer_stages,
    _fill_price,
    _read_stages_from_dom,
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
    item = next(
        i for i in PreparedResponseStore(settings.prepared_responses_dir).list_all() if i.project_id == pid
    )
    stages = plan_offer_stages(int(item.price), item.project)
    browser = get_browser_client(settings)
    url = kwork_offer_form_url(pid)
    try:
        browser.navigate(url)
        browser.wait_ms(5000)
        _fill_price(browser, str(item.price))
        browser.wait_ms(500)
        result = _fill_offer_stages(browser, stages)
        print("fill", json.dumps(result, ensure_ascii=False, indent=2))
        browser.evaluate(TRIGGER_OFFER_AUTOSAVE_JS)
        browser.wait_ms(10000)
        print("before_reload", json.dumps(_read_stages_from_dom(browser), ensure_ascii=False, indent=2))
        browser.navigate(url)
        browser.wait_ms(5000)
        _select_milestone_payment(browser)
        browser.wait_ms(1000)
        print("after_reload", json.dumps(_read_stages_from_dom(browser), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
