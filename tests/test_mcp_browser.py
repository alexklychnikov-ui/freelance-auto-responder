from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.browser.external_adapter import ExternalBrowserMcpAdapter
from src.browser.mcp_session import BrowserMcpSession, resolve_server_path
from src.browser.factory import close_browser_client, get_browser_client
from src.config import Settings


class FakeMcpSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def navigate(self, url: str) -> None:
        self.calls.append(("navigate", {"url": url}))

    def snapshot(self) -> str:
        self.calls.append(("snapshot", {}))
        return "<snapshot/>"

    def evaluate(self, js: str):
        self.calls.append(("evaluate", {"js": js}))
        return 3

    def click(self, ref_or_selector: str) -> None:
        self.calls.append(("click", {"target": ref_or_selector}))

    def fill(self, ref_or_selector: str, text: str) -> None:
        self.calls.append(("fill", {"target": ref_or_selector, "text": text}))

    def screenshot(self) -> bytes:
        return b"png"

    def close(self) -> None:
        pass


def test_resolve_server_path_default() -> None:
    path = resolve_server_path(None)
    assert "BrowserMCP" in path
    assert path.endswith("index.js")


def test_external_adapter_delegates_to_session() -> None:
    fake = FakeMcpSession()
    adapter = ExternalBrowserMcpAdapter(
        server_path="C:/x/index.js",
        session=fake,  # type: ignore[arg-type]
    )
    adapter.navigate("https://kwork.ru/projects?c=11")
    adapter.snapshot()
    adapter.evaluate("1+1")
    adapter.click(".btn")
    adapter.fill("textarea", "hello")
    assert fake.calls[0] == ("navigate", {"url": "https://kwork.ru/projects?c=11"})


def test_close_browser_client() -> None:
    fake = MagicMock()
    close_browser_client(fake)
    fake.close.assert_called_once()


def test_mcp_session_evaluate_parses_cdp_json() -> None:
    session = BrowserMcpSession(
        r"C:\Python\Projects\BrowserMCP\packages\mcp-server\dist\index.js"
    )
    block = MagicMock()
    block.type = "text"
    block.text = json.dumps({"result": {"value": [1, 2, 3]}})
    session.call_tool = MagicMock(return_value=[block])  # type: ignore[method-assign]
    assert session.evaluate("(() => [1,2,3])()") == [1, 2, 3]


def test_factory_external() -> None:
    client = get_browser_client(
        Settings(
            openai_api_key="k",
            telegram_bot_token="t",
            telegram_chat_id="1",
            response_journal="j.xlsx",
            browser_adapter="external",
            browsermcp_server="C:/Python/Projects/BrowserMCP/packages/mcp-server/dist/index.js",
            _env_file=None,
        )
    )
    assert isinstance(client, ExternalBrowserMcpAdapter)
