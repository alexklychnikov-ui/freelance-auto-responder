from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from src.adapters.flru import (
    FlruAdapter,
    is_flru_project_closed,
    parse_listing_from_html,
    parse_project_from_html,
    parse_submitted_offer_from_text,
    read_submitted_offer_text,
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


def test_parse_submitted_offer_from_fixture_text() -> None:
    html = (FIXTURES / "flru_submitted_offer.html").read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"\n{2,}", "\n", text)
    snap = parse_submitted_offer_from_text(text)
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert "40 000" in snap.description or "40000" in snap.description.replace(" ", "")
    assert snap.description.startswith("Иван, здравствуйте!")
    assert "парсер каталога" in snap.description
    assert "Редактировать" not in snap.description
    assert "Отказаться" not in snap.description
    assert "Иван Тестов" not in snap.description
    assert "30.07.2026" not in snap.description
    assert "Чат" not in snap.description
    assert "Другие заказы" not in snap.description
    assert "логотип" not in snap.description
    assert "Статистика откликов" not in snap.description
    assert "до 80 000" not in snap.description


def test_parse_submitted_offer_plain_probe_structure() -> None:
    text = (
        "Нужен бот\nБюджет: от 48 000\nСтатистика откликов\nот 20 000 — до 80 000\n"
        "Ваш отклик\nРедактировать\nОтказаться от заказа\n"
        "Александр Клычников\n30.07.2026 в 15:52\n"
        "Александр, здравствуйте!\n"
        "Сделаю интеграцию под ваш ТЗ. Срок работ устраивает.\n"
        "Стоимость работ от 40 000 ₽. Готов обсудить детали.\n"
        "Срок: 7 дней\nСтоимость работ: 40 000 ₽\n"
        "Чат\nДругие заказы по специализации\nЗаказ про космос\n"
    )
    snap = parse_submitted_offer_from_text(text)
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert "40 000" in snap.description
    assert "Александр, здравствуйте!" in snap.description
    assert "Александр Клычников" not in snap.description
    assert "Другие заказы" not in snap.description
    assert "Статистика откликов" not in snap.description
    assert "космос" not in snap.description
    assert "48 000" not in snap.description


def test_parse_submitted_offer_missing_block() -> None:
    snap = parse_submitted_offer_from_text("Нужен бот без секции отклика")
    assert not snap.ok
    assert snap.error == "block_missing"


def test_read_submitted_offer_text_prefers_inner_text() -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        (
            "Ваш отклик\nРедактировать\nОтказаться от заказа\n"
            "Иван Тестов\n30.07.2026 в 15:52\n"
            "Иван, здравствуйте! Сделаю парсер за неделю, от 40 000 ₽.\n"
            "Срок: 7 дней\nСтоимость работ: 40 000 ₽\nЧат\n"
        ),
    ]
    snap = read_submitted_offer_text(browser, "5515806")
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert "парсер" in snap.description
    assert "Редактировать" not in snap.description
    assert "Иван Тестов" not in snap.description
    browser.navigate.assert_called_once()
    browser.wait_ms.assert_called()


def test_parse_submitted_offer_strips_mashed_chrome() -> None:
    text = (
        "Ваш отклик\n"
        "Редактировать Отказаться от заказа\n"
        "Александр Клычников 30.07.2026 в 15:52 Александр, здравствуйте! "
        "Срок выполнения — 7–10 дней. Стоимость проекта — от 40 000 ₽.\n"
        "Срок: 7 дней Стоимость работ: 40 000 ₽ Чат\n"
    )
    snap = parse_submitted_offer_from_text(text)
    assert snap.ok
    assert snap.price == "40000"
    assert snap.delivery_days == 7
    assert snap.description.startswith("Александр, здравствуйте!")
    assert "Редактировать" not in snap.description
    assert "Стоимость работ" not in snap.description
    assert "Чат" not in snap.description


def test_read_submitted_offer_text_bad_id() -> None:
    snap = read_submitted_offer_text(MagicMock(), "abc")
    assert not snap.ok
    assert snap.error == "bad_project_id"
