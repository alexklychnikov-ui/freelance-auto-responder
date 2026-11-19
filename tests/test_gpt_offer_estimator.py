from __future__ import annotations

from unittest.mock import MagicMock

from src.analyzer.gpt_offer_estimator import (
    GptOfferEstimator,
    _days_from_response_text,
    _normalize_terms,
)
from src.config import Settings
from src.models import ProjectFull


def test_days_from_response_text() -> None:
    assert _days_from_response_text("Сделаю за 7 дней") == 7
    assert _days_from_response_text("без срока") is None


def test_normalize_terms_applies_competitive_factor_before_clamp() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Site",
        full_description="10 pages",
        desired_budget="8 000 ₽",
        max_budget="до 50 000 ₽",
    )
    terms = _normalize_terms(
        {"price_rub": 28000, "delivery_days": 7, "plan_summary": "ok"},
        project,
        competitive_factor=0.8,
    )
    assert terms.price_rub == 22400


def test_offer_estimator_fallback(tmp_path) -> None:
    settings = Settings(
        openai_api_key="x",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal=str(tmp_path / "journal.xlsx"),
        default_offer_days=14,
        offer_price_competitive_factor=0.8,
        _env_file=None,
    )
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Site",
        full_description="10 pages",
        desired_budget="8 000 ₽",
        max_budget="до 24 000 ₽",
    )
    estimator = GptOfferEstimator(settings, http_client=MagicMock())
    terms = estimator.fallback(project, "Готов за 5 дней")
    # listed price stays at желаемый 8000 (never mid/допустимый);
    # competitive 0.8 → 6400 is lifted back to the желаемый floor by the clamp
    assert terms.price_rub == 8000
    assert terms.delivery_days == 5


def test_offer_estimator_no_budget_keeps_unknown_zero(tmp_path) -> None:
    settings = Settings(
        openai_api_key="x",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal=str(tmp_path / "journal.xlsx"),
        default_offer_days=14,
        offer_price_competitive_factor=0.8,
        _env_file=None,
    )
    project = ProjectFull(
        platform="yandex_uslugi",
        source_key="yandex_orders",
        project_id="f559a5fb",
        url="https://uslugi.yandex.ru/order/f559a5fb",
        title="Интеграция",
        full_description="Настроить интеграцию по ТЗ",
        desired_budget=None,
        max_budget=None,
    )
    estimator = GptOfferEstimator(settings, http_client=MagicMock())

    unknown = estimator.fallback(project, "Сделаю интеграцию за 5 дней.")
    assert unknown.price_rub == 0

    explicit = estimator.fallback(
        project,
        "Стоимость первой версии — 95 000 ₽. Срок — 14 дней.",
    )
    assert explicit.price_rub == 76_000
