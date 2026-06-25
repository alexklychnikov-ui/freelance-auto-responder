from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.analyzer.response_history import load_recent_response_context
from src.analyzer.response_strategy import build_generation_strategy
from src.models import GptScoreResult, ProjectFull
from src.responses.prepared_store import PreparedResponse, PreparedResponseStore


def test_build_generation_strategy_varies_by_project() -> None:
    brief = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="100",
        url="https://kwork.ru/projects/100",
        title="Бот",
        full_description="Нужен бот срочно",
    )
    detailed = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="200",
        url="https://kwork.ru/projects/200",
        title="RAG",
        full_description="Python FastAPI " + ("подробное ТЗ " * 80),
    )
    s1 = build_generation_strategy(brief)
    s2 = build_generation_strategy(detailed)
    assert s1["approach"] == "speed"
    assert s1["structure_variant"] == "D"
    assert s2["writing_style"] == "экспертный"
    assert s1["structure_variant"] != s2["structure_variant"] or s1["approach"] != s2["approach"]


def test_load_recent_response_context(tmp_path) -> None:
    store = PreparedResponseStore(tmp_path)
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="A",
        full_description="",
    )
    score = GptScoreResult(
        score=8,
        fit=True,
        reason="ok",
        matched_skills=[],
        risks=[],
        suggested_project_type="Бот",
        competition_level="low",
        recommendation="откликаться",
    )
    store.save(
        PreparedResponse(
            platform="kwork",
            source_key="kwork_dev_it",
            project_id="1",
            url=project.url,
            title=project.title,
            project=project,
            score=score,
            response_text="По задаче с Excel вижу два этапа. Готов обсудить детали.",
            price="5000",
            prepared_at=datetime.now(timezone.utc),
        )
    )
    ctx = load_recent_response_context(store)
    assert ctx["count"] == 1
    assert "Excel" in ctx["recent_openings"][0]


def test_strip_markdown_links() -> None:
    from src.analyzer.response_text import strip_response_markdown

    text = "См. [Price Monitoring](https://github.com/foo/bar)."
    assert "[Price Monitoring]" not in strip_response_markdown(text)
    assert "https://github.com/foo/bar" in strip_response_markdown(text)


def test_kwork_compliance_detects_call() -> None:
    from src.analyzer.response_text import kwork_compliance_issues

    assert "off_platform_call" in kwork_compliance_issues(
        "Давайте созвонимся и обсудим детали!"
    )
    assert not kwork_compliance_issues("Разработаю Telegram-бота для учёта расходов.")


def test_banned_phrase_triggers_retry(monkeypatch) -> None:
    from src.analyzer.gpt_response_generator import GptResponseGenerator
    from src.config import Settings

    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        _env_file=None,
    )
    gen = GptResponseGenerator(settings, http_client=MagicMock())
    calls: list[str] = []

    def fake_call(body, project_id):
        calls.append(body["messages"][1]["content"])
        if len(calls) == 1:
            return "Добрый день! С удовольствием помогу с вашим проектом."
        return "По выгрузке ведомостей из Excel логично начать с формата файла."

    monkeypatch.setattr(gen, "_call_api", fake_call)
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="9",
        url="https://kwork.ru/projects/9",
        title="Excel",
        full_description="выгрузка",
    )
    text = gen.generate(project, "ctx")
    assert len(calls) == 2
    assert "Добрый день" not in text
    retry_payload = json.loads(calls[1])
    assert "banned_detected" in retry_payload
