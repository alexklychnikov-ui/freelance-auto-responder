from __future__ import annotations

import pytest

from src.analyzer.project_tier import (
    is_quick_win_candidate,
    max_listed_budget_rub,
    passes_quick_win_gate,
    passes_standard_gate,
    resolve_acceptance_tier,
)
from src.config import Settings
from src.models import GptScoreResult, ProjectFull


@pytest.fixture
def settings() -> Settings:
    return Settings(
        openai_api_key="k",
        openai_base_url="https://api.example.com",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="journal.xlsx",
        min_gpt_score=7,
        quick_win_enabled=True,
        quick_win_min_score=5,
        quick_win_max_budget_rub=10_000,
        quick_win_max_offers_count=40,
        _env_file=None,
    )


def _score(*, value: int, fit: bool) -> GptScoreResult:
    return GptScoreResult(
        score=value,
        fit=fit,
        reason="test",
        matched_skills=["Python"],
        risks=[],
        suggested_project_type="Парсинг",
        competition_level="low",
        recommendation="откликаться" if fit else "пропустить",
    )


def test_quick_win_candidate_parsing(settings: Settings) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3211980",
        url="https://kwork.ru/projects/3211980/view",
        title="Парсинг сайта",
        full_description="Нужен простой парсер, выгрузка в csv",
        max_budget="до 5 000 ₽",
        offers_count=12,
    )
    assert is_quick_win_candidate(project, settings)
    assert max_listed_budget_rub(project) == 5000


def test_large_order_stays_standard_only(settings: Settings) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="big",
        url="https://kwork.ru/projects/big/view",
        title="Парсинг каталога",
        full_description="Большой парсинг",
        max_budget="до 80 000 ₽",
        offers_count=5,
    )
    score = _score(value=8, fit=True)
    assert not is_quick_win_candidate(project, settings)
    assert resolve_acceptance_tier(project, score, settings) == "standard"
    assert passes_standard_gate(score, settings)


def test_quick_win_additive_when_standard_fails(settings: Settings) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3187946",
        url="https://kwork.ru/projects/3187946/view",
        title="gmail скрины",
        full_description="Скрипт gmail imap, скачать скриншоты",
        max_budget="2 000 ₽",
        offers_count=8,
    )
    low_fit = _score(value=6, fit=False)
    assert not passes_standard_gate(low_fit, settings)
    assert passes_quick_win_gate(project, low_fit, settings)
    assert resolve_acceptance_tier(project, low_fit, settings) == "quick_win"


def test_standard_wins_over_quick_win(settings: Settings) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="q",
        url="https://kwork.ru/projects/q/view",
        title="Парсинг",
        full_description="Небольшой парсинг csv",
        max_budget="4 000 ₽",
        offers_count=3,
    )
    good = _score(value=8, fit=True)
    assert resolve_acceptance_tier(project, good, settings) == "standard"


def test_quick_win_candidate_landing(settings: Settings) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3215930",
        url="https://kwork.ru/projects/3215930/view",
        title="Создать одностраничный сайт по ТЗ",
        full_description="Лендинг, адаптив, форма",
        max_budget="до 8 000 ₽",
        offers_count=10,
    )
    assert is_quick_win_candidate(project, settings)
    low = _score(value=6, fit=False)
    assert resolve_acceptance_tier(project, low, settings) == "quick_win"
