from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.adapters.flru import (
    FlruAdapter,
    parse_listing_from_html,
    parse_project_from_html,
)
from src.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
PID = "5514795"


def test_parse_listing_from_html_fixture() -> None:
    html = (FIXTURES / "flru_listing.html").read_text(encoding="utf-8")
    cards = parse_listing_from_html(html)
    ids = {c["project_id"] for c in cards}
    assert PID in ids
    assert "5514779" in ids


def test_parse_project_from_html_fixture() -> None:
    html = (FIXTURES / "flru_project.html").read_text(encoding="utf-8")
    raw = parse_project_from_html(html, project_id=PID)
    assert "озон" in raw["title"].lower() or "парсинг" in raw["title"].lower()
    assert "озон" in (raw["full_description"] or "").lower()


def test_submit_response_manual_only() -> None:
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        flru_storage_state="data/flru_storage.json",
        _env_file=None,
    )
    adapter = FlruAdapter(
        source_key="flru_orders",
        listing_url="https://www.fl.ru/projects/?kind=1",
        settings=settings,
        browser=MagicMock(),
    )
    result = adapter.submit_response(PID, "text", "500")
    assert result.success is False
    assert "manual_only" in (result.message or "")
