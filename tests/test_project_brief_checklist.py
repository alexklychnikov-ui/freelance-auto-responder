from __future__ import annotations

from src.analyzer.project_brief import (
    buyer_checklist_issues,
    extract_buyer_checklist,
    extract_tz_facts,
)
from src.models import ProjectFull


def _bots_project() -> ProjectFull:
    desc = """
Доработать 2 Telegram-бота по готовому ТЗ
Проект №1: внутренний бот для учета контактов
Проект №2: клиентский бот поддержки
При отклике укажите:
1. Стоимость.
2. Срок.
3. На чем будете разрабатывать.
4. Готовы ли посмотреть текущий код.
5. Что будет входить в итоговую передачу проекта.
"""
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3204427",
        url="https://kwork.ru/projects/3204427/view",
        title="Доработать 2 Telegram-бота по готовому ТЗ",
        full_description=desc,
        desired_budget="до 35 000 ₽",
        max_budget="до 105 000 ₽",
    )


def test_two_bots_not_marked_as_parsing() -> None:
    project = _bots_project()
    facts = extract_tz_facts(project)
    assert any("Telegram-бот" in f for f in facts)
    assert not any("парсинга" in f for f in facts)


def test_extract_buyer_checklist() -> None:
    items = extract_buyer_checklist(_bots_project())
    assert len(items) == 5
    assert "Стоимость" in items[0]


def test_buyer_checklist_issues_detects_missing() -> None:
    bad = "Задача по парсингу Telegram понятна. Готов ответить в чате Kwork."
    issues = buyer_checklist_issues(_bots_project(), bad)
    assert "checklist:стек" in issues
    assert "checklist:код" in issues


def test_buyer_checklist_issues_ok() -> None:
    ok = (
        "Стоимость: 42000 ₽. Срок: 14 дн. Стек: Python, aiogram. "
        "Готов посмотреть текущий код и наработки. "
        "Передача: исходники, база, инструкция по запуску."
    )
    assert not buyer_checklist_issues(_bots_project(), ok)
