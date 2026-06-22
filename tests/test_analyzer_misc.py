from __future__ import annotations

import pytest

from src.analyzer.lightrag_client import LightRagClient
from src.limits.daily import count_today_responses, is_daily_limit_reached
from src.telegram_bot.bot import parse_callback_data


def test_lightrag_injectable_search() -> None:
    calls: list[tuple[str, str]] = []

    def search_fn(query: str, mode: str) -> str:
        calls.append((query, mode))
        return f"ctx:{mode}"

    client = LightRagClient(search_fn=search_fn)
    ctx = client.get_full_context()
    assert "ctx:mix" in ctx
    assert len(calls) == 2


def test_parse_callback_data() -> None:
    parsed = parse_callback_data("approve:kwork:kwork_dev_it:123")
    assert parsed == ("approve", "kwork", "kwork_dev_it", "123")
    assert parse_callback_data("bad") is None


def test_daily_limit_empty_journal(tmp_path) -> None:
    path = tmp_path / "empty.xlsx"
    assert count_today_responses(path) == 0
    assert is_daily_limit_reached(path, 5) is False
