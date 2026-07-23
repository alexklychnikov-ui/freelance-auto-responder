from __future__ import annotations

import pytest

from src.browser.base import BrowserClient
from src.browser.cursor_adapter import CursorBrowserAdapter
from src.browser.external_adapter import ExternalBrowserMcpAdapter
from src.browser.factory import get_browser_client
from src.config import Settings


def test_factory_cursor() -> None:
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        browser_adapter="cursor",
        _env_file=None,
    )
    client = get_browser_client(settings)
    assert isinstance(client, CursorBrowserAdapter)


def test_factory_external() -> None:
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        browser_adapter="external",
        browsermcp_server="/path/to/server.js",
        _env_file=None,
    )
    client = get_browser_client(settings)
    assert isinstance(client, ExternalBrowserMcpAdapter)


def test_factory_unknown_raises() -> None:
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        browser_adapter="unknown",
        _env_file=None,
    )
    with pytest.raises(ValueError):
        get_browser_client(settings)


def test_factory_playwright_custom_storage(tmp_path) -> None:
    from src.browser.playwright_adapter import PlaywrightBrowserAdapter

    state = tmp_path / "yandex_storage.json"
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        browser_adapter="playwright",
        kwork_storage_state=str(tmp_path / "kwork_storage.json"),
        _env_file=None,
    )
    client = get_browser_client(settings, storage_state_path=str(state))
    assert isinstance(client, PlaywrightBrowserAdapter)
    assert client._storage_state_path == str(state)


def test_browser_client_protocol() -> None:
    assert isinstance(CursorBrowserAdapter(), BrowserClient)
