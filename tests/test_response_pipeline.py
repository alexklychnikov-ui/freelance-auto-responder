"""Tests for multi-agent ResponsePipeline."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.analyzer.response_pipeline import (
    MSG_DONE,
    MSG_DRAFT,
    MSG_EXPERT,
    MSG_LIMIT,
    MSG_LOGIC,
    MSG_REVISE,
    ResponsePipeline,
    draft_too_short_for_questions,
    force_logic_fail_for_questions,
    soft_banned_issues,
    _buyer_first_name,
)
from src.config import Settings
from src.models import ProjectFull


def _settings() -> Settings:
    return Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        _env_file=None,
    )


def _project(*, buyer: str | None = None) -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="42",
        url="https://kwork.ru/projects/42",
        title="Telegram-бот",
        full_description="Нужен Telegram-бот на aiogram для заявок без онлайн-оплаты.",
        desired_budget="от 20000",
        buyer=buyer,
    )


SAMPLE_DRAFT = (
    "Сделаю Telegram-бота под заявки менеджеру: каталог, форма и уведомление.\n"
    "Сделаю на aiogram + SQLite, сценарий: заявка уходит менеджеру.\n"
    "Срок — 5–7 дней. Стоимость — от 20 000 ₽.\n"
    "Если подход ок — напишите, согласуем старт."
)


def test_named_hello_allowed_bare_banned() -> None:
    assert soft_banned_issues("Здравствуйте! Готов помочь.")
    assert any("opener" in i for i in soft_banned_issues("Здравствуйте! Готов помочь."))
    named = "Ирина, здравствуйте! Сделаю бота под ваши заявки за 5 дней."
    assert not any(i.startswith("opener:") for i in soft_banned_issues(named))
    assert _buyer_first_name("Ирина · 80%") == "Ирина"
    assert _buyer_first_name(None) is None


def test_soft_banned_sobery_opener_and_cta() -> None:
    sobery = soft_banned_issues("Соберу бота под ваши заявки за 5 дней.")
    assert any(i == "opener:^соберу" for i in sobery)
    named_sobery = soft_banned_issues(
        "Ирина, здравствуйте! Соберу бота под ваши заявки за 5 дней."
    )
    assert any(i == "opener:^соберу" for i in named_sobery)
    cta = soft_banned_issues(
        "Сделаю бота. Срок — 5 дней. Стоимость — от 20 000 ₽. "
        "Предлагаю обсудить детали и приступить."
    )
    assert any("предлагаю обсудить детали и приступить" in i for i in cta)
    clean = soft_banned_issues(
        "Ирина, здравствуйте! Сделаю бота под ваши заявки. "
        "Если подход ок — напишите, согласуем старт."
    )
    assert clean == []


def test_soft_banned_docx_prompt_phrases() -> None:
    assert any("задача понятна" in i for i in soft_banned_issues("Задача понятна. Сделаю бота."))
    assert any(
        "по договорённости" in i or "по договоренности" in i
        for i in soft_banned_issues("Сделаю бота. Стоимость — по договорённости.")
    )
    assert any(
        "я специализируюсь" in i
        for i in soft_banned_issues("Я специализируюсь на ботах. Сделаю интеграцию.")
    )


def test_soft_banned_ponimayu_template_opener() -> None:
    template = "Ирина, здравствуйте! Понимаю, что вам требуется бот."
    issues = soft_banned_issues(template)
    assert any("понимаю, что вам" in i for i in issues)
    assert not soft_banned_issues(
        "Ирина, здравствуйте! Сделаю Telegram-бота под заявки менеджеру."
    )


def test_pipeline_happy_path_one_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    texts: list[str] = []
    drafts = 0

    def fake_text(*, system: str, user: dict, project_id: str, temperature: float = 0.75):
        nonlocal drafts
        drafts += 1
        return SAMPLE_DRAFT

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        if "ExpertReviewer" in system:
            return {
                "verdict": "pass",
                "score": 9,
                "feedback": "ok",
                "must_fix": [],
            }
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_text", fake_text)
    monkeypatch.setattr(pipe, "_openai_json", fake_json)

    out = pipe.generate(_project(), "ctx", progress=texts.append)
    assert "Сделаю Telegram-бота" in out
    assert drafts == 1
    assert texts[0] == MSG_DRAFT
    assert MSG_LOGIC in texts
    assert MSG_EXPERT in texts
    assert MSG_DONE in texts


def test_pipeline_critic_fail_then_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    drafts = 0
    logic_calls = 0

    def fake_text(*, system: str, user: dict, project_id: str, temperature: float = 0.75):
        nonlocal drafts
        drafts += 1
        return SAMPLE_DRAFT + f" v{drafts}"

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        nonlocal logic_calls
        if "ExpertReviewer" in system:
            return {
                "verdict": "pass",
                "score": 8,
                "feedback": "ok",
                "must_fix": [],
            }
        logic_calls += 1
        if logic_calls == 1:
            return {
                "verdict": "fail",
                "issues": ["no price"],
                "missing": ["price"],
                "style_notes": "add price",
            }
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "",
        }

    monkeypatch.setattr(pipe, "_openai_text", fake_text)
    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    msgs: list[str] = []
    out = pipe.generate(_project(), "ctx", progress=msgs.append)
    assert drafts == 2
    assert "v2" in out
    assert any("цикл 1/2" in m for m in msgs)


def test_pipeline_expert_revise_then_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    drafts = 0
    expert_calls = 0

    def fake_text(*, system: str, user: dict, project_id: str, temperature: float = 0.75):
        nonlocal drafts
        drafts += 1
        return SAMPLE_DRAFT + f" e{drafts}"

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        nonlocal expert_calls
        if "ExpertReviewer" in system:
            expert_calls += 1
            if expert_calls == 1:
                return {
                    "verdict": "revise_draft",
                    "score": 5,
                    "feedback": "слабый CTA",
                    "must_fix": ["усиль CTA"],
                }
            return {
                "verdict": "pass",
                "score": 9,
                "feedback": "ok",
                "must_fix": [],
            }
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "",
        }

    monkeypatch.setattr(pipe, "_openai_text", fake_text)
    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    out = pipe.generate(_project(), "ctx")
    assert drafts == 2
    assert expert_calls == 2
    assert "e2" in out


def test_pipeline_max_cycles_returns_best(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    drafts = 0

    def fake_text(*, system: str, user: dict, project_id: str, temperature: float = 0.75):
        nonlocal drafts
        drafts += 1
        return SAMPLE_DRAFT + f" lim{drafts}"

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        if "ExpertReviewer" in system:
            return {
                "verdict": "revise_draft",
                "score": 4,
                "feedback": "ещё раз",
                "must_fix": ["fix"],
            }
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "",
        }

    monkeypatch.setattr(pipe, "_openai_text", fake_text)
    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    msgs: list[str] = []
    out = pipe.generate(_project(), "ctx", progress=msgs.append)
    # initial + 2 revision rewrites after expert
    assert drafts == 3
    assert MSG_LIMIT in msgs
    assert "lim" in out


@pytest.mark.asyncio
async def test_generate_with_progress_async(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_text(*, system: str, user: dict, project_id: str, temperature: float = 0.75):
        return SAMPLE_DRAFT

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        if "ExpertReviewer" in system:
            return {
                "verdict": "pass",
                "score": 9,
                "feedback": "",
                "must_fix": [],
            }
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "",
        }

    monkeypatch.setattr(pipe, "_openai_text", fake_text)
    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    msgs: list[str] = []

    async def notify(m: str) -> None:
        msgs.append(m)

    out = await pipe.generate_with_progress(
        _project(buyer="Ирина"), "ctx", notify=notify, threaded=False
    )
    assert out
    assert msgs == [MSG_DRAFT, MSG_LOGIC, MSG_EXPERT, MSG_DONE]


def test_gpt_generator_banned_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.analyzer.gpt_response_generator import GptResponseGenerator

    gen = GptResponseGenerator(_settings(), http_client=MagicMock())
    pipe = gen._pipeline
    drafts: list[str] = []

    def fake_text(*, system: str, user: dict, project_id: str, temperature: float = 0.75):
        drafts.append(json.dumps(user, ensure_ascii=False))
        if len(drafts) == 1 and not user.get("feedback"):
            return "Добрый день! С удовольствием помогу с вашим проектом."
        return SAMPLE_DRAFT

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        if "ExpertReviewer" in system:
            return {
                "verdict": "pass",
                "score": 8,
                "feedback": "",
                "must_fix": [],
            }
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "",
        }

    monkeypatch.setattr(pipe, "_openai_text", fake_text)
    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    text = gen.generate(_project(), "ctx")
    assert "Добрый день" not in text
    assert any("banned_detected" in d for d in drafts)


def test_draft_too_short_for_many_questions() -> None:
    questions = [f"Вопрос {i}?" for i in range(1, 9)]
    assert draft_too_short_for_questions("Короткий ответ.", questions) is True
    long = "x" * 450
    assert draft_too_short_for_questions(long, questions) is False


def test_build_draft_payload_includes_budget_mismatch() -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    gap = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    payload = pipe._build_draft_payload(
        _project(),
        "ctx",
        price_hint=20_000,
        budget_mismatch=gap,
    )
    assert payload["budget_mismatch"] == gap
    assert payload["price_hint"] == 20_000
    plain = pipe._build_draft_payload(_project(), "ctx")
    assert "budget_mismatch" not in plain


def test_soft_banned_still_flags_ponimayu_with_budget_gap() -> None:
    template = "Ирина, здравствуйте! Понимаю, что вам требуется бот."
    assert any("понимаю, что вам" in i for i in soft_banned_issues(template))


def test_critique_force_fail_short_draft_many_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    questions_block = "\n".join(f"{i}. Вопрос номер {i}?" for i in range(1, 9))
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3217391",
        url="https://kwork.ru/projects/3217391",
        title="Бот с вопросами",
        full_description=f"Нужен бот.\n{questions_block}",
        desired_budget="от 20000",
    )
    short = "Сделаю бота. Срок 5 дней. Стоимость от 20 000. Обсудим."
    assert force_logic_fail_for_questions(
        short, project, verdict="pass", missing=[]
    ) == ["too_short_for_all_questions"]

    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        assert "buyer_questions" in user
        assert len(user["buyer_questions"]) >= 5
        assert temperature == 0.1
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    result = pipe._critique_logic(short, project)
    assert result["verdict"] == "fail"
    assert "too_short_for_all_questions" in result["missing"]

