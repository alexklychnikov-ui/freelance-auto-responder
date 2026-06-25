"""Read back offer form fields after prepare."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import READ_OFFER_FORM_JS, kwork_offer_form_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings


def main() -> None:
    pid = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    browser = get_browser_client(get_settings())
    try:
        browser.navigate(kwork_offer_form_url(pid))
        browser.wait_ms(5000)
        print(json.dumps(browser.evaluate(READ_OFFER_FORM_JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
