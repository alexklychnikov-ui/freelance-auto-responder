"""Дополнительный трек quick_win поверх стандартного отбора (крупные заказы не трогаем)."""
from __future__ import annotations

import re

from src.adapters.kwork_pricing import _budget_amounts
from src.analyzer.project_brief import build_project_brief
from src.config import Settings
from src.models import GptScoreResult, ProjectFull

_QUICK_WIN_TASK_RE = re.compile(
    r"парс\w*|скрап\w*|скрин\w*|gmail|imap|почт\w*|выгруз\w*|"
    r"скачать|скрипт|автоматиз|csv|excel|xlsx|"
    r"быстр\w*|прост\w*|небольш\w*|разово|настро\w*|"
    r"telegram[- ]?бот|бот на python|aiogram",
    re.I,
)

_OUT_OF_STACK_RE = re.compile(
    r"android|ios|swift|kotlin|flutter|1с|wordpress|тильда|"
    r"дизайн без|верстк[аи]\s+без|штатн",
    re.I,
)

_NATIVE_MOBILE_RE = re.compile(
    r"android|ios|swift|kotlin|flutter|нативн\w*\s+(прилож|app)",
    re.I,
)


def max_listed_budget_rub(project: ProjectFull) -> int | None:
    amounts = _budget_amounts(project)
    return max(amounts) if amounts else None


def passes_standard_gate(score: GptScoreResult, settings: Settings) -> bool:
    """Прежние условия: fit + score >= MIN_GPT_SCORE."""
    return bool(score.fit) and int(score.score) >= settings.min_gpt_score


def is_quick_win_candidate(project: ProjectFull, settings: Settings) -> bool:
    """Мелкий быстрый заказ по признакам ТЗ/бюджета (без учёта GPT fit)."""
    if not settings.quick_win_enabled:
        return False

    brief = build_project_brief(project)
    if not brief or not _QUICK_WIN_TASK_RE.search(brief):
        return False
    if _OUT_OF_STACK_RE.search(brief):
        return False
    if _NATIVE_MOBILE_RE.search(brief):
        return False

    max_budget = max_listed_budget_rub(project)
    if max_budget is None or max_budget > settings.quick_win_max_budget_rub:
        return False

    offers = project.offers_count or 0
    if offers > settings.quick_win_max_offers_count:
        return False

    return True


def passes_quick_win_gate(
    project: ProjectFull, score: GptScoreResult, settings: Settings
) -> bool:
    """Дополнительный проход: мелкий заказ + score >= QUICK_WIN_MIN_SCORE."""
    if not is_quick_win_candidate(project, settings):
        return False
    return int(score.score) >= settings.quick_win_min_score


def resolve_acceptance_tier(
    project: ProjectFull, score: GptScoreResult, settings: Settings
) -> str | None:
    """standard — старые правила; quick_win — только если standard не прошёл."""
    if passes_standard_gate(score, settings):
        return "standard"
    if passes_quick_win_gate(project, score, settings):
        return "quick_win"
    return None


def tier_label(tier: str | None) -> str:
    if tier == "quick_win":
        return "⚡ Быстрый заказ"
    return ""
