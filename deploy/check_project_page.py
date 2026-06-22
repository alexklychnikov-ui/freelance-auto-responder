from src.adapters.kwork import OFFER_OPEN_JS
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

b = PlaywrightBrowserAdapter(
    storage_state_path="/opt/freelance-responder/data/kwork_storage.json"
)
try:
    b.navigate("https://kwork.ru/projects/3202099/view")
    b.wait_ms(3000)
    opened = b.evaluate(OFFER_OPEN_JS)
    b.wait_ms(5000)
    info = b.evaluate(
        """() => ({
          url: location.href,
          textareas: document.querySelectorAll('textarea').length,
          inputs: document.querySelectorAll('input').length,
          html: (document.querySelector('.wants-offer, [class*=offer]')||{}).className,
        })"""
    )
    print("open:", opened)
    print("after:", info)
finally:
    b.close()
