from src.adapters.kwork import OFFER_OPEN_JS
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings

settings = get_settings()
browser = get_browser_client(settings)
try:
    browser.navigate("https://kwork.ru/projects/3202099/view")
    browser.wait_ms(2000)
    browser.evaluate(OFFER_OPEN_JS)
    browser.wait_ms(4000)
    info = browser.evaluate(
        """() => ({
          url: location.href,
          inputs: [...document.querySelectorAll('input')].map(el => ({
            name: el.name,
            type: el.type,
            id: el.id,
            cls: (el.className||'').toString().slice(0,80),
            ph: el.placeholder,
          })),
          textareas: [...document.querySelectorAll('textarea')].map(el => ({
            name: el.name,
            cls: (el.className||'').toString().slice(0,80),
          })),
        })"""
    )
    print(info)
finally:
    close_browser_client(browser)
