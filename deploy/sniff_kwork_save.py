"""Capture Kwork network requests during offer form fill."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.responses.prepared_store import PreparedResponseStore


def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else "3202784"
    settings = get_settings()
    store = PreparedResponseStore(settings.prepared_responses_dir)
    item = next(i for i in store.list_all() if i.project_id == project_id)

    from playwright.sync_api import sync_playwright

    state = settings.kwork_storage_state
    urls: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_kwargs = {}
        if state and Path(state).exists():
            ctx_kwargs["storage_state"] = state
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        def on_request(req):
            u = req.url
            if any(x in u for x in ("offer", "want", "project", "api")):
                urls.append(f"{req.method} {u}")

        page.on("request", on_request)
        page.goto(f"https://kwork.ru/new_offer?project={project_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        page.fill('textarea[name="description"], textarea', item.response_text[:500])
        page.wait_for_timeout(1000)
        page.fill("#offer-custom-price, input[name='price']", item.price)
        page.wait_for_timeout(8000)
        print(json.dumps(urls[-40:], ensure_ascii=False, indent=2))
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
