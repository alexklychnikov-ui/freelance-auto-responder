from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.adapters.kwork_auth import ensure_logged_in, KworkCredentials, is_logged_in
from dotenv import dotenv_values

cfg = dotenv_values("/opt/freelance-responder/.env")
browser = PlaywrightBrowserAdapter()
creds = KworkCredentials(cfg["KWORK_LOGIN"], cfg["KWORK_PASSWORD"])
try:
    browser.navigate("https://kwork.ru/login")
    print("url", browser._ensure_page().url)
    print("logged_in_before", is_logged_in(browser))
    ensure_logged_in(browser, creds, force=True)
    print("logged_in_after", is_logged_in(browser))
    browser.navigate("https://kwork.ru/projects?c=11")
    n = browser.evaluate("document.querySelectorAll('a[href*=\"/projects/\"]').length")
    print("project_links", n)
except Exception as e:
    print("ERROR", type(e).__name__, e)
finally:
    browser.close()
