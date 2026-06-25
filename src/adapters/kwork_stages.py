from __future__ import annotations

import re

from src.analyzer.project_brief import build_project_brief
from src.models import ProjectFull

MIN_STAGE_RUB = 500


def _split_amounts(total: int, names: list[str], shares: list[float]) -> list[tuple[str, int]]:
    if len(names) < 2:
        raise ValueError("need at least 2 stages")
    if len(names) != len(shares):
        raise ValueError("names/shares length mismatch")
    total = max(sum(MIN_STAGE_RUB for _ in names), int(total))
    amounts = [max(MIN_STAGE_RUB, int(total * share)) for share in shares]
    amounts[-1] += total - sum(amounts)
    return [(name[:70], amount) for name, amount in zip(names, amounts)]


def _needs_three_stages(brief: str) -> bool:
    low = brief.lower()
    if re.search(r"проект\s*№?\s*2", low):
        return True
    if re.search(r"\b2\s+telegram", low):
        return True
    if "два" in low and "бот" in low:
        return True
    if low.count("бот") >= 2:
        return True
    if re.search(r"этап\s*[123]", low) and len(brief) > 200:
        return True
    return False


def plan_offer_stages(total: int, project: ProjectFull | None = None) -> list[tuple[str, int]]:
    """Разбить сумму отклика на этапы оплаты (минимум 2)."""
    brief = build_project_brief(project) if project else ""
    total_rub = max(MIN_STAGE_RUB * 2, int(total or 0))

    if _needs_three_stages(brief):
        low = brief.lower()
        if "контакт" in low and ("поддерж" in low or "клиент" in low):
            names = [
                "Аудит наработок и согласование плана",
                "MVP: бот учёта контактов",
                "MVP: бот поддержки и передача",
            ]
        else:
            names = [
                "Аудит наработок и согласование плана",
                "Реализация основной части по ТЗ",
                "Финальная доработка, тесты и передача",
            ]
        return _split_amounts(total_rub, names, [0.2, 0.45, 0.35])

    return _split_amounts(
        total_rub,
        [
            "Анализ ТЗ и реализация основной части",
            "Тестирование, правки и передача проекта",
        ],
        [0.55, 0.45],
    )
