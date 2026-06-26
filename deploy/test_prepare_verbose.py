"""Run prepare_response once and print full result."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import KworkAdapter
from src.adapters.kwork_stages import plan_offer_stages
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings
from src.responses.prepared_store import PreparedResponseStore


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
        result = adapter.prepare_response(
            project_id,
            item.response_text,
            item.price,
            delivery_days=item.delivery_days,
            order_title=item.title,
            project=item.project,
        )
        print("success:", result.success)
        print("message:", result.message[:4000] if result.message else "")
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
