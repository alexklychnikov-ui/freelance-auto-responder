"""Tests for DraftWriter vertical accent hints."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.analyzer.response_pipeline import ResponsePipeline
from src.analyzer.response_vertical import (
    VERTICAL_AUTOMATION_AI,
    VERTICAL_GENERAL,
    VERTICAL_INTEGRATION,
    VERTICAL_PARSING,
    VERTICAL_TELEGRAM_AI,
    VERTICAL_WEB_MVP,
    resolve_response_vertical,
    vertical_hints_for,
)
from src.config import Settings
from src.models import ProjectFull


def _settings() -> Settings:
    return Settings(
        openai_api_key="sk-test",
        openai_model="gpt-4o-mini",
        default_offer_days=5,
    )


def _proj(title: str, description: str) -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="v1",
        url="https://kwork.ru/projects/v1",
        title=title,
        full_description=description,
        desired_budget="от 15000",
    )


def test_resolve_prefers_suggested_project_type() -> None:
    project = _proj("Любая тема", "Нужен парсинг цен с сайта")
    assert (
        resolve_response_vertical(project, "Telegram-бот") == VERTICAL_TELEGRAM_AI
    )
    assert resolve_response_vertical(project, {"suggested_project_type": "Веб-MVP"}) == (
        VERTICAL_WEB_MVP
    )


def test_resolve_keywords_telegram_aiogram() -> None:
    assert (
        resolve_response_vertical(
            _proj("Заказ", "Нужен бот на aiogram для заявок")
        )
        == VERTICAL_TELEGRAM_AI
    )


def test_resolve_keywords_do_not_match_rabota() -> None:
    # «работа» contains substring «бот» — must not become telegram_ai
    assert (
        resolve_response_vertical(
            _proj("Работа по Excel", "Нужна доработка отчёта в таблице")
        )
        == VERTICAL_AUTOMATION_AI
    )


def test_resolve_keywords_automation_rag() -> None:
    assert (
        resolve_response_vertical(
            _proj("RAG по документам", "Классификация PDF и Excel через LLM")
        )
        == VERTICAL_AUTOMATION_AI
    )


def test_resolve_keywords_integration() -> None:
    assert (
        resolve_response_vertical(
            _proj("Связка CRM", "Интеграция API и webhook в PostgreSQL")
        )
        == VERTICAL_INTEGRATION
    )


def test_resolve_keywords_web_mvp() -> None:
    assert (
        resolve_response_vertical(
            _proj("Лендинг", "Одностраничник на Next.js с формой")
        )
        == VERTICAL_WEB_MVP
    )


def test_resolve_keywords_parsing() -> None:
    assert (
        resolve_response_vertical(
            _proj("Каталог", "Парсинг каталога цен, выгрузка CSV")
        )
        == VERTICAL_PARSING
    )


def test_resolve_general_fallback() -> None:
    assert (
        resolve_response_vertical(_proj("Консультация", "Нужен совет по архитектуре"))
        == VERTICAL_GENERAL
    )
    assert "ТЗ" in vertical_hints_for(VERTICAL_GENERAL)


def test_build_draft_payload_includes_vertical_hints_for_telegram() -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    project = _proj(
        "Telegram-бот",
        "Нужен Telegram-бот на aiogram для заявок без онлайн-оплаты.",
    )
    payload = pipe._build_draft_payload(project, "ctx")
    assert payload["vertical"] == VERTICAL_TELEGRAM_AI
    hints = payload["vertical_hints"]
    assert isinstance(hints, str)
    assert "aiogram" in hints.lower() or "сценарии" in hints
    assert "заявк" in hints.lower()


def test_build_draft_payload_uses_explicit_project_type() -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    project = _proj("Разное", "Обычный заказ без явных маркеров вертикали")
    payload = pipe._build_draft_payload(
        project, "ctx", project_type="Интеграция"
    )
    assert payload["vertical"] == VERTICAL_INTEGRATION
    assert "идемпотент" in payload["vertical_hints"].lower()
