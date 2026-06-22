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
          selects: [...document.querySelectorAll('select')].map(s => ({
            cls: (s.className||'').slice(0,60),
            opts: [...s.options].slice(0,5).map(o => o.textContent.trim()),
          })),
          duration: [...document.querySelectorAll('.duration-select *, [class*=duration]')]
            .map(el => (el.textContent||'').replace(/\\s+/g,' ').trim())
            .filter(t => t && t.length < 60).slice(0,20),
          pay: [...document.querySelectorAll('label, span, div')]
            .map(el => (el.textContent||'').replace(/\\s+/g,' ').trim())
            .filter(t => /целиком|поэтап|оплат/i.test(t)).slice(0,10),
        })"""
    )
    print(info)
finally:
    close_browser_client(browser)
