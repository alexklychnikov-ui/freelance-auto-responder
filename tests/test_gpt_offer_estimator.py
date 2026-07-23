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
    # suggest mid ≈17600 → *0.8 → 14100 (nearest 100)
    assert terms.price_rub == 14100
    assert terms.delivery_days == 5
