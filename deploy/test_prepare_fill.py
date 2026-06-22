from src.adapters.kwork import KworkAdapter, OFFER_OPEN_JS, _build_offer_fill_js
from src.adapters.kwork_pricing import suggest_offer_price
from src.browser.factory import close_browser_client, get_browser_client
from src.config import get_settings
import json

settings = get_settings()
browser = get_browser_client(settings)
text = json.loads(
    open("data/prepared_responses/kwork_kwork_dev_it_3202099.json", encoding="utf-8").read()
)["response_text"]
price = "5000"
days = 14
try:
    adapter = KworkAdapter(
        source_key="kwork_dev_it",
        listing_url="",
        browser=browser,
        dry_run_submit=True,
        kwork_credentials=None,
        auto_login=False,
    )
    result = adapter.prepare_response("3202099", text, price, delivery_days=days)
    print("result:", result)
finally:
    close_browser_client(browser)
