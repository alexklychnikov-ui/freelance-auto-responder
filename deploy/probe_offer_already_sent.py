from __future__ import annotations

import json
import sys

from src.adapters.kwork_offers import parse_offers_html
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

PROJECT_ID = sys.argv[1] if len(sys.argv) > 1 else "3204847"


def main() -> None:
    settings = get_settings()
    browser = get_browser_client(settings)
    try:
        browser.navigate("https://kwork.ru/offers")
        browser.wait_ms(4000)
        html = browser.snapshot() if hasattr(browser, "snapshot") else ""
        if not html:
            html = browser.evaluate("() => document.documentElement.outerHTML") or ""
        offers = parse_offers_html(html)
        offer = offers.get(PROJECT_ID)
        print("offer_on_offers_page:", json.dumps(
            {
                "found": offer is not None,
                "title": offer.title if offer else None,
                "informers": offer.informers if offer else None,
                "buyer_orders": offer.buyer_orders if offer else None,
            },
            ensure_ascii=False,
        ))
        links = browser.evaluate(
            f"""
            () => {{
              const pid = {json.dumps(PROJECT_ID)};
              const anchors = [...document.querySelectorAll('a[href*="/projects/"]')];
              const card = anchors.find((a) => a.href.includes('/projects/' + pid));
              const root = card?.closest('.want-card, .wants-card, article, div');
              const links = root
                ? [...root.querySelectorAll('a[href]')].map((a) => a.href).slice(0, 15)
                : [];
              return {{
                projectHref: card?.href || null,
                nearbyLinks: links,
              }};
            }}
            """
        )
        print("card_links:", json.dumps(links, ensure_ascii=False, indent=2))

        view = browser.evaluate(
            f"""
            () => {{
              const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
              return {{
                hasEditOffer: /редактир/i.test(text),
                hasRevoke: /отозвать/i.test(text),
                hasYourOffer: /ваше предложение|ваш отклик|вы предложили/i.test(text),
              }};
            }}
            """
        )
        print("view_flags:", json.dumps(view, ensure_ascii=False))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
