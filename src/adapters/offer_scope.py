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


def suggest_delivery_days_for_scope(
    project: ProjectFull,
    *,
    volume: int | None = None,
) -> int | None:
    """Volume-calibrated Kwork delivery days; None → keep pipeline default."""
    text = f"{project.title or ''}\n{project.full_description or ''}"
    parsing = is_parsing_task(text)
    vol = int(volume or 0)
    if not parsing and vol <= 0:
        return None
    images = project_wants_images(project)
    if vol >= LARGE_CATALOG_ITEMS:
        raw = 5 if images else 4
    elif vol >= MEDIUM_CATALOG_ITEMS:
        raw = 4 if images else 3
    elif vol > 0 or parsing:
        raw = 3 if images else 2
    else:
        return None
    return snap_delivery_days(raw)
