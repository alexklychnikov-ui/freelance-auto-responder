from __future__ import annotations

import re

from src.models import ProjectFull

_SOURCE_RE = re.compile(
    r"\b(linkedin|kwork|avito|instagram|facebook|hh\.ru|habr)\b",
    re.IGNORECASE,
)
_PARSE_TASK_RE = re.compile(
    r"(парс\w*|скрап\w*|собира\w+|выгруж\w+|мониторинг\s+цен|парсер)",
    re.IGNORECASE,
)
_BOT_TASK_RE = re.compile(r"telegram[- ]?бот|бот на python|aiogram|телеграм[- ]?бот", re.I)
_TARGET_RE = re.compile(
    r"(ссылк\w*|публикац\w*|пост\w*|цен\w*|контакт\w*|email|телефон)",
    re.IGNORECASE,
)
_CHECKLIST_HEADER_RE = re.compile(r"при отклике укажите", re.I)
_CHECKLIST_ITEM_RE = re.compile(r"^\s*\d+[\.\):\-]\s*(.+)", re.MULTILINE)
_MAX_NUMBERED_ITEMS = 15


def build_project_brief(project: ProjectFull) -> str:
    title = (project.title or "").strip()
    desc = (project.full_description or "").strip()
    if title and desc and title.lower() not in desc.lower()[: max(len(title), 20)]:
        return f"{title}\n\n{desc}"
    if desc:
        return desc
    return title


def _extract_numbered_block(text: str, *, start_at: int = 0) -> list[str]:
    """First contiguous numbered list in text[start_at:], up to _MAX_NUMBERED_ITEMS."""
    section = text[start_at:] if start_at else text
    items: list[str] = []
    started = False
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            if items:
                break
            continue
        match = _CHECKLIST_ITEM_RE.match(stripped)
        if match:
            started = True
            items.append(match.group(1).strip().rstrip("."))
            if len(items) >= _MAX_NUMBERED_ITEMS:
                break
            continue
        if started:
            break
    return items


def extract_numbered_questions(project: ProjectFull) -> list[str]:
    """All consecutive numbered items from the first numbered block in the brief."""
    text = build_project_brief(project)
    if not text:
        return []
    return _extract_numbered_block(text)


def extract_buyer_questions(project: ProjectFull) -> list[str]:
    """Buyer Q&A items: after checklist header if present, else first numbered block."""
    text = build_project_brief(project)
    if not text:
        return []
    header = _CHECKLIST_HEADER_RE.search(text)
    if header:
        return _extract_numbered_block(text, start_at=header.end())
    return extract_numbered_questions(project)


def extract_buyer_checklist(project: ProjectFull) -> list[str]:
    text = build_project_brief(project)
    if not _CHECKLIST_HEADER_RE.search(text):
        return []
    start = _CHECKLIST_HEADER_RE.search(text)
    if not start:
        return []
    return _extract_numbered_block(text, start_at=start.end())


def buyer_checklist_issues(project: ProjectFull, response: str) -> list[str]:
    items = extract_buyer_checklist(project)
    if not items:
        return []
    resp = response.lower()
    issues: list[str] = []
    for item in items:
        low = item.lower()
        if re.search(r"стоимост|цен[аеу]", low):
            if not re.search(r"стоимост|цен[аеу]|₽|руб", resp):
                issues.append("checklist:стоимость")
        elif "срок" in low:
            if not re.search(r"срок|\d+\s*(?:дн|дня|дней|рабоч)", resp):
                issues.append("checklist:срок")
        elif re.search(r"на чем|чем будете|стек|технолог", low):
            if not re.search(r"python|aiogram|fastapi|postgresql|sqlite|стек|разрабатыва", resp):
                issues.append("checklist:стек")
        elif "код" in low:
            if not re.search(r"наработк|код|репозитор|аудит|посмотр|оценю текущ", resp):
                issues.append("checklist:код")
        elif re.search(r"передач|входит|итог", low):
            if not re.search(
                r"передач|исходник|инструкц|баз[аы]|документ|запуск|деплой",
                resp,
            ):
                issues.append("checklist:передача")
    return issues


def extract_tz_facts(project: ProjectFull) -> list[str]:
    text = build_project_brief(project)
    if not text:
        return []
    facts: list[str] = []
    if _PARSE_TASK_RE.search(text):
        facts.append("В ТЗ указана задача парсинга/сбора данных")
    elif _BOT_TASK_RE.search(text):
        facts.append("В ТЗ указана разработка Telegram-бота(ов)")
    for match in _SOURCE_RE.finditer(text):
        facts.append(f"Источник данных в ТЗ: {match.group(1)}")
    if _PARSE_TASK_RE.search(text) and _TARGET_RE.search(text):
        m = _TARGET_RE.search(text)
        if m:
            facts.append(f"Целевые данные в ТЗ упомянуты ({m.group(1)})")
    checklist = extract_buyer_checklist(project) or extract_buyer_questions(project)
    if checklist:
        facts.append(
            "Заказчик просит в отклике явно указать: " + "; ".join(checklist[:6])
        )
    if re.search(
        r"без\s+онлайн[- ]?оплат|оформление\s+заявки\s+без|заявк\w*\s+менеджер",
        text,
        re.I,
    ):
        facts.append(
            "В ТЗ: без онлайн-оплаты в боте — только заявки/контакты менеджеру"
        )
    if len(text) >= 80:
        facts.append(f"Суть заказа (цитата): {text[:280].strip()}")
    return facts


def task_is_clear(project: ProjectFull) -> bool:
    text = build_project_brief(project)
    if len(text) < 25:
        return False
    has_task = bool(
        _PARSE_TASK_RE.search(text)
        or _BOT_TASK_RE.search(text)
        or len(text) >= 60
    )
    has_source_or_target = bool(_SOURCE_RE.search(text) or _TARGET_RE.search(text))
    return has_task and (has_source_or_target or len(text) >= 60)


def tz_is_vague(project: ProjectFull) -> bool:
    return not task_is_clear(project)
