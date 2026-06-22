from __future__ import annotations

import re

from src.models import ProjectFull

_SOURCE_RE = re.compile(
    r"\b(linkedin|kwork|avito|telegram|instagram|facebook|hh\.ru|habr)\b",
    re.IGNORECASE,
)
_TASK_RE = re.compile(
    r"(парс\w*|скрап\w*|собира\w*|выгруз\w*|мониторинг|бот\w*|парсер)",
    re.IGNORECASE,
)
_TARGET_RE = re.compile(
    r"(ссылк\w*|публикац\w*|пост\w*|цен\w*|контакт\w*|email|телефон)",
    re.IGNORECASE,
)


def build_project_brief(project: ProjectFull) -> str:
    title = (project.title or "").strip()
    desc = (project.full_description or "").strip()
    if title and desc and title.lower() not in desc.lower()[: max(len(title), 20)]:
        return f"{title}\n\n{desc}"
    if desc:
        return desc
    return title


def extract_tz_facts(project: ProjectFull) -> list[str]:
    text = build_project_brief(project)
    if not text:
        return []
    facts: list[str] = []
    if _TASK_RE.search(text):
        facts.append("В ТЗ указана задача парсинга/сбора данных")
    for match in _SOURCE_RE.finditer(text):
        facts.append(f"Источник данных в ТЗ: {match.group(1)}")
    if _TARGET_RE.search(text):
        m = _TARGET_RE.search(text)
        if m:
            facts.append(f"Целевые данные в ТЗ упомянуты ({m.group(1)})")
    if len(text) >= 80:
        facts.append(f"Суть заказа (цитата): {text[:240].strip()}")
    return facts


def task_is_clear(project: ProjectFull) -> bool:
    text = build_project_brief(project)
    if len(text) < 25:
        return False
    has_task = bool(_TASK_RE.search(text))
    has_source_or_target = bool(_SOURCE_RE.search(text) or _TARGET_RE.search(text))
    return has_task and (has_source_or_target or len(text) >= 60)


def tz_is_vague(project: ProjectFull) -> bool:
    return not task_is_clear(project)
