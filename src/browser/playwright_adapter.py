from __future__ import annotations

from pathlib import Path
from typing import Any

from src.browser.base import BrowserClient


class PlaywrightBrowserAdapter:
    """Headless Chromium for VPS/Linux (no BrowserMCP extension)."""

    def __init__(
        self,
        *,
        headless: bool = True,
        storage_state_path: str | None = None,
    ) -> None:
        self._headless = headless
        self._storage_state_path = storage_state_path
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        context_kwargs: dict[str, Any] = {}
        if self._storage_state_path:
            state_path = Path(self._storage_state_path)
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)
        self._context = self._browser.new_context(**context_kwargs)
        self._page = self._context.new_page()
        return self._page

    def navigate(self, url: str) -> None:
        page = self._ensure_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)

    def snapshot(self) -> str:
        return self._ensure_page().content()

    def click(self, ref_or_selector: str) -> None:
        page = self._ensure_page()
        if ref_or_selector.startswith("ref-") or ref_or_selector.startswith("@"):
            raise NotImplementedError("Playwright adapter uses CSS selectors only")
        page.click(ref_or_selector, timeout=15000)

    def fill(self, ref_or_selector: str, text: str) -> None:
        page = self._ensure_page()
        page.fill(ref_or_selector, text, timeout=15000)

    def evaluate(self, js: str) -> Any:
        return self._ensure_page().evaluate(js)

    def screenshot(self) -> bytes:
        return self._ensure_page().screenshot(full_page=True)

    def wait_ms(self, ms: int) -> None:
        self._ensure_page().wait_for_timeout(ms)

    def save_storage_state(self) -> None:
        if self._context is None or not self._storage_state_path:
            return
        path = Path(self._storage_state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(path))

    def close(self) -> None:
        if self._context is not None and self._storage_state_path:
            try:
                self.save_storage_state()
            except Exception:
                pass
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
