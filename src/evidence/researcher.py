from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Literal

from src.evidence.models import EvidenceFact, EvidenceInsight, EvidenceSource
from src.models import ProjectFull

Role = Literal[
    "reference",
    "data_source",
    "documentation",
    "repository",
    "unknown",
]

_WS_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)

_LOT_RE = re.compile(r"Лот\s*№\s*(\d{4,})", re.IGNORECASE)
_NOTICE_RE = re.compile(
    r"(?:Номер\s+извещения|№)\s*(\d{10,})",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"(\d{1,3}(?:\s\d{3})+)\s*₽")
# No \b: Cyrillic letters are \w, so glued "публикации12.09.2026" has no word boundary
_DATE_ISO_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_DATE_RU_RE = re.compile(r"(?<!\d)(\d{2}\.\d{2}\.\d{4})(?!\d)")

_LABEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("начальная цена", re.compile(r"Начальная\s+цена", re.IGNORECASE)),
    ("срок подачи заявок", re.compile(r"Срок\s+подачи\s+заявок", re.IGNORECASE)),
    ("дата публикации", re.compile(r"Дата\s+публикации", re.IGNORECASE)),
    ("номер извещения", re.compile(r"Номер\s+извещения", re.IGNORECASE)),
    ("документы", re.compile(r"(?:^|\n)Документы(?:\n|$)", re.IGNORECASE)),
    ("вложения", re.compile(r"(?:^|\n)Вложения(?:\n|$)", re.IGNORECASE)),
)

_LK_IN_SOURCE_RE = re.compile(
    r"личн\w*\s+кабинет|\bЛК\b|агент\w*|избранн\w*",
    re.IGNORECASE,
)
_NO_LK_IN_PROJECT_RE = re.compile(
    r"без\s+(?:личн\w*\s+кабинет\w*|ЛК)\b",
    re.IGNORECASE,
)

# Prompt-injection / instruction-looking lines — never promote as facts
_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+)?previous|system\s*prompt|you\s+are\s+now|"
    r"забудь\s+предыдущ|игнорируй\s+предыдущ",
    re.IGNORECASE,
)


def normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def strip_pii(text: str) -> str:
    out = _EMAIL_RE.sub("[email]", text)
    out = _PHONE_RE.sub("[phone]", out)
    return out


def quote_in_source(quote: str, source_text: str) -> bool:
    """True if quote is a substring of source (exact or whitespace-normalized)."""
    if not quote or not source_text:
        return False
    if quote in source_text:
        return True
    return normalize_ws(quote) in normalize_ws(source_text)


def _has_pii(text: str) -> bool:
    return bool(_EMAIL_RE.search(text) or _PHONE_RE.search(text))


def verify_fact_quote(fact: EvidenceFact, source_text: str) -> EvidenceFact:
    """Gate eligible_for_response on exact/normalized quote presence."""
    ok = bool(fact.quote) and quote_in_source(fact.quote, source_text)
    if ok and _INJECTION_RE.search(fact.quote):
        ok = False
    if ok and _INJECTION_RE.search(fact.claim):
        ok = False
    # Do not promote quotes that still carry raw PII
    if ok and _has_pii(fact.quote):
        ok = False
    return fact.model_copy(update={"eligible_for_response": ok})


def _fact_id(source_id: str, kind: str, payload: str) -> str:
    raw = f"{source_id}:{kind}:{payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _context_quote(text: str, match: re.Match[str], *, radius: int = 40) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    snippet = text[start:end].strip()
    # Prefer the matched span itself when short enough
    matched = match.group(0).strip()
    if matched and quote_in_source(matched, text):
        return matched
    return snippet


def extract_facts_from_text(
    source_id: str,
    role: Role,
    text: str,
    *,
    max_facts: int = 7,
) -> list[EvidenceFact]:
    """Deterministic MVP extractor. Quotes must appear in source to be eligible."""
    if not text or max_facts <= 0:
        return []

    candidates: list[EvidenceFact] = []
    seen_keys: set[str] = set()

    def _add(
        *,
        kind: str,
        claim: str,
        quote: str,
        anchors: list[str],
        relevance: float,
        verification: Literal["exact_quote", "structured_value"] = "exact_quote",
    ) -> None:
        if len(candidates) >= max_facts:
            return
        if _INJECTION_RE.search(claim) or _INJECTION_RE.search(quote):
            return
        claim_clean = strip_pii(claim).strip()
        quote_clean = quote.strip()
        if not claim_clean or not quote_clean:
            return
        # Skip facts whose verbatim quote still embeds email/phone
        if _has_pii(quote_clean):
            return
        if not quote_in_source(quote_clean, text):
            return
        key = f"{kind}:{normalize_ws(quote_clean).casefold()}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        fact = EvidenceFact(
            id=_fact_id(source_id, kind, quote_clean),
            source_id=source_id,
            claim=claim_clean,
            quote=quote_clean,
            anchors=anchors,
            relevance=relevance,
            verification=verification,
            eligible_for_response=False,
        )
        candidates.append(verify_fact_quote(fact, text))

    for m in _LOT_RE.finditer(text):
        lot = m.group(1)
        _add(
            kind="lot",
            claim=f"На странице указан лот №{lot}",
            quote=m.group(0),
            anchors=[lot],
            relevance=0.95,
            verification="structured_value",
        )

    for m in _NOTICE_RE.finditer(text):
        notice = m.group(1)
        _add(
            kind="notice",
            claim=f"Номер извещения: {notice}",
            # Prefer the digit span — group(0) may span a newline after HTML normalize
            quote=notice if quote_in_source(notice, text) else m.group(0).strip(),
            anchors=[notice],
            relevance=0.9,
            verification="structured_value",
        )

    for m in _PRICE_RE.finditer(text):
        price = m.group(1)
        _add(
            kind="price",
            claim=f"Указана цена {price} ₽",
            quote=m.group(0),
            anchors=[price, f"{price} ₽"],
            relevance=0.9,
            verification="structured_value",
        )

    for m in _DATE_ISO_RE.finditer(text):
        date = m.group(1)
        _add(
            kind="date",
            claim=f"Указана дата {date}",
            quote=date,
            anchors=[date],
            relevance=0.75,
            verification="structured_value",
        )

    for m in _DATE_RU_RE.finditer(text):
        date = m.group(1)
        _add(
            kind="date",
            claim=f"Указана дата {date}",
            quote=date,
            anchors=[date],
            relevance=0.75,
            verification="structured_value",
        )

    for label, pattern in _LABEL_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        quote = m.group(0).strip()
        if quote.startswith("\n"):
            quote = quote.strip()
        _add(
            kind="label",
            claim=f"На странице есть блок/поле «{label}»",
            quote=quote,
            anchors=[label],
            relevance=0.55,
        )

    # Role-tagged soft signal for search/docs scope (still quote-gated)
    if role in ("reference", "data_source"):
        for needle, claim in (
            ("без личного кабинета", "Источник подтверждает сценарий без личного кабинета"),
            ("Поиск торгов", "На странице есть поиск торгов"),
            ("Документы", "На странице есть секция документов"),
        ):
            if needle in text:
                _add(
                    kind="scope_signal",
                    claim=claim,
                    quote=needle,
                    anchors=[needle],
                    relevance=0.5,
                )

    candidates.sort(key=lambda f: (-f.relevance, f.id))
    return candidates[:max_facts]


def build_insights(
    project: ProjectFull,
    sources: Sequence[EvidenceSource],
    facts: Sequence[EvidenceFact],
    *,
    source_texts: dict[str, str] | None = None,
) -> list[EvidenceInsight]:
    """Derive implementation insights from project TZ vs verified source signals."""
    insights: list[EvidenceInsight] = []
    project_text = f"{project.title or ''}\n{project.full_description or ''}"
    project_wants_no_lk = bool(_NO_LK_IN_PROJECT_RE.search(project_text))
    texts = source_texts or {}

    for src in sources:
        if src.role != "reference":
            continue
        if src.status not in ("verified", "partial"):
            continue
        body = texts.get(src.id, "")
        if not body or not _LK_IN_SOURCE_RE.search(body):
            continue
        if not project_wants_no_lk:
            continue
        related = [
            f.id
            for f in facts
            if f.source_id == src.id and f.eligible_for_response
        ]
        insights.append(
            EvidenceInsight(
                fact_ids=related[:5],
                finding=(
                    "Референс содержит ЛК/агентов/избранное, "
                    "а ТЗ требует реализацию без личного кабинета"
                ),
                implementation_consequence=(
                    "В объём не копировать ЛК, агентов и избранное; "
                    "оставить поиск, выдачу и документы"
                ),
                kind="scope",
            )
        )

    return insights


def reject_invented_quote(
    source_id: str,
    claim: str,
    quote: str,
    source_text: str,
) -> EvidenceFact:
    """Helper for tests: build a fact and mark eligibility via quote check."""
    fact = EvidenceFact(
        id=_fact_id(source_id, "manual", quote or claim),
        source_id=source_id,
        claim=strip_pii(claim),
        quote=quote,
        anchors=[],
        relevance=0.1,
        verification="exact_quote",
        eligible_for_response=True,  # caller may set True; verify gates it
    )
    return verify_fact_quote(fact, source_text)
