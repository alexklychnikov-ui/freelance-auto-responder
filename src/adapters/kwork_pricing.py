from __future__ import annotations

import re

from src.models import ProjectFull

MIN_OFFER_PRICE_RUB = 500
DESIRED_GAP_RATIO = 1.2
LISTED_PRICE_MIX = 0.15


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


def parse_desired_budget_rub(project: ProjectFull) -> int | None:
    """Желаемый бюджет из desired_budget only (title digits ignored).

    «до N» → N; otherwise min of amounts in that field (от N / single / range).
    """
    raw = (project.desired_budget or "").replace("\u00a0", " ")
    amounts = [a for a in _parse_amounts(raw) if a >= MIN_OFFER_PRICE_RUB]
    if not amounts:
        return None
    if re.search(r"\bдо\b", raw, flags=re.IGNORECASE):
        return max(amounts)
    return min(amounts)


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


def pick_listed_offer_price(
    project: ProjectFull,
    *,
    fair: int = 0,
    form_min: int | None = None,
    form_max: int | None = None,
) -> int:
    """Price in the order corridor, near желаемый — never default to допустимый max."""
    desired = parse_desired_budget_rub(project)
    amounts = _budget_amounts(project)
    hi = parse_budget_ceiling_rub(project)
    lo = desired if desired is not None else (min(amounts) if amounts else None)
    if lo is None and hi is None:
        base = 8000
    else:
        if lo is None:
            lo = hi
        if hi is None:
            hi = lo
        if lo is None or hi is None:
            base = 8000
        else:
            if lo > hi:
                lo = hi
            if fair > 0:
                target_hi = min(int(fair), hi)
            else:
                target_hi = lo
            if target_hi <= lo:
                base = lo
            else:
                base = int(lo + (target_hi - lo) * LISTED_PRICE_MIX)
    return clamp_price_to_budget(
        int(base), project, form_min=form_min, form_max=form_max
    )


def budget_gap(
    fair_price: int,
    project: ProjectFull,
    *,
    multiplier: float = 1.0,
    form_max: int | None = None,
) -> dict | None:
    """Soft gap when fair is materially above желаемый or above допустимый.

    ``gap["ceiling"]`` = customer-facing listed budget we stay in (desired
    if present, else допустимый / form_max). ``gap["fill_price"]`` = listed
    corridor price (near desired), not max ceiling. ``max_ceiling`` = допустимый.
    ``form_max`` is a fallback ceiling only when project has no listed ceiling.
    """
    desired = parse_desired_budget_rub(project)
    project_ceiling = parse_budget_ceiling_rub(project)
    max_ceiling = project_ceiling
    if max_ceiling is None and form_max is not None:
        max_ceiling = int(form_max)
    listed = desired if desired is not None else max_ceiling
    fair = int(fair_price or 0)
    if listed is None or fair <= 0:
        return None
    above_desired = (
        desired is not None and fair > int(desired * DESIRED_GAP_RATIO)
    )
    above_max = (
        max_ceiling is not None
        and fair > int(max_ceiling * float(multiplier))
    )
    if not above_desired and not above_max:
        return None
    fill = pick_listed_offer_price(
        project, fair=fair, form_max=form_max
    )
    return {
        "ceiling": int(listed),
        "fair_price": fair,
        "fill_price": int(fill),
        "ratio": round(fair / listed, 4),
        "form_max": int(form_max) if form_max is not None else None,
        "project_ceiling": (
            int(project_ceiling) if project_ceiling is not None else None
        ),
        "max_ceiling": int(max_ceiling) if max_ceiling is not None else None,
        "desired": int(desired) if desired is not None else None,
    }


def format_budget_mismatch_sentence(gap: dict) -> str:
    fair = format_rub_amount(int(gap["fair_price"]))
    listed = int(gap.get("fill_price") or gap["ceiling"])
    fill = format_rub_amount(listed)
    return (
        f"В бюджете заказа ({fill} ₽) сделаю основной сценарий по ТЗ. "
        f"Полный объём — ориентир от {fair} ₽, если захотите расширить."
    )


_SCOPE_NOTE_RE = re.compile(
    r"основн\w+\s+сценари|"
    r"полн\w+\s+объ[её]м|"
    r"если\s+захотите\s+расширить|"
    r"в\s+бюджет\w*\s+заказ",
    flags=re.IGNORECASE,
)

_ZANIZHEN_RE = re.compile(r"занижен", flags=re.IGNORECASE)


def response_has_budget_discuss_note(text: str) -> bool:
    return bool(_SCOPE_NOTE_RE.search(text or ""))


def ensure_budget_mismatch_note(text: str, gap: dict | None) -> str:
    """Append deterministic scope sentence if gap present and note missing."""
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
    """Local checks: listed asking price + scope note; ban «занижен» / fair-only."""
    if not gap:
        return []
    issues: list[str] = []
    body = text or ""
    compact = re.sub(r"[\s\u00a0]+", "", body)
    fair = int(gap["fair_price"])
    fill = int(gap.get("fill_price") or gap["ceiling"])
    fair_compact = re.sub(r"\s+", "", format_rub_amount(fair))
    fill_compact = re.sub(r"\s+", "", format_rub_amount(fill))
    has_fill = str(fill) in compact or fill_compact in compact
    has_fair = str(fair) in compact or fair_compact in compact
    has_note = response_has_budget_discuss_note(body)
    if _ZANIZHEN_RE.search(body):
        issues.append("budget_mismatch:zanizhen_strategy")
    if not has_note:
        issues.append("budget_mismatch:no_scope_note")
    asks_fair_only = (
        has_fair
        and not has_fill
        and bool(
            re.search(
                rf"(?:стоим\w*|цен\w*|бюджет)\D{{0,20}}{re.escape(fair_compact)}",
                compact,
                flags=re.IGNORECASE,
            )
        )
    )
    if asks_fair_only:
        issues.append("budget_mismatch:fair_as_asking_price")
    elif not has_fill:
        issues.append("budget_mismatch:no_listed_price")
    return issues


def suggest_offer_price(
    project: ProjectFull,
    *,
    form_min: int | None = None,
    form_max: int | None = None,
) -> str:
    return str(
        pick_listed_offer_price(
            project, form_min=form_min, form_max=form_max
        )
    )
