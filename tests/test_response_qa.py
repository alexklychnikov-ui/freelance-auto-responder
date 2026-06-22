from __future__ import annotations

from src.analyzer.project_brief import (
    build_project_brief,
    extract_tz_facts,
    task_is_clear,
)
from src.analyzer.response_qa import rule_check_alignment
from src.analyzer.response_strategy import build_generation_strategy
from src.models import ProjectFull


def _project(title: str, desc: str = "", pid: str = "1") -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id=pid,
        url=f"https://kwork.ru/projects/{pid}",
        title=title,
        full_description=desc,
    )


def test_linkedin_parser_clear_task() -> None:
    project = _project(
        "Нужно сделать парсер",
        "который будет собирать ссылки на публикации в Linkedin",
    )
    assert task_is_clear(project)
    strategy = build_generation_strategy(project)
    assert strategy["approach"] in ("solution", "understanding")
    assert strategy["approach"] != "questions"
    facts = extract_tz_facts(project)
    assert any("LinkedIn" in f or "linkedin" in f.lower() for f in facts)


def test_rule_flags_redundant_parser_question() -> None:
    project = _project(
        "Парсер LinkedIn",
        "собирать ссылки на публикации",
    )
    bad = "Хочу уточнить, какие именно данные вы планируете парсить и откуда?"
    issues = rule_check_alignment(project, bad)
    assert issues


def test_rule_allows_auth_question() -> None:
    project = _project(
        "Парсер LinkedIn",
        "собирать ссылки на публикации",
    )
    ok = (
        "Понял задачу: парсер ссылок на публикации LinkedIn. "
        "Уточните, нужна ли авторизация в аккаунт или публичные страницы?"
    )
    assert not rule_check_alignment(project, ok)


def test_brief_merges_title_and_description() -> None:
    project = _project("Нужно сделать парсер", "собирать ссылки в Linkedin")
    brief = build_project_brief(project)
    assert "парсер" in brief.lower()
    assert "linkedin" in brief.lower()
