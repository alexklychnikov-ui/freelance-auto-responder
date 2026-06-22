from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.adapters.kwork import KworkAdapter

URLS = [
    "https://kwork.ru/projects?c=11",
    "https://kwork.ru/projects?c=41",
]

browser = PlaywrightBrowserAdapter()
try:
    for url in URLS:
        adapter = KworkAdapter(
            source_key="kwork_dev_it",
            listing_url=url,
            browser=browser,
            auto_login=False,
        )
        previews = adapter.scan_new()
        ids = [p.project_id for p in previews]
        print(url, "count", len(previews))
        print("  ids", ids)
finally:
    browser.close()
