"""Постобработка текста отклика перед вставкой в форму."""
from __future__ import annotations

import hashlib
import re

from src.analyzer.project_brief import build_project_brief, buyer_checklist_issues
from src.models import ProjectFull

_NO_ONLINE_PAYMENT_TZ_RE = re.compile(
    r"без\s+онлайн[- ]?оплат|оформление\s+заявки\s+без|без\s+оплат[ыи]?\s+внутри",
    re.I,
)
_PAYMENT_IN_RESPONSE_RE = re.compile(
    r"плат[её]жн\w*|эквайринг|yookassa|юкасс|stripe|оплат[аы]\s+внутри",
    re.I,
)
_GITHUB_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/\S+|(?<![/\w])github\.com/[\w\-./]+",
    re.I,
)
_PORTFOLIO_LINE_RE = re.compile(
    r"\n*портфолио\s*:\s*https?://\S+\s*$",
    re.I,
)

_KWORK_VIOLATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("off_platform_call", re.compile(r"созвон", re.I)),
    ("phone_call", re.compile(r"позвон(им|ить|ю)?|звонок|перезвон", re.I)),
    ("direct_contact", re.compile(r"свяжемся напрямую|обменяемся контактами|вне (?:kwork|кворк|сервиса)", re.I)),
    ("messengers", re.compile(r"whatsapp|вайбер|viber|signal|директ в инст", re.I)),
    (
        "telegram_contact",
        re.compile(
            r"(?:напишите|пишите|пиши|напиши|свяжитесь|свяжись|мой|в)\s+(?:в\s+)?(?:telegram|телеграм|tg)\b",
            re.I,
        ),
    ),
    ("email_share", re.compile(r"(?:мой\s+)?e-?mail|почт[аеу]\s*:|@\w+\.(?:ru|com)", re.I)),
    ("kwork_commission", re.compile(r"комисси[яю].*kwork|комисси[яю].*кворк", re.I)),
)


def strip_response_markdown(text: str) -> str:
    """Убрать markdown-выделение и ссылки [текст](url) из GPT-ответа."""
    cleaned = text
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")
    return cleaned.strip()


# Orphan «см. / смотрите … и» left after URL wipe.
_SEE_AND_ORPHAN_RE = re.compile(
    r"(?:^|(?<=[.!?…]\s)|(?<=\n))"
    r"(?:см\.?|смотрите|смотри)\s+и\b[.,;:]?\s*",
    re.I,
)
# Lone connector / see-also debris on its own line.
_ORPHAN_CONNECTOR_LINE_RE = re.compile(
    r"^\s*(?:и|или|а\s+также|см\.?|смотрите|смотри)\s*[.,;:]?\s*$",
    re.I | re.M,
)
# Line that is only leftover punctuation / empty parens.
_PUNCT_ONLY_LINE_RE = re.compile(r"^\s*[()\[\].,;:\-–—«»\"']+\s*$", re.M)


def clean_link_strip_collateral(text: str) -> str:
    """Remove prose debris left after URL/label stripping — never restore links."""
    cleaned = text or ""
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    cleaned = _SEE_AND_ORPHAN_RE.sub("", cleaned)
    cleaned = _ORPHAN_CONNECTOR_LINE_RE.sub("", cleaned)
    cleaned = _PUNCT_ONLY_LINE_RE.sub("", cleaned)
    # Dangling connectors before sentence end / next sentence.
    cleaned = re.sub(r"\s+(?:и|или)\s*([.,;:])", r"\1", cleaned, flags=re.I)
    # Collapse empty / punct-only lines and trim horizontal runs.
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = re.sub(r"[^\S\n]{2,}", " ", raw_line).strip()
        if not line:
            continue
        if _PUNCT_ONLY_LINE_RE.match(line) or _ORPHAN_CONNECTOR_LINE_RE.match(line):
            continue
        if _SEE_AND_ORPHAN_RE.fullmatch(line):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"[^\S\n]+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?;:]){2,}", r"\1", cleaned)
    cleaned = re.sub(r"[^\S\n]{2,}", " ", cleaned)
    return cleaned.strip()


def strip_github_links(text: str) -> str:
    cleaned = _GITHUB_URL_RE.sub("", text)
    cleaned = re.sub(r"GitHub\s*:\s*", "", cleaned, flags=re.I)
    # Keep paragraph breaks — only collapse horizontal whitespace runs.
    cleaned = re.sub(r"[^\S\n]{2,}", " ", cleaned)
    cleaned = re.sub(r"[^\S\n]*\n[^\S\n]*", "\n", cleaned)
    cleaned = re.sub(r"[^\S\n]+([,.])", r"\1", cleaned)
    return clean_link_strip_collateral(cleaned)


def strip_portfolio_footer(text: str) -> str:
    return _PORTFOLIO_LINE_RE.sub("", (text or "").strip()).strip()


def tz_requires_lead_only(project: ProjectFull) -> bool:
    brief = build_project_brief(project)
    return bool(_NO_ONLINE_PAYMENT_TZ_RE.search(brief))


def payment_mismatch_issues(project: ProjectFull, response: str) -> list[str]:
    if not tz_requires_lead_only(project):
        return []
    if _PAYMENT_IN_RESPONSE_RE.search(response):
        return ["tz:payment_not_required"]
    return []


_NAMED_HELLO_RE = re.compile(
    r"^([А-ЯЁA-Z][\w\-]*(?:\s+[А-ЯЁA-Z][\w\-]*)?),\s*здравствуйте\s*[!.]?\s*",
    re.I,
)

_OTHER_GREETING_RE = re.compile(
    r"^(?:здравствуйте|добрый день|доброго времени|приветствую)\s*[!.,]?\s*",
    re.I,
)


def strip_hallucinated_greeting(text: str, buyer_name: str | None) -> str:
    """Legacy hook — greeting is normalized in ensure_hello_greeting."""
    _ = buyer_name
    return (text or "").strip()


def ensure_hello_greeting(text: str) -> str:
    """Every response must start with «Здравствуйте!» (no buyer name prefix)."""
    body = (text or "").strip()
    if not body:
        return "Здравствуйте!"
    named = _NAMED_HELLO_RE.match(body)
    if named:
        body = body[named.end() :].lstrip()
    body = _OTHER_GREETING_RE.sub("", body, count=1).lstrip()
    if not body:
        return "Здравствуйте!"
    # Own paragraph so later paragraphizer keeps greeting separate.
    return f"Здравствуйте!\n{body}"


_BUYER_NOISE = frozenset(
    {
        "войти",
        "вход",
        "логин",
        "чаты",
        "чат",
        "отклики",
        "заказчик",
        "покупатель",
        "фрилансер",
        "профиль",
        "настройки",
        "уведомления",
        "помощь",
        "поддержка",
        "меню",
        "кабинет",
        "пользователь",
        "user",
        "login",
        "customer",
        "author",
    }
)

# Self / account chrome mistaken for buyer (Yandex cab menu, etc.)
_BUYER_SELF_OR_UI_RE = re.compile(
    r"клычников|уведомлен|выйти\s+из\s+аккаунт|написать\s+письмо|"
    r"@yandex\.|@gmail\.|почта\d|alexander\.klichnikov",
    re.I,
)


def sanitize_buyer_field(buyer: str | None) -> str | None:
    """Drop garbled UI dumps and own account name mistaken for заказчик."""
    if not buyer:
        return None
    raw = buyer.strip()
    if not raw:
        return None
    if len(raw) > 48 or _BUYER_SELF_OR_UI_RE.search(raw):
        return None
    return raw


def buyer_first_name(buyer: str | None) -> str | None:
    buyer = sanitize_buyer_field(buyer)
    if not buyer:
        return None
    name = buyer.strip()
    if not name or name.lower() in {"неизвестно", "unknown", "-", "—"}:
        return None
    name = re.split(r"[·|•,/]", name, maxsplit=1)[0].strip()
    token = name.split()[0].strip()
    if len(token) < 2 or not re.search(r"[А-ЯЁA-Z]", token, re.I):
        return None
    if token.lower() in _BUYER_NOISE:
        return None
    if re.fullmatch(r"\d+", token):
        return None
    return token


_PARAGRAPH_INDENT = "    "  # красная строка


def _with_red_line(paragraph: str) -> str:
    body = (paragraph or "").strip()
    if not body:
        return ""
    return _PARAGRAPH_INDENT + body


def unescape_literal_newlines(text: str) -> str:
    """GPT sometimes dumps literal \\n / \\r\\n into the body instead of real breaks."""
    raw = text or ""
    # Collapse double-escaped sequences first (\\\\n → \\n), then to real newlines.
    while "\\\\n" in raw:
        raw = raw.replace("\\\\n", "\\n")
    while "\\\\r\\\\n" in raw:
        raw = raw.replace("\\\\r\\\\n", "\\r\\n")
    raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    return raw


_HELLO_ONLY_RE = re.compile(r"^здравствуйте\s*[!.]?$", re.I)
_COMMERCIAL_LINE_RE = re.compile(
    r"^(?:стоимость|срок|цен[аы])\s*[—\-–:]",
    re.I,
)


def _sentence_parts(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?…])\s+", raw) if p.strip()]


def _group_logical_blocks(parts: list[str]) -> list[str]:
    """Greeting / essence / details / commercials / CTA as separate blocks."""
    if not parts:
        return []
    if len(parts) == 1:
        return parts

    blocks: list[str] = []
    i = 0
    if _HELLO_ONLY_RE.match(parts[0]):
        blocks.append(parts[0])
        i = 1
    rest = parts[i:]
    if not rest:
        return blocks

    commercials = [p for p in rest if _COMMERCIAL_LINE_RE.match(p)]
    content = [p for p in rest if not _COMMERCIAL_LINE_RE.match(p)]
    if not content:
        blocks.append(" ".join(commercials))
        return blocks

    if len(content) == 1:
        blocks.append(content[0])
    elif len(content) == 2:
        blocks.append(content[0])
        blocks.append(content[1])
    else:
        # essence | details | CTA
        blocks.append(content[0])
        mid = content[1:-1]
        if len(mid) == 1:
            blocks.append(mid[0])
        else:
            half = max(1, len(mid) // 2)
            blocks.append(" ".join(mid[:half]))
            if mid[half:]:
                blocks.append(" ".join(mid[half:]))
        blocks.append(content[-1])

    if commercials:
        # Price/srok as their own block, just before the closing CTA.
        cta = blocks.pop()
        blocks.append(" ".join(commercials))
        blocks.append(cta)
    return blocks


def ensure_response_paragraphs(text: str) -> str:
    """Blank line between logical blocks; each starts with 4-space red line."""
    raw = unescape_literal_newlines(text or "").strip()
    if not raw:
        return raw

    # Existing line breaks → keep as paragraph candidates (blank or single).
    if "\n" in raw:
        rough = [ln.strip() for ln in re.split(r"\n+", raw) if ln.strip()]
        # One fat line still a wall → sentence-split that chunk.
        if len(rough) == 1:
            parts = _sentence_parts(rough[0])
            blocks = _group_logical_blocks(parts)
        elif len(rough) >= 4:
            # Already broken enough — keep as-is (greeting/body/details/cta).
            blocks = rough
        else:
            # Few lines: re-group by sentences across the whole body for balance.
            parts = _sentence_parts(" ".join(rough))
            blocks = _group_logical_blocks(parts) if len(parts) >= 3 else rough
    else:
        blocks = _group_logical_blocks(_sentence_parts(raw))

    return "\n\n".join(_with_red_line(b) for b in blocks if b)


# Stock mini-proof openers the model overuses at paragraph start.
_MINI_PROOF_STOCK_RE = re.compile(
    r"(?:^|(?<=\n)|(?<=[.!?…]\s))"
    r"(?:"
    r"Могу показать\s+(?:похожий|близкий)\s+demo\s*/\s*кейс\s+в\s+чате(?:\s+Kwork)?"
    r"|Могу показать\s+(?:похожий|близкий)\s+(?:demo|кейс)\s+в\s+чате(?:\s+Kwork)?"
    r"|Demo\s+похожего\s+кейса\s+могу показать\s+в\s+чате(?:\s+Kwork)?"
    r"|Похожую логику[^.!?\n]{0,80}?могу показать\s+в\s+чате(?:\s+Kwork)?"
    r"|Похожий\s+(?:demo|кейс)[^.!?\n]{0,40}?могу показать\s+в\s+чате(?:\s+Kwork)?"
    r")"
    r"\s*[.!]?",
    re.I | re.M,
)

_MINI_PROOF_VARIANTS: tuple[str, ...] = (
    "Близкий рабочий пример по такой логике пришлю в чате.",
    "Если нужно — разберу похожий кейс прямо в переписке.",
    "По сопоставимому кейсу могу скинуть короткое demo в чате.",
    "Есть близкий пример: детали покажу у вас в чате.",
    "Похожий сценарий уже делал — demo обсудим в чате без ссылок.",
    "Могу коротко пройтись по близкому кейсу здесь в переписке.",
    "Сопоставимый пример есть: покажу ход работ в чате.",
)


def _pick_mini_proof_variant(seed: str | None) -> str:
    key = (seed or "mini-proof").encode("utf-8", errors="ignore")
    idx = int(hashlib.md5(key).hexdigest(), 16) % len(_MINI_PROOF_VARIANTS)
    return _MINI_PROOF_VARIANTS[idx]


def diversify_mini_proof(text: str, *, seed: str | None = None) -> str:
    """Replace overused «Могу показать … в чате Kwork» with a seeded variant."""
    body = text or ""
    if not _MINI_PROOF_STOCK_RE.search(body):
        return body
    replacement = _pick_mini_proof_variant(seed)

    def _sub(match: re.Match[str]) -> str:
        start = match.start()
        prefix = ""
        # Keep paragraph break / space before replacement when match ate it via lookbehind.
        if start > 0 and body[start - 1] not in "\n ":
            prefix = " "
        return prefix + replacement

    return _MINI_PROOF_STOCK_RE.sub(_sub, body, count=1)


_YANDEX_USLUGI_MAX_CHARS = 1000


def clamp_yandex_response_length(text: str, limit: int = _YANDEX_USLUGI_MAX_CHARS) -> str:
    """Hard cap for Яндекс Услуги form (1000 chars incl. spaces/newlines)."""
    body = text or ""
    if len(body) <= limit:
        return body
    cut = body[:limit]
    floor = int(limit * 0.55)
    for sep in ("\n\n", "\n"):
        idx = cut.rfind(sep)
        if idx >= floor:
            return cut[:idx].rstrip()
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx >= floor:
            return cut[: idx + 1].rstrip()
    return cut.rstrip()


def finalize_response_text(text: str, project: ProjectFull | None = None) -> str:
    cleaned = unescape_literal_newlines(text)
    cleaned = strip_response_markdown(cleaned)
    cleaned = strip_github_links(cleaned)
    cleaned = strip_portfolio_footer(cleaned)
    cleaned = clean_link_strip_collateral(cleaned)
    cleaned = ensure_hello_greeting(cleaned)
    seed = None
    if project is not None:
        seed = f"{project.platform}:{project.project_id}"
    cleaned = diversify_mini_proof(cleaned, seed=seed)
    if project is not None:
        cleaned = ensure_response_paragraphs(cleaned)
    # Keep leading indent (красная строка) on the first paragraph.
    cleaned = cleaned.rstrip()
    if project is not None and project.platform == "yandex_uslugi":
        cleaned = clamp_yandex_response_length(cleaned)
    return cleaned

def kwork_compliance_issues(text: str) -> list[str]:
    issues: list[str] = []
    for name, pattern in _KWORK_VIOLATION_PATTERNS:
        if pattern.search(text):
            issues.append(name)
    return issues


def append_missing_checklist_answers(
    text: str,
    project: ProjectFull,
    *,
    price_rub: int | None = None,
    delivery_days: int | None = None,
) -> str:
    missing = buyer_checklist_issues(project, text)
    if not missing:
        return text
    extras: list[str] = []
    if "checklist:стоимость" in missing and price_rub:
        extras.append(f"Стоимость: {price_rub} ₽.")
    if "checklist:срок" in missing and delivery_days:
        extras.append(f"Срок: {delivery_days} дн.")
    if "checklist:стек" in missing:
        extras.append("Стек: Python, aiogram, SQLAlchemy, PostgreSQL или SQLite.")
    if "checklist:код" in missing:
        extras.append(
            "Готов сначала посмотреть текущий код и наработки предыдущего исполнителя."
        )
    if "checklist:передача" in missing:
        extras.append(
            "В передачу входят исходный код, база данных, инструкция по запуску "
            "и проверка основных сценариев."
        )
    if "checklist:входит" in missing:
        extras.append(
            "В стоимость входит основной функционал по ТЗ, установка/запуск "
            "и инструкция по проверке основных сценариев."
        )
    if not extras:
        return text
    indented = "\n\n".join(_with_red_line(x) for x in extras)
    return text.rstrip() + "\n\n" + indented
