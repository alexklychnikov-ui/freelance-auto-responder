"""Debug new_offer form fill + readback on VPS."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.adapters.kwork import (
    EXTRACT_FORM_BOUNDS_JS,
    FIND_PRICE_INPUT_JS,
    FIND_TITLE_INPUT_JS,
    READ_OFFER_FORM_JS,
    _build_deadline_pick_js,
    _build_offer_fill_js,
    _fill_deadline,
    _fill_order_title,
    _fill_price,
    _read_form_price_bounds,
    kwork_offer_form_url,
)
from src.adapters.kwork_auth import ensure_logged_in, is_logged_in
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings, get_enabled_sources
from src.adapters.kwork import KworkAdapter

INSPECT_JS = f"""
() => {{
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const findPrice = {FIND_PRICE_INPUT_JS};
  const findTitle = {FIND_TITLE_INPUT_JS};
  const price = findPrice();
  const title = findTitle();
  const inputs = [...document.querySelectorAll('input')].map((el) => ({{
    id: el.id,
    name: el.name,
    type: el.type,
    value: el.value,
    ph: el.placeholder,
    cls: String(el.className || '').slice(0, 60),
  }}));
  return {{
    url: location.href,
    priceFound: Boolean(price),
    priceId: price?.id,
    priceName: price?.name,
    priceValue: price?.value,
    titleFound: Boolean(title),
    titleValue: title?.value,
    bounds: (() => {{
      const fn = {EXTRACT_FORM_BOUNDS_JS};
      return fn();
    }})(),
    inputs,
  }};
}}
"""


def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else "3204427"
    settings = get_settings()
    browser = get_browser_client(settings)
    source = get_enabled_sources(settings.sources_config_path)[0]
    creds = None
    if settings.kwork_login and settings.kwork_password:
        from src.adapters.kwork_auth import KworkCredentials

        creds = KworkCredentials(settings.kwork_login, settings.kwork_password)

    try:
        adapter = KworkAdapter(
            source_key=source.id,
            listing_url=source.url or "https://kwork.ru/projects",
            browser=browser,
            kwork_credentials=None,
            auto_login=False,
        )
        browser.navigate("https://kwork.ru/")
        browser.wait_ms(1500)
        if not is_logged_in(browser):
            print("NOT LOGGED IN")
            sys.exit(1)
        url = kwork_offer_form_url(project_id)
        browser.navigate(url)
        browser.wait_ms(5000)
        print("logged_in", is_logged_in(browser))
        print("before", json.dumps(browser.evaluate(INSPECT_JS), ensure_ascii=False, indent=2))
        print("readback_before", browser.evaluate(READ_OFFER_FORM_JS))

        price = "35000"
        title = "Доработать 2 Telegram-бота по готовому ТЗ"
        text = "test description fill debug"

        print("fill_price", _fill_price(browser, price))
        print("fill_title", _fill_order_title(browser, title))
        browser.wait_ms(500)
        print("after_manual", json.dumps(browser.evaluate(INSPECT_JS), ensure_ascii=False, indent=2))

        fill = browser.evaluate(_build_offer_fill_js(text, price, order_title=title))
        print("fill_js", fill)
        browser.wait_ms(500)
        dl = _fill_deadline(browser, 10)
        print("deadline", dl)
        _fill_price(browser, price)
        _fill_order_title(browser, title)
        browser.wait_ms(2000)
        print("readback_after", browser.evaluate(READ_OFFER_FORM_JS))
        print("after", json.dumps(browser.evaluate(INSPECT_JS), ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
