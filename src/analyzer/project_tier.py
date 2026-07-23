"""Треки отбора: standard, quick_win (мелкие быстрые), experience_win (500–1000 ₽ за опыт)."""
from __future__ import annotations

import re

from src.adapters.kwork_pricing import _budget_amounts
from src.analyzer.landing_case import LANDING_PATTERN
from src.analyzer.project_brief import build_project_brief
from src.config import Settings
from src.models import GptScoreResult, ProjectFull

_QUICK_WIN_TASK_RE = re.compile(
    r"парс\w*|скрап\w*|скрин\w*|gmail|imap|почт\w*|выгруз\w*|"
    r"скачать|скрипт|автоматиз|csv|excel|xlsx|"
    r"быстр\w*|прост\w*|небольш\w*|разово|настро\w*|"
    r"telegram[- ]?бот|бот на python|aiogram|"
    + LANDING_PATTERN,
    re.I,
)

_OUT_OF_STACK_RE = re.compile(
    r"android|ios|swift|kotlin|flutter|1с|wordpress|тильда|"
    r"дизайн без|верстк[аи]\s+без|штатн|pdf|пдф|photoshop|figma\s+без",
    re.I,
)

_NATIVE_MOBILE_RE = re.compile(
    r"android|ios|swift|kotlin|flutter|нативн\w*\s+(прилож|app)",
    re.I,
)


def max_listed_budget_rub(project: ProjectFull) -> int | None:
    amounts = _budget_amounts(project)
    return max(amounts) if amounts else None


def is_out_of_stack(project: ProjectFull) -> bool:
    brief = build_project_brief(project)
    if not brief:
        return True
    if _OUT_OF_STACK_RE.search(brief):
        return True
    if _NATIVE_MOBILE_RE.search(brief):
        return True
    return False


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
    if is_out_of_stack(project):
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


def is_experience_win_budget(project: ProjectFull, settings: Settings) -> bool:
    max_budget = max_listed_budget_rub(project)
    if max_budget is None:
        return False
    return (
        settings.experience_win_min_budget_rub
        <= max_budget
        <= settings.experience_win_max_budget_rub
    )


def is_experience_win_candidate(project: ProjectFull, settings: Settings) -> bool:
    """Микробюджет 500–1000 ₽: главное совпадение со стеком, не заработок."""
    if not settings.experience_win_enabled:
        return False
    if not is_experience_win_budget(project, settings):
        return False
    if is_out_of_stack(project):
        return False
    return True


def passes_experience_win_gate(
    project: ProjectFull, score: GptScoreResult, settings: Settings
) -> bool:
    if not is_experience_win_candidate(project, settings):
        return False
    if int(score.score) < settings.experience_win_min_score:
        return False
    skills = [s.strip() for s in (score.matched_skills or []) if str(s).strip()]
    return len(skills) >= settings.experience_win_min_matched_skills


def resolve_acceptance_tier(
    project: ProjectFull, score: GptScoreResult, settings: Settings
) -> str | None:
    """standard → quick_win → experience_win (если предыдущие не прошли)."""
    if passes_standard_gate(score, settings):
        return "standard"
    if passes_quick_win_gate(project, score, settings):
        return "quick_win"
    if passes_experience_win_gate(project, score, settings):
        return "experience_win"
    return None


def tier_label(tier: str | None) -> str:
    if tier == "quick_win":
        return "⚡ Быстрый заказ"
    if tier == "experience_win":
        return "🎯 За опыт"
    return ""
