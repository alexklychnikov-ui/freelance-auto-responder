"""Tests for multi-agent ResponsePipeline."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.analyzer.project_brief import buyer_checklist_issues
from src.analyzer.response_pipeline import (
    DEFAULT_DRAFT_SYSTEM_PROMPT,
    DEFAULT_EXPERT_REVIEWER_PROMPT,
    DEFAULT_LOGIC_CRITIC_PROMPT,
    DRAFT_SYSTEM_PROMPT,
    EXPERT_REVIEWER_PROMPT,
    LOGIC_CRITIC_PROMPT,
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
from tests.test_project_brief_checklist import (
    _YANDEX_347BC2FC_BAD,
    _YANDEX_347BC2FC_GOLD,
    _yandex_347bc2fc,
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


def _project_non_kwork(*, full_description: str) -> ProjectFull:
    return ProjectFull(
        platform="upwork",
        source_key="upwork_webdev",
        project_id="84",
        url="https://example.com/jobs/84",
        title="Simple API task",
        full_description=full_description,
        desired_budget="1000",
    )


SAMPLE_DRAFT = (
    "Здравствуйте! Сделаю Telegram-бота под заявки менеджеру: каталог, форма и уведомление.\n"
    "Сделаю на aiogram + SQLite, сценарий: заявка уходит менеджеру.\n"
    "Срок — 5–7 дней. Стоимость — от 20 000 ₽.\n"
    "Если подход ок — напишите, согласуем старт."
)


def test_bare_hello_required_named_banned() -> None:
    assert "opener:missing_hello" in soft_banned_issues("Сделаю бота под ваши заявки за 5 дней.")
    bare = "Здравствуйте! Сделаю бота под ваши заявки за 5 дней."
    assert not any(i.startswith("opener:missing") for i in soft_banned_issues(bare))
    named = "Ирина, здравствуйте! Сделаю бота под ваши заявки за 5 дней."
    assert "opener:named_hello_instead_of_bare" in soft_banned_issues(named)
    assert _buyer_first_name("Ирина · 80%") == "Ирина"
    assert _buyer_first_name(None) is None


def test_soft_banned_sobery_opener_and_cta() -> None:
    sobery = soft_banned_issues("Соберу бота под ваши заявки за 5 дней.")
    assert any(i == "opener:^соберу" for i in sobery)
    named_sobery = soft_banned_issues(
        "Здравствуйте! Соберу бота под ваши заявки за 5 дней."
    )
    assert any(i == "opener:^соберу" for i in named_sobery)
    cta = soft_banned_issues(
        "Здравствуйте! Сделаю бота. Срок — 5 дней. Стоимость — от 20 000 ₽. "
        "Предлагаю обсудить детали и приступить."
    )
    assert any("предлагаю обсудить детали и приступить" in i for i in cta)
    clean = soft_banned_issues(
        "Здравствуйте! Сделаю бота под ваши заявки. "
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
    template = "Здравствуйте! Понимаю, что вам требуется бот."
    issues = soft_banned_issues(template)
    assert any("понимаю, что вам" in i for i in issues)
    assert not soft_banned_issues(
        "Здравствуйте! Сделаю Telegram-бота под заявки менеджеру."
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
        price_hint=1500,
        budget_mismatch=gap,
    )
    assert payload["budget_mismatch"] == gap
    assert payload["price_hint"] == 1500
    plain = pipe._build_draft_payload(_project(), "ctx")
    assert "budget_mismatch" not in plain


def test_platform_policy_kwork_preserved_in_payload() -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    payload = pipe._build_draft_payload(_project(), "ctx")
    assert payload["platform"] == "kwork"
    assert payload["platform_policy"]["policy_id"] == "kwork"
    assert any(
        "Здравствуйте" in rule for rule in payload["platform_policy"]["required_rules"]
    )


def test_platform_policy_yandex_has_1000_limit() -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    payload = pipe._build_draft_payload(_yandex_347bc2fc(), "ctx")
    assert payload["platform"] == "yandex_uslugi"
    assert payload["platform_policy"]["policy_id"] == "yandex_uslugi"
    assert payload["platform_policy"]["max_chars"] == 1000


def test_platform_policy_non_kwork_relaxed_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_non_kwork(
        full_description="При отклике прошу указать: срок выполнения и стоимость."
    )
    draft = "Сделаю API-интеграцию. Срок 3 дня. Стоимость 1000."
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        assert user["platform"] == "default"
        assert user["platform_policy"]["policy_id"] == "default"
        assert user["local_issues"] == []
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    result = pipe._critique_logic(draft, project)
    assert result["verdict"] == "pass"


def test_platform_prompt_selection_kwork_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())
    systems_text: list[str] = []
    systems_json: list[str] = []

    def fake_text(*, system: str, user: dict, project_id: str, temperature: float = 0.75):
        systems_text.append(system)
        return SAMPLE_DRAFT

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        systems_json.append(system)
        if "ExpertReviewer" in system or "финальный гейт" in system:
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

    pipe.generate(_project(), "ctx")
    assert systems_text[0] == DRAFT_SYSTEM_PROMPT
    assert systems_json[0] == LOGIC_CRITIC_PROMPT
    assert systems_json[1] == EXPERT_REVIEWER_PROMPT

    systems_text.clear()
    systems_json.clear()
    pipe.generate(
        _project_non_kwork(
            full_description="Нужен API endpoint. При отклике укажите срок и стоимость."
        ),
        "ctx",
    )
    assert systems_text[0] == DEFAULT_DRAFT_SYSTEM_PROMPT
    assert systems_json[0] == DEFAULT_LOGIC_CRITIC_PROMPT
    assert systems_json[1] == DEFAULT_EXPERT_REVIEWER_PROMPT


def test_soft_banned_still_flags_ponimayu_with_budget_gap() -> None:
    template = "Здравствуйте! Понимаю, что вам требуется бот."
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
    short = (
        "Здравствуйте! Сделаю бота. Срок 5 дней. Стоимость от 20 000. Обсудим."
    )
    forced = force_logic_fail_for_questions(short, project, verdict="pass", missing=[])
    assert "too_short_for_all_questions" in forced
    assert "uncovered:1" in forced

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


def test_critique_logic_passes_buyer_questions_colon_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3234444",
        url="https://kwork.ru/projects/3234444/view",
        title="Telegram-бот",
        full_description=(
            "Нужен бот. При отклике прошу указать: "
            "Какие технологии будете использовать. Срок выполнения. Стоимость."
        ),
    )
    bad = "Сделаю бота. Срок 10 дней. Стоимость от 12 000 ₽."
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        assert len(user["buyer_questions"]) == 3
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    result = pipe._critique_logic(bad, project)
    assert result["verdict"] == "fail"
    assert "checklist:стек" in result["missing"]


def test_buyer_questions_uncovered_fail_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="5001",
        url="https://kwork.ru/projects/5001",
        title="Опрос перед стартом",
        full_description=(
            "Перед стартом ответьте:\n"
            "1) Сколько дней займёт работа?\n"
            "2) Какая будет стоимость в рублях?"
        ),
    )
    draft = "Здравствуйте! Могу начать сегодня. Работаю аккуратно и по этапам."
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    result = pipe._critique_logic(draft, project)
    assert result["verdict"] == "fail"
    assert "buyer_q:uncovered:1" in result["issues"]
    assert "checklist:стоимость" in result["missing"]


def test_buyer_questions_covered_no_extra_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="5002",
        url="https://kwork.ru/projects/5002",
        title="Опрос перед стартом",
        full_description=(
            "Перед стартом ответьте:\n"
            "1) Сколько дней займёт работа?\n"
            "2) Какая будет стоимость в рублях?"
        ),
    )
    draft = (
        "Здравствуйте! Работа займет 5 дней, стоимость 20000 рублей. "
        "Соберу MVP и подключу уведомления."
    )
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    result = pipe._critique_logic(draft, project)
    assert "buyer_q:uncovered:1" not in result["issues"]
    assert "buyer_q:uncovered:2" not in result["issues"]


def test_buyer_question_single_shared_word_not_enough_for_long_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="5003",
        url="https://kwork.ru/projects/5003",
        title="Интеграция CRM",
        full_description=(
            "Перед стартом ответьте:\n"
            "1) Какие технологии будете использовать для интеграции с amoCRM?"
        ),
    )
    draft = "Здравствуйте! Для интеграции подготовлю план этапов, срок 5 дней, стоимость 15000 рублей."
    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    result = pipe._critique_logic(draft, project)
    assert result["verdict"] == "fail"
    assert "checklist:стек" in result["missing"]
    assert "buyer_q:uncovered:1" not in result["issues"]


def test_revise_parses_openai_response() -> None:
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "  Исправленный отклик с сроком 5 дней.  "}}]
    }
    mock_client.post.return_value = mock_resp

    pipe = ResponsePipeline(_settings(), http_client=mock_client)
    out = pipe.revise(
        _project(),
        "Старый отклик без срока.",
        "добавь срок 5 дней",
    )
    assert out == "Исправленный отклик с сроком 5 дней."
    body = mock_client.post.call_args.kwargs["json"]
    assert body["messages"][0]["role"] == "system"
    user = json.loads(body["messages"][1]["content"])
    assert user["instruction"] == "добавь срок 5 дней"
    assert user["current_text"] == "Старый отклик без срока."


def test_yandex_347bc2fc_bad_fails_logic_even_if_critic_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _yandex_347bc2fc()
    forced = force_logic_fail_for_questions(
        _YANDEX_347BC2FC_BAD, project, verdict="pass", missing=[]
    )
    assert "checklist:входит" in forced
    assert "checklist:расходы" in forced

    pipe = ResponsePipeline(_settings(), http_client=MagicMock())

    def fake_json(*, system: str, user: dict, project_id: str, temperature: float = 0.2):
        return {
            "verdict": "pass",
            "issues": [],
            "missing": [],
            "style_notes": "ok",
        }

    monkeypatch.setattr(pipe, "_openai_json", fake_json)
    result = pipe._critique_logic(_YANDEX_347BC2FC_BAD, project)
    assert result["verdict"] == "fail"
    assert "checklist:входит" in result["missing"] or any(
        "checklist" in i for i in result["issues"]
    )


def test_yandex_347bc2fc_gold_no_uncovered_checklist() -> None:
    project = _yandex_347bc2fc()
    assert buyer_checklist_issues(project, _YANDEX_347BC2FC_GOLD) == []
    assert len(_YANDEX_347BC2FC_GOLD.strip()) <= 1000
    forced = force_logic_fail_for_questions(
        _YANDEX_347BC2FC_GOLD, project, verdict="pass", missing=[]
    )
    assert forced == []


def test_yandex_over_1000_chars_too_long() -> None:
    project = _yandex_347bc2fc()
    bloated = _YANDEX_347BC2FC_GOLD.strip() + (" подробно" * 40)
    assert len(bloated) > 1000
    forced = force_logic_fail_for_questions(
        bloated, project, verdict="pass", missing=[]
    )
    assert "too_long" in forced

