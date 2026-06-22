from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.adapters.kwork import KworkAdapter

browser = PlaywrightBrowserAdapter()
adapter = KworkAdapter(
    source_key="kwork_dev_it",
    listing_url="https://kwork.ru/projects?c=11",
    browser=browser,
    auto_login=False,
)
try:
    previews = adapter.scan_new()
    print("count", len(previews))
    for p in previews[:5]:
        print(p.project_id, p.title[:60])
except Exception as e:
    print("ERROR", type(e).__name__, e)
finally:
    browser.close()
