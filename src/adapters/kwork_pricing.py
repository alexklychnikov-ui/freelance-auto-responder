from __future__ import annotations

import re

from src.models import ProjectFull


def _parse_amounts(text: str | None) -> list[int]:
    if not text:
        return []
    return [
        int(n.replace(" ", "").replace("\u00a0", ""))
        for n in re.findall(r"\d[\d\s]*", text.replace("\u00a0", " "))
        if n.strip()
    ]


def _budget_amounts(project: ProjectFull) -> list[int]:
    amounts: list[int] = []
    for raw in (project.max_budget, project.desired_budget, project.full_description):
        amounts.extend(_parse_amounts(raw))
    return amounts


def parse_budget_ceiling_rub(project: ProjectFull) -> int | None:
    """Верхний предел оплаты (допустимый бюджет Kwork), ₽."""
    amounts = _parse_amounts(project.max_budget)
    if not amounts:
        return None
    return max(amounts)


def price_exceeds_budget_ceiling(
    estimated_price: int,
    project: ProjectFull,
    *,
    multiplier: float = 2.0,
) -> bool:
    ceiling = parse_budget_ceiling_rub(project)
    if ceiling is None or estimated_price <= 0:
        return False
    return estimated_price > int(ceiling * multiplier)


def clamp_price_to_budget(price: int, project: ProjectFull) -> int:
    amounts = _budget_amounts(project)
    if not amounts:
        return max(500, price)
    max_budget = max(amounts)
    min_budget = min(amounts)
    if len(amounts) >= 2:
        desired = min(amounts)
        capped = min(price, max_budget)
        return max(min_budget, capped) if desired <= max_budget else capped
    return min(price, max_budget) if price > max_budget else max(500, price)


def suggest_offer_price(project: ProjectFull) -> str:
    amounts = _budget_amounts(project)
    if not amounts:
        return "8000"
    if len(amounts) >= 2:
        desired, maximum = min(amounts), max(amounts)
        target = int(desired + (maximum - desired) * 0.6)
        return str(clamp_price_to_budget(target, project))
    value = amounts[0]
    if project.max_budget:
        return str(clamp_price_to_budget(value, project))
    return str(max(value, int(value * 1.2)))
