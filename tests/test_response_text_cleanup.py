from __future__ import annotations

from src.adapters.kwork import _strip_stage_title_leakage
from src.analyzer.response_text import (
    finalize_response_text,
    payment_mismatch_issues,
    strip_github_links,
    strip_portfolio_footer,
    tz_requires_lead_only,
)
from src.models import ProjectFull


def _project(desc: str) -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Telegram-магазин",
        full_description=desc,
    )


def test_strip_stage_title_leakage_from_start() -> None:
    stages = [
        ("Анализ ТЗ и реализация основной части", 11000),
        ("Тестирование, правки и передача проекта", 9000),
    ]
    raw = (
        "Тестирование, правки и передача проекта"
        "Анализ ТЗ и реализация основной части"
        "Изучив ваш проект, готов взяться."
    )
    cleaned = _strip_stage_title_leakage(raw, stages)
    assert cleaned.startswith("Изучив")


def test_strip_github_and_portfolio_footer() -> None:
    text = (
        "Кейс по боту.\n"
        "GitHub: github.com/alexklychnikov-ui (https://github.com/alexklychnikov-ui).\n"
        "Портфолио: https://portfolio.hayklyvibelexy.ru/"
    )
    out = finalize_response_text(text)
    assert "github" not in out.lower()
    assert "portfolio.hayklyvibelexy.ru" not in out


def test_payment_mismatch_when_tz_lead_only() -> None:
    project = _project("оформление заявки без онлайн-оплаты; заявка менеджеру")
    assert tz_requires_lead_only(project)
    issues = payment_mismatch_issues(
        project,
        "Настрою интеграцию с API платёжных систем.",
    )
    assert issues == ["tz:payment_not_required"]


def test_strip_portfolio_footer_only() -> None:
    text = "Готов обсудить.\n\nПортфолио: https://portfolio.hayklyvibelexy.ru/"
    assert strip_portfolio_footer(text) == "Готов обсудить."


def test_strip_github_links() -> None:
    text = "См. github.com/alexklychnikov-ui и https://github.com/foo/bar"
    assert strip_github_links(text) == "См. и"
