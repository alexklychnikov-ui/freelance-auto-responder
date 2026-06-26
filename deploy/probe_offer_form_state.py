from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

PROJECT_ID = sys.argv[1] if len(sys.argv) > 1 else "3204847"


def main() -> None:
    settings = get_settings()
    browser = get_browser_client(settings)
    try:
        for url in (
            f"https://kwork.ru/new_offer?project={PROJECT_ID}",
            f"https://kwork.ru/projects/{PROJECT_ID}/view",
            f"https://kwork.ru/offers",
        ):
            browser.navigate(url)
            browser.wait_ms(4000)
            info = browser.evaluate(
                """() => ({
                  url: location.href,
                  title: document.title,
                  h1: (document.querySelector('h1')?.textContent || '').replace(/\\s+/g,' ').trim().slice(0,120),
                  bodySnippet: (document.body?.innerText || '').replace(/\\s+/g,' ').trim().slice(0,1500),
                  hasOfferForm: Boolean(document.querySelector('textarea[name="description"], .offer-custom, .custom-kwork-offer__wrapper')),
                  hasStages: document.querySelectorAll('.stages__stage').length,
                  hasPaymentType: document.querySelectorAll('.offer-payment-type__item').length,
                  hasDuration: Boolean(document.querySelector('.duration-select')),
                  alreadyOffered: /уже отправили|отклик уже|ваше предложение|редактировать предложение|отозвать/i.test(document.body?.innerText || ''),
                })"""
            )
            print("===", url, "===")
            print(json.dumps(info, ensure_ascii=False, indent=2))
    finally:
        close_browser_client(browser)


if __name__ == "__main__":
    main()
