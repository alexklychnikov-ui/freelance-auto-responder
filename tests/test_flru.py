from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.adapters.flru import (
    FlruAdapter,
    is_flru_project_closed,
    parse_listing_from_html,
    parse_project_from_html,
)
from src.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
PID = "5514795"


def test_is_flru_project_closed_phrases() -> None:
    assert is_flru_project_closed("Заказчик выбрал исполнителя: Никита Петров")
    assert is_flru_project_closed("Исполнитель определён")
    assert is_flru_project_closed("Исполнитель определен")
    assert not is_flru_project_closed("Откликнуться на проект")


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


def _make_adapter(**kwargs) -> FlruAdapter:
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        flru_storage_state="data/flru_storage.json",
        _env_file=None,
    )
    defaults = {
        "source_key": "flru_orders",
        "listing_url": "https://www.fl.ru/projects/?kind=1",
        "settings": settings,
        "browser": MagicMock(),
        "filters": {"for_all": True, "skip_closed": True},
    }
    defaults.update(kwargs)
    return FlruAdapter(**defaults)


def test_submit_response_manual_only() -> None:
    adapter = _make_adapter()
    result = adapter.submit_response(PID, "text", "500")
    assert result.success is False
    assert "manual_only" in (result.message or "")


def test_listing_url_appends_for_all() -> None:
    adapter = _make_adapter()
    assert adapter._listing_url() == "https://www.fl.ru/projects/?kind=1&for_all=1"


def test_scan_new_ensures_for_all_checkbox() -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        "https://www.fl.ru/projects/?kind=1&for_all=1",  # _ensure_logged_in
        {"ok": True, "clicked": True},  # _ensure_for_all_filter
        [{"project_id": PID, "url": f"https://www.fl.ru/projects/{PID}/", "title": "t"}],
    ]
    adapter = _make_adapter(browser=browser)
    cards = adapter.scan_new()
    assert len(cards) == 1
    assert cards[0].project_id == PID
    browser.navigate.assert_called_once_with(
        "https://www.fl.ru/projects/?kind=1&for_all=1"
    )
    assert browser.evaluate.call_count == 3
