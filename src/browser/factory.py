from __future__ import annotations

from src.browser.base import BrowserClient
from src.browser.cursor_adapter import CursorBrowserAdapter
from src.browser.external_adapter import ExternalBrowserMcpAdapter
from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.browser.mcp_session import resolve_server_path
from src.config import Settings


def get_browser_client(settings: Settings) -> BrowserClient:
    adapter = settings.browser_adapter.lower().strip()
    server_path = resolve_server_path(settings.browsermcp_server)
    wait = settings.browser_navigate_wait_seconds
    if adapter == "playwright":
        storage = (settings.kwork_storage_state or "").strip() or None
        return PlaywrightBrowserAdapter(storage_state_path=storage)
    if adapter == "external":
        return ExternalBrowserMcpAdapter(
            server_path=server_path,
            navigate_wait_seconds=wait,
        )
    if adapter == "cursor":
        return CursorBrowserAdapter(
            server_path=server_path,
            navigate_wait_seconds=wait,
        )
    raise ValueError(f"Unknown BROWSER_ADAPTER: {settings.browser_adapter!r}")


def close_browser_client(client: BrowserClient) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
