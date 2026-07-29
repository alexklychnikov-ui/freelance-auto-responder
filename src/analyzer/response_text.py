"""Постобработка текста отклика перед вставкой в форму."""
from __future__ import annotations

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


def strip_github_links(text: str) -> str:
    cleaned = _GITHUB_URL_RE.sub("", text)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"GitHub\s*:\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.])", r"\1", cleaned)
    return cleaned.strip()


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


def strip_hallucinated_greeting(text: str, buyer_name: str | None) -> str:
    body = (text or "").strip()
    m = _NAMED_HELLO_RE.match(body)
    if not m:
        return body
    used = m.group(1).strip()
    if not buyer_name:
        return body[m.end() :].lstrip()
    if used.casefold() != buyer_name.casefold():
        return f"{buyer_name}, здравствуйте!\n{body[m.end() :].lstrip()}"
    return body


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


def ensure_response_paragraphs(text: str) -> str:
    """Paragraphs: single \\n between blocks, each starts with 4-space red line."""
    raw = unescape_literal_newlines(text or "").strip()
    if not raw:
        return raw
    raw = re.sub(r"\n{2,}", "\n", raw).strip()

    if "\n" in raw:
        blocks = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    else:
        parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", raw) if p.strip()]
        if len(parts) <= 2:
            blocks = parts
        else:
            blocks = [parts[0]]
            mid = parts[1:-2] if len(parts) >= 4 else parts[1:-1]
            if mid:
                if len(mid) <= 2:
                    blocks.append(" ".join(mid))
                else:
                    half = max(1, len(mid) // 2)
                    blocks.append(" ".join(mid[:half]))
                    blocks.append(" ".join(mid[half:]))
            tail = parts[-2:] if len(parts) >= 4 else parts[-1:]
            blocks.append(" ".join(tail))

    return "\n".join(_with_red_line(b) for b in blocks if b)


def finalize_response_text(text: str, project: ProjectFull | None = None) -> str:
    cleaned = unescape_literal_newlines(text)
    cleaned = strip_response_markdown(cleaned)
    cleaned = strip_github_links(cleaned)
    cleaned = strip_portfolio_footer(cleaned)
    if project is not None:
        cleaned = strip_hallucinated_greeting(
            cleaned, buyer_first_name(project.buyer)
        )
        cleaned = ensure_response_paragraphs(cleaned)
    # Keep leading indent (красная строка) on the first paragraph.
    return cleaned.rstrip()

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
    if not extras:
        return text
    indented = "\n".join(_with_red_line(x) for x in extras)
    return text.rstrip() + "\n" + indented
