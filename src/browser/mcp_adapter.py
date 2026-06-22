from __future__ import annotations

import time
from typing import Any

from src.browser.base import BrowserClient
from src.browser.mcp_session import BrowserMcpSession, resolve_server_path


class McpBrowserAdapter:
    """Live BrowserMCP adapter — navigate/snapshot/CDP via stdio MCP session."""

    def __init__(
        self,
        server_path: str | None = None,
        *,
        session: BrowserMcpSession | None = None,
        navigate_wait_seconds: float = 2.0,
    ) -> None:
        path = resolve_server_path(server_path)
        self._owns_session = session is None
        self._session = session or BrowserMcpSession(
            path,
            navigate_wait_seconds=navigate_wait_seconds,
        )

    def navigate(self, url: str) -> None:
        self._session.navigate(url)

    def snapshot(self) -> str:
        return self._session.snapshot()

    def click(self, ref_or_selector: str) -> None:
        self._session.click(ref_or_selector)

    def fill(self, ref_or_selector: str, text: str) -> None:
        self._session.fill(ref_or_selector, text)

    def evaluate(self, js: str) -> Any:
        return self._session.evaluate(js)

    def screenshot(self) -> bytes:
        return self._session.screenshot()

    def wait_ms(self, ms: int) -> None:
        time.sleep(ms / 1000)

    def close(self) -> None:
        if self._owns_session:
            self._session.close()


def _as_browser_client(adapter: McpBrowserAdapter) -> BrowserClient:
    return adapter
