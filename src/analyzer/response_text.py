"""Постобработка текста отклика перед вставкой в форму."""
from __future__ import annotations

import re

from src.analyzer.project_brief import buyer_checklist_issues
from src.models import ProjectFull

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
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")
    return cleaned.strip()


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
    return text.rstrip() + "\n\n" + "\n".join(extras)
