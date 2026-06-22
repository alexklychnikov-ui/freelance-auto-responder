from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.adapters.kwork_auth import is_logged_in

browser = PlaywrightBrowserAdapter(
    storage_state_path="/opt/freelance-responder/data/kwork_storage.json"
)
try:
    browser.navigate("https://kwork.ru/projects/3202099/view")
    browser.wait_ms(2000)
    print("logged_in", is_logged_in(browser))
    info = browser.evaluate("""() => ({
      offer: [...document.querySelectorAll('a, button')].filter(el =>
        /предложить/i.test(el.textContent||'')).map(el => el.textContent.trim().slice(0,60)),
      textareas: document.querySelectorAll('textarea').length,
    })""")
    print(info)
finally:
    browser.close()
