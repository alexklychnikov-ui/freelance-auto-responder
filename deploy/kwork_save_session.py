"""Login to Kwork and save Playwright storage state for daemon."""
from __future__ import annotations

import sys

from src.adapters.kwork_auth import KworkAuthError, KworkCredentials, ensure_logged_in, is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.config import get_settings


def main() -> int:
    settings = get_settings()
    pair = settings.kwork_credentials()
    if not pair:
        print("FAIL: set KWORK_LOGIN and KWORK_PASSWORD in .env")
        return 1

    state_path = settings.kwork_storage_state or "data/kwork_storage.json"
    browser = PlaywrightBrowserAdapter(storage_state_path=state_path)
    creds = KworkCredentials(login=pair[0], password=pair[1])
    try:
        print("logging in...")
        ensure_logged_in(browser, creds, force=True)
        browser.navigate("https://kwork.ru/")
        browser.wait_ms(1500)
        if not is_logged_in(browser):
            print("FAIL: login submitted but session not detected")
            return 1
        browser.save_storage_state()
        print(f"OK: session saved to {state_path}")
        return 0
    except KworkAuthError as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
