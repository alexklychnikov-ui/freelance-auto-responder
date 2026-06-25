"""Compare offer.stages vs localStages after vue fill."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import (
    _fill_offer_stages,
    _fill_price,
    _sync_stages_draft,
    kwork_offer_form_url,
)
from src.adapters.kwork_stages import plan_offer_stages
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings
from src.responses.prepared_store import PreparedResponseStore

JS = """
() => {
  const offer = document.querySelector('.offer-custom')?.__vue__;
  const parent = document.querySelector('.stages')?.__vue__;
  return {
    offerStages: offer?.stages,
    priceStages: offer?.priceStages,
    localStages: parent?.localStages,
  };
}
"""


def main() -> None:
    item = next(
        i
        for i in PreparedResponseStore(get_settings().prepared_responses_dir).list_all()
        if i.project_id == "3204427"
    )
    stages = plan_offer_stages(int(item.price), item.project)
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url("3204427"))
        browser.wait_ms(5000)
        _fill_price(browser, str(item.price))
        _fill_offer_stages(browser, stages)
        _sync_stages_draft(browser)
        print(json.dumps(browser.evaluate(JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
