from __future__ import annotations

import re
from typing import Literal, Never

from src.models import ProjectFull

_SOURCE_RE = re.compile(
    r"\b(linkedin|kwork|avito|instagram|facebook|hh\.ru|habr)\b",
    re.IGNORECASE,
)
_PARSE_TASK_RE = re.compile(
    r"(парс\w*|скрап\w*|выгруз\w*|парсер|мониторинг\s+цен|"
    r"(?:собира\w+|собр\w+)\s+(?:данн|товар|каталог|цен|контент|таблиц)|"
    r"(?:xml|json|html).{0,16}(?:extract|извлеч|парс|scrap|дамп|feed|выгруз)|"
    r"(?:extract|извлеч\w*).{0,16}(?:xml|json|html))",
    re.IGNORECASE,
)
_SITE_RECON_TASK_RE = re.compile(
    r"(парс\w*|скрап\w*|выгруж\w*|парсер|"
    r"(?:собира\w+|собр\w+)\s+(?:данн|товар|каталог|цен|контент|таблиц))",
    re.IGNORECASE,
)
_BOT_TASK_RE = re.compile(r"telegram[- ]?бот|бот на python|aiogram|телеграм[- ]?бот", re.I)
_TARGET_RE = re.compile(
    r"(ссылк\w*|публикац\w*|пост\w*|цен\w*|контакт\w*|email|телефон)",
    re.IGNORECASE,
)
_CHECKLIST_HEADER_RE = re.compile(
    r"(?:просьба\s+)?(?:при|в)\s+отклике\s+(?:сразу\s+)?"
    r"(?:"
    r"напишите|ответьте|укажи(?:те)?|указать|"
    r"нужно\s+указать|прошу\s+указать"
    r")",
    re.I,
)
_CHECKLIST_ITEM_RE = re.compile(r"^\s*\d+[\.\):\-]\s*(.+)", re.MULTILINE)
_NUMBERED_MARKER_RE = re.compile(r"\d+[\.\)\:]\s*")
_MAX_NUMBERED_ITEMS = 15
_POST_LAUNCH_ITEM_RE = re.compile(r"расход|после запуска|хостинг|абонент|saas", re.I)
_HOSTING_COST_RE = re.compile(r"хостинг|vps|сервер|домен|подписк|saas|абонент", re.I)
_COST_AMOUNT_RE = re.compile(r"\d|₽|руб|мес")
_STRONG_INCLUSION_RE = re.compile(
    r"\bэтап|исходник|инструкц|деплой|установк",
    re.I,
)
_ANY_INCLUSION_RE = re.compile(
    r"\bэтап|исходник|инструкц|деплой|установк|хост|табел|график|qr",
    re.I,
)
_WATERY_INCLUSION_RE = re.compile(r"основн\w*\s+функц", re.I)

ChecklistRule = Literal[
    "расходы", "входит", "стоимость", "срок", "стек", "код", "передача"
]


def build_project_brief(project: ProjectFull) -> str:
    title = (project.title or "").strip()
    desc = (project.full_description or "").strip()
    if title and desc and title.lower() not in desc.lower()[: max(len(title), 20)]:
        return f"{title}\n\n{desc}"
    if desc:
        return desc
    return title


def _extract_inline_numbered_items(section: str) -> list[str]:
    """Numbered items on one line (optionally after a leading colon)."""
    s = (section or "").strip()
    if not s:
        return []
    if s.startswith(":"):
        s = s[1:].strip()
    # Stay within the first paragraph after the header.
    s = re.split(r"\n\s*\n", s, maxsplit=1)[0].strip()
    matches = list(_NUMBERED_MARKER_RE.finditer(s))
    if not matches or matches[0].start() != 0:
        return []
    items: list[str] = []
    for i, marker in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        cleaned = _clean_checklist_item(s[marker.end() : end])
        if cleaned:
            items.append(cleaned)
        if len(items) >= _MAX_NUMBERED_ITEMS:
            break
    return items


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
        # Same-line list: «: 1. … 2. …» or «1. … 2. …» on one line.
        inline = _extract_inline_numbered_items(stripped)
        if len(inline) >= 2:
            return inline[:_MAX_NUMBERED_ITEMS]
        if not started and inline and re.match(r"^:?\s*\d+[\.\)\:]", stripped):
            items.extend(inline)
            started = True
            if len(items) >= _MAX_NUMBERED_ITEMS:
                break
            continue
        match = _CHECKLIST_ITEM_RE.match(stripped)
        if match:
            started = True
            cleaned = _clean_checklist_item(match.group(1))
            if cleaned:
                items.append(cleaned)
            if len(items) >= _MAX_NUMBERED_ITEMS:
                break
            continue
        if started:
            break
    if items:
        return items
    # Whole header section on one physical line after «напишите:».
    return _extract_inline_numbered_items(section)[:_MAX_NUMBERED_ITEMS]


def _extract_colon_list_items(text: str, *, start_at: int) -> list[str]:
    """Items after «при отклике … указать:» separated by . ; or newlines."""
    section = text[start_at:].strip()
    if section.startswith(":"):
        section = section[1:].strip()
    if not section:
        return []
    if re.match(r"^\s*\d+[\.\):\-]", section):
        numbered = _extract_numbered_block(text, start_at=start_at)
        if numbered:
            return numbered
        return _extract_inline_numbered_items(text[start_at:])[:_MAX_NUMBERED_ITEMS]
    items: list[str] = []
    for part in re.split(r"[.;\n]+", section):
        s = _clean_checklist_item(part)
        if not s:
            if items:
                break
            continue
        if items and len(s) > 120:
            break
        if len(s) >= 3:
            items.append(s)
        if len(items) >= _MAX_NUMBERED_ITEMS:
            break
    return items


def _clean_checklist_item(raw: str) -> str:
    return (raw or "").strip().strip("-*•").rstrip(".,;:(").strip()


def checklist_rule_for_question(item: str) -> ChecklistRule | None:
    low = (item or "").lower()
    if _POST_LAUNCH_ITEM_RE.search(low):
        return "расходы"
    if re.search(r"передач|итог", low):
        return "передача"
    if "входит" in low:
        return "входит"
    if re.search(r"стоимост|цен[аеу]", low):
        return "стоимость"
    if "срок" in low:
        return "срок"
    if re.search(r"на чем|чем будете|стек|технолог", low):
        return "стек"
    if "код" in low:
        return "код"
    return None


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
        numbered = _extract_numbered_block(text, start_at=header.end())
        if numbered:
            return numbered
        colon_items = _extract_colon_list_items(text, start_at=header.end())
        if colon_items:
            return colon_items
    return extract_numbered_questions(project)


def extract_buyer_checklist(project: ProjectFull) -> list[str]:
    text = build_project_brief(project)
    if not _CHECKLIST_HEADER_RE.search(text):
        return []
    return extract_buyer_questions(project)


def _response_covers_post_launch_costs(resp: str) -> bool:
    for part in re.split(r"[.\n]+", resp):
        if _HOSTING_COST_RE.search(part) and _COST_AMOUNT_RE.search(part):
            return True
    return False


def _response_covers_inclusion(resp: str) -> bool:
    has_strong = bool(_STRONG_INCLUSION_RE.search(resp))
    if _WATERY_INCLUSION_RE.search(resp) and not has_strong:
        return False
    return bool(_ANY_INCLUSION_RE.search(resp))


def buyer_checklist_issues(project: ProjectFull, response: str) -> list[str]:
    items = extract_buyer_questions(project)
    if not items:
        return []
    resp = response.lower()
    issues: list[str] = []
    for item in items:
        rule = checklist_rule_for_question(item)
        if rule is None:
            continue
        if rule == "расходы":
            if not _response_covers_post_launch_costs(resp):
                issues.append("checklist:расходы")
        elif rule == "входит":
            if not _response_covers_inclusion(resp):
                issues.append("checklist:входит")
        elif rule == "стоимость":
            if not re.search(r"стоимост|цен[аеу]|₽|руб", resp):
                issues.append("checklist:стоимость")
        elif rule == "срок":
            if not re.search(r"срок|\d+\s*(?:дн|дня|дней|рабоч)", resp):
                issues.append("checklist:срок")
        elif rule == "стек":
            if not re.search(r"python|aiogram|fastapi|postgresql|sqlite|стек|разрабатыва", resp):
                issues.append("checklist:стек")
        elif rule == "код":
            if not re.search(r"наработк|код|репозитор|аудит|посмотр|оценю текущ", resp):
                issues.append("checklist:код")
        elif rule == "передача":
            if not re.search(
                r"передач|исходник|инструкц|баз[аы]|документ|запуск|деплой",
                resp,
            ):
                issues.append("checklist:передача")
        else:
            _exhaustive: Never = rule
            raise ValueError(_exhaustive)
    return issues


def is_parsing_task(text: str) -> bool:
    """True when the TZ text describes parsing/scraping/data collection."""
    return bool(_PARSE_TASK_RE.search(text or ""))


def is_site_recon_task(text: str) -> bool:
    """Narrower gate for website recon: parse/scrape, not «собирать заявки»."""
    return bool(_SITE_RECON_TASK_RE.search(text or ""))


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
