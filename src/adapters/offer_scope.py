"""Scope-based offer price mix and delivery days from evidence volume."""
from __future__ import annotations

import re
from typing import Any

from src.adapters.kwork_delivery import snap_delivery_days
from src.adapters.kwork_pricing import LISTED_PRICE_MIX
from src.analyzer.project_brief import is_parsing_task
from src.models import ProjectFull

_SITEMAP_ITEMS_RE = re.compile(
    r"(?:из\s+них|страниц[- ]элементов)[^\d]{0,24}(\d[\d\s]{0,8}\d|\d+)",
    re.IGNORECASE,
)
_SITEMAP_TOTAL_RE = re.compile(
    r"(?:в\s+sitemap|sitemap)[^\d]{0,24}(\d[\d\s]{0,8}\d|\d+)\s*URL",
    re.IGNORECASE,
)
_IMAGES_RE = re.compile(
    r"изображен|картинк|выкач\w*\s+изобр|скачиван\w*\s+изобр|отдельн\w*\s+папк",
    re.IGNORECASE,
)

_INTEGRATION_RE = re.compile(
    r"интеграц|api\b|crm|ocr|документ|микросервис|автоматизац",
    re.IGNORECASE,
)
_MANY_MODULES_RE = re.compile(
    r"нескольк\w+\s+модул|много\s+модул|\d+\s*[-–—]?\s*модул|"
    r"набор\s+модул|комплексн\w+\s+интеграц|микросервис",
    re.IGNORECASE,
)
_TELEGRAM_BOT_RE = re.compile(
    r"telegram[- ]?бот|телеграм[- ]?бот|\btg[- ]?бот\b",
    re.IGNORECASE,
)
_LANDING_WEB_RE = re.compile(
    r"landing|лендинг|сайт|веб[- ]?mvp|web[- ]?mvp|одностранич",
    re.IGNORECASE,
)

LARGE_CATALOG_ITEMS = 3000
MEDIUM_CATALOG_ITEMS = 500
LARGE_PRICE_MIX = 0.65
MEDIUM_PRICE_MIX = 0.40


def _parse_int(raw: str) -> int | None:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    return int(digits)


def catalog_item_count_from_evidence(evidence: Any | None) -> int | None:
    """Best-effort item/page count from evidence fact claims (recon sitemap)."""
    if evidence is None:
        return None
    facts = getattr(evidence, "facts", None) or []
    best: int | None = None
    for fact in facts:
        claim = str(getattr(fact, "claim", "") or "")
        if not claim:
            continue
        m = _SITEMAP_ITEMS_RE.search(claim)
        if m:
            n = _parse_int(m.group(1))
            if n is not None and n > 0:
                best = n if best is None else max(best, n)
                continue
        m = _SITEMAP_TOTAL_RE.search(claim)
        if m:
            n = _parse_int(m.group(1))
            if n is not None and n > 0:
                best = n if best is None else max(best, n)
    return best


def project_wants_images(project: ProjectFull) -> bool:
    text = f"{project.title or ''}\n{project.full_description or ''}"
    return bool(_IMAGES_RE.search(text))


def listed_price_mix_for_scope(
    project: ProjectFull,
    *,
    volume: int | None = None,
) -> float:
    """Higher mix → listed price closer to fair (still capped by ceiling)."""
    parsing = is_parsing_task(
        f"{project.title or ''}\n{project.full_description or ''}"
    )
    vol = int(volume or 0)
    if vol >= LARGE_CATALOG_ITEMS or (parsing and vol >= MEDIUM_CATALOG_ITEMS):
        return LARGE_PRICE_MIX
    if vol >= MEDIUM_CATALOG_ITEMS or parsing:
        return MEDIUM_PRICE_MIX
    return LISTED_PRICE_MIX


def _default_days_for_task_type(text: str) -> int:
    if _TELEGRAM_BOT_RE.search(text):
        return 5
    if _LANDING_WEB_RE.search(text) and not _INTEGRATION_RE.search(text):
        return 5
    if _INTEGRATION_RE.search(text):
        if _MANY_MODULES_RE.search(text):
            return 10
        return 7
    return 7


def suggest_delivery_days_for_scope(
    project: ProjectFull,
    *,
    volume: int | None = None,
) -> int:
    """Volume/type-calibrated Kwork delivery days (always a snapped int)."""
    text = f"{project.title or ''}\n{project.full_description or ''}"
    vol = int(volume or 0)
    if vol > 0:
        images = project_wants_images(project)
        if vol >= LARGE_CATALOG_ITEMS:
            raw = 5 if images else 4
        elif vol >= MEDIUM_CATALOG_ITEMS:
            raw = 4 if images else 3
        else:
            raw = 3 if images else 2
        return snap_delivery_days(raw)
    return snap_delivery_days(_default_days_for_task_type(text))
