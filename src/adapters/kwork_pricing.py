from __future__ import annotations

import re

from src.models import ProjectFull

MIN_OFFER_PRICE_RUB = 500


def _parse_amounts(text: str | None) -> list[int]:
    if not text:
        return []
    amounts: list[int] = []
    for m in re.finditer(
        r"([\d][\d\s]*)\s*(?:₽|руб\.?)",
        text.replace("\u00a0", " "),
        flags=re.IGNORECASE,
    ):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "")
        if raw.isdigit():
            amounts.append(int(raw))
    if amounts:
        return amounts
    for n in re.findall(r"\d[\d\s]*", text.replace("\u00a0", " ")):
        raw = n.replace(" ", "").replace("\u00a0", "")
        if raw.isdigit():
            amounts.append(int(raw))
    return amounts


def _budget_amounts(project: ProjectFull) -> list[int]:
    amounts: list[int] = []
    for raw in (project.desired_budget, project.max_budget):
        amounts.extend(_parse_amounts(raw))
    return [a for a in amounts if a >= MIN_OFFER_PRICE_RUB]


def parse_budget_ceiling_rub(project: ProjectFull) -> int | None:
    """Верхний предел оплаты (допустимый бюджет Kwork), ₽."""
    amounts = _parse_amounts(project.max_budget)
    amounts = [a for a in amounts if a >= MIN_OFFER_PRICE_RUB]
    if not amounts:
        return None
    return max(amounts)


def parse_form_price_bounds(page_text: str | None) -> tuple[int | None, int | None]:
    if not page_text:
        return None, None
    m = re.search(
        r"от\s*([\d\s]+)\s*руб\W*\s*до\s*([\d\s]+)\s*руб",
        page_text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None, None
    lo = int(m.group(1).replace(" ", "").replace("\u00a0", ""))
    hi = int(m.group(2).replace(" ", "").replace("\u00a0", ""))
    return lo, hi


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


def clamp_price_to_budget(
    price: int,
    project: ProjectFull,
    *,
    form_min: int | None = None,
    form_max: int | None = None,
) -> int:
    amounts = _budget_amounts(project)
    result = max(MIN_OFFER_PRICE_RUB, int(price or 0))
    if amounts:
        max_budget = max(amounts)
        min_budget = min(amounts)
        if len(amounts) >= 2:
            result = min(result, max_budget)
            result = max(min_budget, result)
        else:
            result = min(result, max_budget) if result > max_budget else result
            result = max(MIN_OFFER_PRICE_RUB, result)
    if form_min is not None:
        result = max(form_min, result)
    if form_max is not None:
        result = min(form_max, result)
    return result


def suggest_offer_price(
    project: ProjectFull,
    *,
    form_min: int | None = None,
    form_max: int | None = None,
) -> str:
    amounts = _budget_amounts(project)
    if not amounts:
        base = 8000
    elif len(amounts) >= 2:
        desired, maximum = min(amounts), max(amounts)
        base = int(desired + (maximum - desired) * 0.6)
    else:
        base = amounts[0]
    price = clamp_price_to_budget(
        base, project, form_min=form_min, form_max=form_max
    )
    return str(price)
