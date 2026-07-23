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


def test_strip_hallucinated_irina_when_no_buyer() -> None:
    project = _project("Нужен парсер hh.ru")
    project = project.model_copy(update={"buyer": None})
    text = (
        "Ирина, здравствуйте! Предлагаю связать API hh.ru с ChatGPT. "
        "Сделаю поиск и анализ резюме. Срок — 14 дней, стоимость — от 120000 руб. "
        "Дайте знать, с чего удобнее начать."
    )
    out = finalize_response_text(text, project)
    assert not out.lower().startswith("ирина")
    assert "\n\n" in out
    assert "hh.ru" in out


def test_keep_real_buyer_greeting() -> None:
    project = _project("бот").model_copy(update={"buyer": "Ирина · 80%"})
    text = "Ирина, здравствуйте! Сделаю бота под заявки. Срок — 5 дней."
    out = finalize_response_text(text, project)
    assert out.startswith("Ирина, здравствуйте!")


def test_buyer_first_name_rejects_ui_noise() -> None:
    from src.analyzer.response_text import buyer_first_name

    assert buyer_first_name("Чаты") is None
    assert buyer_first_name("Ирина · 80%") == "Ирина"
