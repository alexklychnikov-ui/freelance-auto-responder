from __future__ import annotations

import re

from src.models import ProjectFull


def _parse_amounts(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(n) for n in re.findall(r"\d[\d\s]*", text.replace("\u00a0", " ")) if n.strip()]


def suggest_offer_price(project: ProjectFull) -> str:
    amounts: list[int] = []
    for raw in (project.max_budget, project.desired_budget):
        amounts.extend(_parse_amounts(raw))
    if not amounts:
        return "5000"
    if len(amounts) >= 2:
        return str(max(amounts))
    value = amounts[0]
    if project.max_budget:
        return str(value)
    return str(max(value, int(value * 1.5)))
