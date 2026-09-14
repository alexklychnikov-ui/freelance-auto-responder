"""Tests for per-role OpenAI model routing."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.analyzer.response_pipeline import ResponsePipeline
from src.config import Settings
from src.models import ProjectFull


def _settings(**overrides: str) -> Settings:
    return Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        _env_file=None,
        **overrides,
    )


def _project() -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="42",
        url="https://kwork.ru/projects/42",
        title="Telegram-бот",
        full_description="Нужен Telegram-бот на aiogram для заявок без онлайн-оплаты.",
        desired_budget="от 20000",
    )


def _mock_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    client.post.return_value = response
    return client


def test_model_for_falls_back_to_openai_model() -> None:
    settings = _settings()
    for role in ("draft", "logic", "expert", "revise", "score", "evidence", "escalation"):
        assert settings.model_for(role) == "gpt-4o-mini"
    assert settings.model_for("unknown_role") == "gpt-4o-mini"


def test_model_for_uses_per_role_override() -> None:
    settings = _settings(
        openai_model_draft="  gpt-5  ",
        openai_model_logic="o3-mini",
        openai_model_score="   ",
    )
    assert settings.model_for("draft") == "gpt-5"
    assert settings.model_for("DRAFT") == "gpt-5"
    assert settings.model_for("logic") == "o3-mini"
    assert settings.model_for("score") == "gpt-4o-mini"
    assert settings.model_for("expert") == "gpt-4o-mini"


def test_pipeline_roles_send_their_own_models() -> None:
    settings = _settings(
        openai_model_draft="gpt-5",
        openai_model_logic="o3-mini",
        openai_model_expert="gpt-5-pro",
        openai_model_revise="gpt-4.1",
    )

    draft_client = _mock_client(
        "Здравствуйте! Сделаю Telegram-бота под заявки менеджеру.\n"
        "Срок — 5–7 дней. Стоимость — от 20 000 ₽.\n"
        "Если подход ок — напишите, согласуем старт."
    )
    pipe = ResponsePipeline(settings, http_client=draft_client)
    pipe._draft(_project(), "ctx")
    assert draft_client.post.call_args.kwargs["json"]["model"] == "gpt-5"

    logic_client = _mock_client('{"verdict": "pass", "issues": [], "missing": []}')
    pipe = ResponsePipeline(settings, http_client=logic_client)
    pipe._critique_logic("Здравствуйте! Сделаю бота.", _project())
    assert logic_client.post.call_args.kwargs["json"]["model"] == "o3-mini"

    expert_client = _mock_client('{"verdict": "pass", "score": 9, "must_fix": []}')
    pipe = ResponsePipeline(settings, http_client=expert_client)
    pipe._expert_review("Здравствуйте! Сделаю бота.", {"verdict": "pass"}, _project())
    assert expert_client.post.call_args.kwargs["json"]["model"] == "gpt-5-pro"

    revise_client = _mock_client("Исправленный отклик.")
    pipe = ResponsePipeline(settings, http_client=revise_client)
    pipe.revise(_project(), "Старый отклик.", "добавь срок")
    assert revise_client.post.call_args.kwargs["json"]["model"] == "gpt-4.1"


def test_pipeline_without_overrides_uses_default_model() -> None:
    client = _mock_client('{"verdict": "pass", "issues": [], "missing": []}')
    pipe = ResponsePipeline(_settings(), http_client=client)
    pipe._critique_logic("Здравствуйте! Сделаю бота.", _project())
    assert client.post.call_args.kwargs["json"]["model"] == "gpt-4o-mini"
