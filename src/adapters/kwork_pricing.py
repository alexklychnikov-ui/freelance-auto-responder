from __future__ import annotations

import re

from src.models import ProjectFull

MIN_OFFER_PRICE_RUB = 500


def apply_competitive_price(
    price: int,
    factor: float = 0.8,
    *,
    min_price: int = MIN_OFFER_PRICE_RUB,
) -> int:
    """Scale price down for competitiveness; round to nearest 100; keep floor."""
    scaled = int(round(int(price or 0) * float(factor) / 100) * 100)
    return max(int(min_price), scaled)


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
    """Верхний предел оплаты (допустимый бюджет Kwork), ₽.

    Prefer max_budget («Допустимый») only. Fall back to desired «до X»
    when max is missing — never mix both fields into one ceiling.
    """
    amounts = _parse_amounts(project.max_budget)
    amounts = [a for a in amounts if a >= MIN_OFFER_PRICE_RUB]
    if amounts:
        return max(amounts)
    # Listing/card often stores «до X ₽» only in desired_budget.
    desired = project.desired_budget or ""
    if re.search(r"\bдо\b", desired.replace("\u00a0", " "), flags=re.IGNORECASE):
        amounts = [a for a in _parse_amounts(desired) if a >= MIN_OFFER_PRICE_RUB]
        if amounts:
            return max(amounts)
    return None


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
    # Hard ceiling: max_budget field only (desired may be higher / misleading).
    ceiling = parse_budget_ceiling_rub(project)
    if ceiling is not None:
        result = min(result, ceiling)
    if form_min is not None:
        result = max(form_min, result)
    if form_max is not None:
        result = min(form_max, result)
    # form_max / ceiling can pull below platform min — keep Kwork-valid floor when possible
    if form_max is not None and form_max < MIN_OFFER_PRICE_RUB:
        return max(1, form_max)
    if result < MIN_OFFER_PRICE_RUB:
        result = MIN_OFFER_PRICE_RUB
        if form_max is not None:
            result = min(result, form_max)
        if ceiling is not None:
            result = min(result, ceiling)
    return result


def format_rub_amount(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def pick_commercial_price(market: int, offer: int) -> int:
    """One commercial figure from market fair and offer-terms price.

    Prefer the more competitive (lower) when both estimates are positive;
    otherwise take the nonzero side.
    """
    m = int(market or 0)
    o = int(offer or 0)
    if m > 0 and o > 0:
        return min(m, o)
    return m if m > 0 else max(0, o)


def budget_gap(
    fair_price: int,
    project: ProjectFull,
    *,
    multiplier: float = 1.0,
    form_max: int | None = None,
) -> dict | None:
    """Soft gap when fair estimate exceeds listed budget ceiling.

    Returns None if no ceiling or fair_price is within ceiling * multiplier.
    Prefer meaningful gap: fair > ceiling (multiplier default 1.0).

    ``gap["ceiling"]`` / TG «потолок» = project допустимый (or desired «до X»).
    ``form_max`` is a fallback ceiling only when project has no listed ceiling —
    never override допустимый with a tighter form band from желаемый.
    Form fill still clamps via ``clamp_price_to_budget(..., form_max=...)``.
    """
    project_ceiling = parse_budget_ceiling_rub(project)
    ceiling = project_ceiling
    if ceiling is None and form_max is not None:
        ceiling = int(form_max)
    fair = int(fair_price or 0)
    if ceiling is None or fair <= 0:
        return None
    threshold = int(ceiling * float(multiplier))
    if fair <= threshold:
        return None
    return {
        "ceiling": ceiling,
        "fair_price": fair,
        # fill_price for messaging = project ceiling; form may clamp lower separately
        "fill_price": ceiling,
        "ratio": round(fair / ceiling, 4),
        "form_max": int(form_max) if form_max is not None else None,
        "project_ceiling": int(project_ceiling) if project_ceiling is not None else None,
    }


def format_budget_mismatch_sentence(gap: dict) -> str:
    fair = format_rub_amount(int(gap["fair_price"]))
    ceiling = format_rub_amount(int(gap["ceiling"]))
    return (
        f"По объёму работ ориентир — от {fair} ₽; "
        f"указанный в заказе бюджет ({ceiling} ₽) для такого объёма выглядит заниженным. "
        f"Предлагаю обсудить сумму под ваш результат."
    )


_DISCUSS_PRICE_RE = re.compile(
    r"обсудить\s+(?:сумм\w*|цен\w*|бюджет\w*|стоим\w*)|"
    r"выглядит\s+занижен|"
    r"бюджет[^\n.]{0,40}занижен",
    flags=re.IGNORECASE,
)


def response_has_budget_discuss_note(text: str) -> bool:
    return bool(_DISCUSS_PRICE_RE.search(text or ""))


def ensure_budget_mismatch_note(text: str, gap: dict | None) -> str:
    """Append deterministic soft sentence if gap present and discuss CTA missing."""
    if not gap:
        return text
    body = (text or "").rstrip()
    if response_has_budget_discuss_note(body):
        return body
    note = format_budget_mismatch_sentence(gap)
    if not body:
        return note
    return f"{body}\n\n{note}"


def budget_mismatch_issues(text: str, gap: dict | None) -> list[str]:
    """Local checks when budget_mismatch is set: need fair price + discuss CTA."""
    if not gap:
        return []
    issues: list[str] = []
    body = text or ""
    compact = re.sub(r"[\s\u00a0]+", "", body)
    fair = int(gap["fair_price"])
    ceiling = int(gap["ceiling"])
    fair_compact = re.sub(r"\s+", "", format_rub_amount(fair))
    ceiling_compact = re.sub(r"\s+", "", format_rub_amount(ceiling))
    has_fair = str(fair) in compact or fair_compact in compact
    has_discuss = response_has_budget_discuss_note(body)
    # «Стоимость — от {ceiling}» without fair looks like accepting tiny budget
    echoes_ceiling_only = (
        not has_fair
        and ceiling_compact in compact
        and bool(
            re.search(
                rf"(?:стоим\w*|цен\w*|бюджет)\D{{0,20}}{re.escape(ceiling_compact)}",
                compact,
                flags=re.IGNORECASE,
            )
        )
    )
    if echoes_ceiling_only and not has_discuss:
        issues.append("budget_mismatch:ceiling_as_price_no_discuss")
    elif not has_discuss:
        issues.append("budget_mismatch:no_discuss_cta")
    elif not has_fair:
        issues.append("budget_mismatch:no_fair_price")
    return issues


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
