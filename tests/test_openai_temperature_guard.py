"""Tests for the retry guard around models that reject `temperature`."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from src.analyzer import openai_compat
from src.analyzer.gpt_offer_estimator import GptOfferEstimator
from src.analyzer.gpt_scorer import GptScorer
from src.analyzer.openai_compat import post_chat
from src.analyzer.response_pipeline import ResponsePipeline
from src.analyzer.response_qa import ResponseQaValidator
from src.config import Settings
from src.models import ProjectFull

TEMPERATURE_ERROR = {
    "error": {
        "message": (
            "Unsupported value: 'temperature' does not support 0.82 with this model. "
            "Only the default (1) value is supported."
        ),
        "type": "invalid_request_error",
        "param": "temperature",
        "code": "unsupported_value",
    }
}

OTHER_ERROR = {
    "error": {
        "message": "Unknown parameter: 'foo'.",
        "type": "invalid_request_error",
        "param": "foo",
        "code": "unknown_parameter",
    }
}


@pytest.fixture(autouse=True)
def _clear_guard_cache() -> Any:
    openai_compat._NO_TEMPERATURE_MODELS.clear()
    yield
    openai_compat._NO_TEMPERATURE_MODELS.clear()


def _settings(**overrides: str) -> Settings:
    return Settings(
        openai_api_key="k",
        openai_base_url="https://api.example.com/openai/v1",
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
        project_id="77",
        url="https://kwork.ru/projects/77",
        title="Telegram-бот",
        full_description=(
            "Нужен Telegram-бот на aiogram для приёма заявок без онлайн-оплаты, "
            "заявки уходят менеджеру в чат, нужна простая админка."
        ),
        desired_budget="от 20000",
        offers_count=5,
    )


def _response(status: int, payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=MagicMock(), response=MagicMock()
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _content(payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"choices": [{"message": {"content": text}}]}


def _client(*responses: MagicMock) -> MagicMock:
    client = MagicMock(spec=httpx.Client)
    client.post.side_effect = list(responses)
    return client


def _bodies(client: MagicMock) -> list[dict[str, Any]]:
    return [call.kwargs["json"] for call in client.post.call_args_list]


def test_post_chat_retries_once_without_temperature() -> None:
    client = _client(
        _response(400, TEMPERATURE_ERROR),
        _response(200, _content("ok")),
    )
    body = {"model": "gpt-5.5", "messages": [], "temperature": 0.82}

    response = post_chat(client, "https://api.example.com/v1/chat/completions", headers={}, body=body)

    assert response.status_code == 200
    assert client.post.call_count == 2
    first, second = _bodies(client)
    assert first["temperature"] == 0.82
    assert "temperature" not in second
    assert second["model"] == "gpt-5.5"
    assert body["temperature"] == 0.82


def test_post_chat_skips_temperature_for_known_model() -> None:
    first_client = _client(
        _response(400, TEMPERATURE_ERROR),
        _response(200, _content("ok")),
    )
    post_chat(
        first_client,
        "https://api.example.com/v1/chat/completions",
        headers={},
        body={"model": "gpt-5-nano", "messages": [], "temperature": 0.82},
    )

    second_client = _client(_response(200, _content("ok")))
    post_chat(
        second_client,
        "https://api.example.com/v1/chat/completions",
        headers={},
        body={"model": "gpt-5-nano", "messages": [], "temperature": 0.82},
    )

    assert second_client.post.call_count == 1
    assert "temperature" not in _bodies(second_client)[0]


def test_post_chat_does_not_retry_other_400() -> None:
    client = _client(_response(400, OTHER_ERROR))

    response = post_chat(
        client,
        "https://api.example.com/v1/chat/completions",
        headers={},
        body={"model": "gpt-4o-mini", "messages": [], "temperature": 0.2},
    )

    assert client.post.call_count == 1
    assert response.status_code == 400
    assert "gpt-4o-mini" not in openai_compat._NO_TEMPERATURE_MODELS
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()


def test_pipeline_retries_without_temperature() -> None:
    client = _client(
        _response(400, TEMPERATURE_ERROR),
        _response(200, _content({"verdict": "pass", "issues": [], "missing": []})),
    )
    pipe = ResponsePipeline(_settings(openai_model_logic="gpt-5.5"), http_client=client)

    result = pipe._critique_logic("Здравствуйте! Сделаю бота.", _project())

    assert result["verdict"] == "pass"
    first, second = _bodies(client)
    assert first["temperature"] == 0.1
    assert "temperature" not in second
    assert second["model"] == "gpt-5.5"


def test_pipeline_other_400_still_raises() -> None:
    client = _client(_response(400, OTHER_ERROR))
    pipe = ResponsePipeline(_settings(), http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        pipe._critique_logic("Здравствуйте! Сделаю бота.", _project())
    assert client.post.call_count == 1


def test_scorer_retries_without_temperature() -> None:
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
    client = _client(
        _response(400, TEMPERATURE_ERROR),
        _response(200, _content(score_payload)),
    )
    scorer = GptScorer(_settings(openai_model_score="gpt-5.5"), http_client=client)

    result = scorer.score(_project(), "ctx")

    assert result.score == 8
    first, second = _bodies(client)
    assert first["temperature"] == 0.15
    assert "temperature" not in second


def test_estimator_retries_without_temperature() -> None:
    client = _client(
        _response(400, TEMPERATURE_ERROR),
        _response(200, _content({"price_rub": 20000, "delivery_days": 7})),
    )
    estimator = GptOfferEstimator(
        _settings(openai_model_estimate="gpt-5.5"), http_client=client
    )

    terms = estimator.estimate(_project(), "Здравствуйте! Сделаю бота.")

    assert terms.price_rub > 0
    first, second = _bodies(client)
    assert first["temperature"] == 0.2
    assert "temperature" not in second


def test_qa_retries_without_temperature() -> None:
    client = _client(
        _response(400, TEMPERATURE_ERROR),
        _response(
            200,
            _content({"aligned": True, "issues": [], "redundant_questions": []}),
        ),
    )
    validator = ResponseQaValidator(
        _settings(openai_model_qa="gpt-5.5"), http_client=client
    )

    result = validator.validate(_project(), "Здравствуйте! Сделаю бота за 7 дней.")

    assert result["aligned"] is True
    first, second = _bodies(client)
    assert first["temperature"] == 0.1
    assert "temperature" not in second
