from __future__ import annotations

import hashlib
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
    mix: float | None = None,
) -> int:
    """Price in the order corridor, near желаемый — never default to допустимый max.

    ``mix`` (0..1) walks from желаемый toward min(fair, ceiling).
    Default ``LISTED_PRICE_MIX``; large parse catalogs pass a higher mix.
    """
    price_mix = LISTED_PRICE_MIX if mix is None else max(0.0, min(1.0, float(mix)))
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
                base = int(lo + (target_hi - lo) * price_mix)
    return clamp_price_to_budget(
        int(base), project, form_min=form_min, form_max=form_max
    )


def budget_gap(
    fair_price: int,
    project: ProjectFull,
    *,
    multiplier: float = 1.0,
    form_max: int | None = None,
    mix: float | None = None,
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
        project, fair=fair, form_max=form_max, mix=mix
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


def customer_expansion_hint_rub(gap: dict) -> int | None:
    """Soft customer-facing expansion orientir; hide wild fair outliers."""
    fair = int(gap.get("fair_price") or 0)
    fill = int(gap.get("fill_price") or gap.get("ceiling") or 0)
    max_c_raw = gap.get("max_ceiling")
    max_c = int(max_c_raw) if max_c_raw is not None else None
    if fair <= 0 or fill <= 0:
        return None
    if max_c is not None:
        hint = min(fair, max_c)
        if hint <= int(fill * 1.15):
            return None
        return hint
    if fair > int(fill * 3):
        return None
    return min(fair, int(fill * 2.5))


_MISMATCH_WITH_HINT: tuple[str, ...] = (
    (
        "В бюджете заказа ({fill} ₽) сделаю основной сценарий по ТЗ. "
        "Полный объём — ориентир от {hint} ₽, если захотите расширить."
    ),
    (
        "В указанном бюджете ({fill} ₽) закрою основной сценарий по ТЗ. "
        "Полный объём — ориентир от {hint} ₽, если решите расширить."
    ),
    (
        "В рамках бюджета заказа ({fill} ₽) сделаю MVP по ТЗ. "
        "Полный объём — ориентир от {hint} ₽ при расширении."
    ),
    (
        "На сумму заказа ({fill} ₽) соберу базовый сценарий по ТЗ. "
        "Полный объём — ориентир от {hint} ₽, если захотите расширить."
    ),
    (
        "В бюджете заказа ({fill} ₽) сделаю основной сценарий. "
        "Расширение объёма — ориентир от {hint} ₽."
    ),
    (
        "В указанном бюджете ({fill} ₽) закрою MVP по ТЗ. "
        "Полный объём обсудим отдельно — ориентир от {hint} ₽."
    ),
    (
        "В рамках бюджета ({fill} ₽) сделаю основной сценарий по ТЗ; "
        "полный объём — ориентир от {hint} ₽, если захотите расширить."
    ),
)

_MISMATCH_SOFT: tuple[str, ...] = (
    (
        "В бюджете заказа ({fill} ₽) сделаю основной сценарий по ТЗ. "
        "Расширение сверх MVP обсудим отдельно — после уточнения объёма."
    ),
    (
        "В указанном бюджете ({fill} ₽) закрою основной сценарий по ТЗ. "
        "Расширение сверх MVP обсудим отдельно после уточнения объёма."
    ),
    (
        "В рамках бюджета заказа ({fill} ₽) сделаю MVP. "
        "Расширение объёма обсудим отдельно — после уточнения."
    ),
    (
        "На сумму заказа ({fill} ₽) соберу базовый сценарий. "
        "Расширение сверх MVP обсудим отдельно."
    ),
    (
        "В бюджете заказа ({fill} ₽) сделаю основной сценарий по ТЗ; "
        "расширение сверх MVP — обсудим отдельно после уточнения объёма."
    ),
)


def _pick_mismatch_variant(variants: tuple[str, ...], *, seed: str | None, fill: str, hint: int | None) -> str:
    key = f"{seed or ''}|{fill}|{hint or 0}".encode()
    idx = int(hashlib.md5(key).hexdigest(), 16) % len(variants)
    return variants[idx]


def format_budget_mismatch_sentence(gap: dict, *, seed: str | None = None) -> str:
    listed = int(gap.get("fill_price") or gap["ceiling"])
    fill = format_rub_amount(listed)
    hint = customer_expansion_hint_rub(gap)
    if hint is not None:
        tmpl = _pick_mismatch_variant(
            _MISMATCH_WITH_HINT, seed=seed, fill=fill, hint=hint
        )
        return tmpl.format(fill=fill, hint=format_rub_amount(hint))
    tmpl = _pick_mismatch_variant(_MISMATCH_SOFT, seed=seed, fill=fill, hint=None)
    return tmpl.format(fill=fill)


_SCOPE_NOTE_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "listed_budget",
        re.compile(
            r"в\s+бюджет\w*\s+заказ|"
            r"бюджет\w*\s+заказа|"
            r"в\s+указанн\w+\s+бюджет|"
            r"в\s+рамках\s+бюджет|"
            r"сумм\w*\s+заказа|"
            r"на\s+сумм\w*\s+заказ",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "main_scope",
        re.compile(r"основн\w+\s+сценари|базов\w+\s+сценари|mvp", flags=re.IGNORECASE),
    ),
    (
        "full_scope",
        re.compile(
            r"полн\w+\s+объ[её]м|"
            r"ориентир\w*\s+от|"
            r"если\s+(?:захотите|решите)\s+расширить|"
            r"при\s+расширении|"
            r"расширени\w+\s+объ[её]ма|"
            r"расширение\s+сверх|"
            r"обсудим\s+отдельно",
            flags=re.IGNORECASE,
        ),
    ),
)

_ZANIZHEN_RE = re.compile(r"занижен", flags=re.IGNORECASE)

_DEFER_PRICE_DAYS_RE = re.compile(
    r"обсудим\s+после|"
    r"после\s+уточнения(?:\s+объ[её]ма)?|"
    r"по\s+стоимости\s+и\s+срокам\s+предлагаю\s+обсудить|"
    r"стоимост\w*\s+и\s+срок\w*\s+(?:предлагаю\s+)?(?:обсуд|уточн)|"
    r"срок\w*\s+и\s+стоимост\w*\s+(?:предлагаю\s+)?(?:обсуд|уточн)|"
    r"предлагаю\s+обсудить\s+(?:стоимост|срок|цен)",
    flags=re.IGNORECASE,
)

_ASKING_PRICE_RE = re.compile(
    r"(?P<label>(?:стоимост\w*|цен\w*)\s*[—\-–:]\s*(?:от\s*)?)"
    r"(?P<amount>\d(?:[\d\s\u00a0]*\d)?)\s*(?P<unit>₽|руб\.?)",
    flags=re.IGNORECASE,
)

_OT_MVP_PRICE_RE = re.compile(
    r"(?P<label>от\s+)(?P<amount>\d(?:[\d\s\u00a0]*\d)?)\s*(?P<unit>₽|руб\.?)"
    r"(?P<tail>\s+за\s+MVP)",
    flags=re.IGNORECASE,
)

_OT_ASKING_PRICE_RE = re.compile(
    r"(?P<label>от\s+)(?P<amount>\d(?:[\d\s\u00a0]*\d)?)\s*(?P<unit>₽|руб\.?)",
    flags=re.IGNORECASE,
)

_FAIR_ORIENTIR_RE = re.compile(
    r"полн\w+\s+объ[её]м[^\n\d]{0,48}(?:ориентир\w*\s+)?от\s*"
    r"(?P<amount>\d(?:[\d\s\u00a0]*\d)?)\s*(?:₽|руб\.?)",
    flags=re.IGNORECASE,
)

_MISMATCH_FILL_RE = re.compile(
    r"(?:"
    r"в\s+бюджет\w*\s+заказ\w*|"
    r"в\s+указанн\w+\s+бюджет\w*|"
    r"в\s+рамках\s+бюджет\w*(?:\s+заказ\w*)?|"
    r"на\s+сумм\w*\s+заказ\w*"
    r")\s*\((?P<amount>\d(?:[\d\s\u00a0]*\d)?)(\s*₽\))",
    flags=re.IGNORECASE,
)

_SROK_RE = re.compile(
    r"(срок\s*[—\-–:]\s*)([^\n.]+)",
    flags=re.IGNORECASE,
)

_MISMATCH_LEAD_RE = (
    r"(?:"
    r"в\s+бюджет\w*\s+заказ|"
    r"в\s+указанн\w+\s+бюджет|"
    r"в\s+рамках\s+бюджет|"
    r"на\s+сумм\w*\s+заказ"
    r")"
)

_MISMATCH_TAIL_RE = re.compile(
    rf"\n\n(?={_MISMATCH_LEAD_RE})",
    flags=re.IGNORECASE,
)

# Second line only if it continues the soft note — never swallow the next
# response paragraph (that caused stub-only drafts when note led the text).
_MISMATCH_BLOCK_RE = re.compile(
    rf"(?:^|\n\n)(?P<block>"
    rf"{_MISMATCH_LEAD_RE}[^\n]*"
    r"(?:\n[^\n]*(?:расширение|полн\w+\s+объ[её]м|ориентир\w*\s+от)[^\n]*)?"
    r")",
    flags=re.IGNORECASE,
)

_SOFT_EXPAND_RE = re.compile(
    r"расширение\s+сверх\s+mvp\s*[—\-]?\s*обсудим\s+отдельно[^\n]*|"
    r"расширение\s+объ[её]ма\s+обсудим\s+отдельно[^\n]*",
    flags=re.IGNORECASE,
)


def response_defers_price_or_days(text: str) -> bool:
    """True when body clearly defers pricing / timing to clarification."""
    # Soft mismatch note also contains «после уточнения объёма» — ignore it.
    body = _SOFT_EXPAND_RE.sub(" ", text or "")
    return bool(_DEFER_PRICE_DAYS_RE.search(body))


def response_has_budget_discuss_note(text: str) -> bool:
    """Scope note needs two independent signals, not a single stray phrase."""
    body = text or ""
    hits = sum(1 for _, pattern in _SCOPE_NOTE_SIGNALS if pattern.search(body))
    return hits >= 2


def _hint_cap_rub(gap: dict) -> int | None:
    """Upper bound for a customer-facing expansion number (hint or soft cap)."""
    hint = customer_expansion_hint_rub(gap)
    if hint is not None:
        return hint
    fill = int(gap.get("fill_price") or gap.get("ceiling") or 0)
    max_c_raw = gap.get("max_ceiling")
    if max_c_raw is not None:
        return int(max_c_raw)
    if fill > 0:
        return int(fill * 2.5)
    return None


def _join_prefix_note_suffix(prefix: str, note: str, suffix: str) -> str:
    prefix = (prefix or "").rstrip()
    suffix = (suffix or "").lstrip("\n")
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    parts.append(note.rstrip())
    if suffix:
        parts.append(suffix)
    return "\n\n".join(parts)


def _normalize_mismatch_note_block(
    body: str, gap: dict, *, seed: str | None = None
) -> str | None:
    """Replace trailing mismatch / scary fair orientir with soft sentence.

    Always keep text outside the replaced block (prefix AND suffix). Old code
    dropped suffix when the scope note sat at the start → stub-only responses.
    """
    note = format_budget_mismatch_sentence(gap, seed=seed)
    block_m = None
    for m in _MISMATCH_BLOCK_RE.finditer(body):
        block_m = m
    if block_m is not None:
        start = block_m.start("block")
        end = block_m.end("block")
        return _join_prefix_note_suffix(
            body[:start], note, body[end:]
        )

    fair_m = None
    for m in _FAIR_ORIENTIR_RE.finditer(body):
        fair_m = m
    if fair_m is not None:
        amount = _amount_int(fair_m.group("amount"))
        cap = _hint_cap_rub(gap)
        if amount is not None and cap is not None and amount > cap:
            # Replace from sentence start (prefer after newline) through EOL.
            line_start = body.rfind("\n", 0, fair_m.start()) + 1
            line_end = body.find("\n", fair_m.end())
            if line_end < 0:
                line_end = len(body)
            return _join_prefix_note_suffix(
                body[:line_start], note, body[line_end:]
            )
        return None

    soft_m = _SOFT_EXPAND_RE.search(body)
    if soft_m is not None:
        line_start = body.rfind("\n", 0, soft_m.start()) + 1
        # Also pull preceding «В бюджете…» on same paragraph if present.
        para_start = body.rfind("\n\n", 0, soft_m.start())
        start = 0 if para_start < 0 else para_start + 2
        if re.search(_MISMATCH_LEAD_RE, body[start:soft_m.start()], flags=re.IGNORECASE):
            line_start = start
        line_end = body.find("\n", soft_m.end())
        if line_end < 0:
            line_end = len(body)
        return _join_prefix_note_suffix(
            body[:line_start], note, body[line_end:]
        )
    return None


def ensure_budget_mismatch_note(
    text: str, gap: dict | None, *, seed: str | None = None
) -> str:
    """Append or normalize soft scope sentence when gap present."""
    if not gap:
        return text
    body = (text or "").rstrip()
    if response_defers_price_or_days(body):
        return body
    note = format_budget_mismatch_sentence(gap, seed=seed)
    if response_has_budget_discuss_note(body):
        rewritten = _normalize_mismatch_note_block(body, gap, seed=seed)
        if rewritten is None:
            return body
        # Guard: never collapse a full draft into the note alone.
        if len(body) >= 220 and len(rewritten) < max(120, len(body) // 3):
            if note.lower() in body.lower():
                return body
            return f"{body}\n\n{note}"
        return rewritten
    if not body:
        return note
    return f"{body}\n\n{note}"


def _amount_int(raw: str) -> int | None:
    digits = re.sub(r"[\s\u00a0]+", "", raw or "")
    if not digits.isdigit():
        return None
    return int(digits)


def _fair_span_overlaps(start: int, end: int, fair_spans: list[tuple[int, int]]) -> bool:
    for fs, fe in fair_spans:
        if start < fe and end > fs:
            return True
    return False


def _split_mismatch_tail(body: str) -> tuple[str, str]:
    m = _MISMATCH_TAIL_RE.search(body)
    if not m:
        return body, ""
    return body[: m.start()].rstrip(), body[m.start() :]


def align_response_commercials(
    text: str,
    *,
    price: int,
    delivery_days: int | None,
) -> str:
    """Force asking price / срок in body to match form fields."""
    body = (text or "").rstrip()
    asking = int(price or 0)
    if asking <= 0 and delivery_days is None:
        return body
    formatted = format_rub_amount(asking) if asking > 0 else ""
    head, tail = _split_mismatch_tail(body)

    if asking > 0 and tail:
        fill_m = _MISMATCH_FILL_RE.search(tail)
        if fill_m:
            fill_val = _amount_int(fill_m.group("amount"))
            if fill_val is not None and fill_val != asking:
                tail = (
                    tail[: fill_m.start("amount")]
                    + formatted
                    + tail[fill_m.end("amount") :]
                )

    if asking > 0:
        fair_spans = [
            (m.start("amount"), m.end("amount"))
            for m in _FAIR_ORIENTIR_RE.finditer(head)
            if _amount_int(m.group("amount")) != asking
        ]
        replaced = False
        for pattern in (_ASKING_PRICE_RE, _OT_MVP_PRICE_RE, _OT_ASKING_PRICE_RE):
            for m in pattern.finditer(head):
                if _fair_span_overlaps(m.start("amount"), m.end("amount"), fair_spans):
                    continue
                current = _amount_int(m.group("amount"))
                if current is None:
                    continue
                if current == asking:
                    replaced = True
                    break
                head = (
                    head[: m.start("amount")]
                    + formatted
                    + head[m.end("amount") :]
                )
                replaced = True
                break
            if replaced:
                break
        if not replaced:
            price_line = f"Стоимость — {formatted} ₽."
            head = f"{head.rstrip()}\n{price_line}" if head else price_line

    if delivery_days is not None:
        days = int(delivery_days)
        srok = _SROK_RE.search(head)
        if srok:
            head = head[: srok.start(2)] + f"{days} дн" + head[srok.end(2) :]
        else:
            srok_line = f"Срок — {days} дн."
            head = f"{head.rstrip()}\n{srok_line}" if head else srok_line

    out = f"{head.rstrip()}{tail}" if tail else head.rstrip()
    return out


def budget_mismatch_issues(text: str, gap: dict | None) -> list[str]:
    """Local checks: listed asking price + scope note; ban «занижен» / fair-only."""
    if not gap:
        return []
    issues: list[str] = []
    body = text or ""
    if response_defers_price_or_days(body):
        if _ZANIZHEN_RE.search(body):
            issues.append("budget_mismatch:zanizhen_strategy")
        return issues
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
