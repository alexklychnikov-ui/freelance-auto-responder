from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.adapters.yandex_uslugi import (
    YandexUslugiAdapter,
    _is_executor_cab_missing,
    _is_login_url,
    parse_listing_from_html,
    parse_order_from_html,
)
from src.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
UUID1 = "11111111-2222-3333-4444-555555555555"
UUID2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_is_login_url_passport() -> None:
    assert _is_login_url("https://passport.yandex.ru/auth")
    assert not _is_login_url("https://uslugi.yandex.ru/cab/orders")


def test_is_executor_cab_missing_registration() -> None:
    assert _is_executor_cab_missing("https://uslugi.yandex.ru/registration")
    assert not _is_executor_cab_missing(
        "https://uslugi.yandex.ru/cab/orders?type=new"
    )
    assert not _is_executor_cab_missing(
        f"https://uslugi.yandex.ru/order/{UUID1}"
    )


def test_parse_listing_from_html_fixture() -> None:
    html = (FIXTURES / "yandex_cab_orders.html").read_text(encoding="utf-8")
    cards = parse_listing_from_html(html)
    ids = {c["project_id"] for c in cards}
    assert UUID1 in ids
    assert UUID2 in ids
    assert len(cards) == 2
    by_id = {c["project_id"]: c for c in cards}
    assert "Telegram" in by_id[UUID1]["title"] or "бот" in by_id[UUID1]["title"].lower()


def test_parse_order_from_html_fixture() -> None:
    html = (FIXTURES / "yandex_order_page.html").read_text(encoding="utf-8")
    raw = parse_order_from_html(html, order_id=UUID1)
    assert "Telegram" in raw["title"] or "бот" in raw["title"].lower()
    assert "aiogram" in (raw["full_description"] or "").lower()
    assert raw["desired_budget"]
    assert "₽" in (raw["desired_budget"] or "")


def test_submit_response_manual_only() -> None:
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        yandex_storage_state="data/yandex_storage.json",
        _env_file=None,
    )
    adapter = YandexUslugiAdapter(
        source_key="yandex_uslugi_it",
        listing_url="https://uslugi.yandex.ru/cab/orders",
        settings=settings,
        browser=MagicMock(),
    )
    result = adapter.submit_response(UUID1, "text", "5000")
    assert result.success is False
    assert "manual_only" in (result.message or "")
