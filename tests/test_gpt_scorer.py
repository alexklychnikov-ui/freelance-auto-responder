from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from src.analyzer.gpt_scorer import GptScorer, load_scoring_system_prompt
from src.config import Settings
from src.models import GptScoreResult, ProjectFull


@pytest.fixture
def settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.example.com/openai/v1",
        openai_model="gpt-4o-mini",
        telegram_bot_token="token",
        telegram_chat_id="1",
        response_journal="data/test.xlsx",
        _env_file=None,
    )


@pytest.fixture
def project() -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3201949",
        url="https://kwork.ru/projects/3201949",
        title="Telegram-бот",
        full_description="Python aiogram bot",
        desired_budget="5000",
        offers_count=5,
    )


def test_load_scoring_system_prompt() -> None:
    prompt = load_scoring_system_prompt()
    assert "Александра Клычниковова" in prompt
    assert "не подходит" in prompt.lower() or "не подходит" in prompt
    assert "мобильн" in prompt.lower() or "android" in prompt.lower()
    assert len(prompt) > 500
    assert "risks" in prompt


def test_score_guardrails_native_mobile() -> None:
    from src.analyzer.gpt_scorer import _normalize_score_payload

    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Проект айро",
        full_description="веб-версия сайта и два мобильных приложения Android и iOS",
        desired_budget="20000",
    )
    raw = {
        "score": 10,
        "fit": True,
        "reason": "Отлично подходит",
        "matched_skills": ["Python", "Android"],
        "risks": [
            "Отсутствие четкого описания проекта может привести к недопониманию.",
            "Неизвестный бюджет может ограничить возможности реализации.",
        ],
        "suggested_project_type": "Веб-MVP",
        "competition_level": "medium",
        "recommendation": "откликаться",
    }
    data = _normalize_score_payload(raw, project)
    assert data["score"] <= 4
    assert data["fit"] is False
    assert data["recommendation"] == "пропустить"
    assert not any("неизвестный бюджет" in r.lower() for r in data["risks"])
    assert any("android" in r.lower() or "мобил" in r.lower() for r in data["risks"])


def test_gpt_scorer_parses_response(settings: Settings, project: ProjectFull) -> None:
    score_payload = {
        "score": 8,
        "fit": True,
        "reason": "Подходит под стек",
        "matched_skills": ["Python", "aiogram"],
        "risks": [],
        "suggested_project_type": "Telegram-бот",
        "competition_level": "medium",
        "recommendation": "откликаться",
    }
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(score_payload)}}]
    }

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_response

    scorer = GptScorer(settings, http_client=mock_client)
    result = scorer.score(project, "lightrag context")

    assert isinstance(result, GptScoreResult)
    assert result.score == 8
    assert result.fit is True
    assert result.recommendation == "откликаться"

    call_kwargs = mock_client.post.call_args
    assert "chat/completions" in call_kwargs[0][0]
    headers = call_kwargs[1]["headers"]
    assert headers["Authorization"] == "Bearer test-key"


def test_gpt_scorer_parses_json_codeblock(
    settings: Settings, project: ProjectFull
) -> None:
    score_payload = {
        "score": 5,
        "fit": False,
        "reason": "Не подходит",
        "matched_skills": [],
        "risks": ["низкий бюджет"],
        "suggested_project_type": "Другое",
        "competition_level": "high",
        "recommendation": "пропустить",
    }
    content = f"```json\n{json.dumps(score_payload)}\n```"
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_response

    scorer = GptScorer(settings, http_client=mock_client)
    result = scorer.score(project, "")

    assert result.fit is False
    assert result.score == 5
