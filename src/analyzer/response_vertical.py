"""Vertical accent hints for DraftWriter (short blocks, not full prompts)."""
from __future__ import annotations

import re
from typing import Any

from src.models import ProjectFull

VERTICAL_TELEGRAM_AI = "telegram_ai"
VERTICAL_AUTOMATION_AI = "automation_ai"
VERTICAL_INTEGRATION = "integration"
VERTICAL_WEB_MVP = "web_mvp"
VERTICAL_PARSING = "parsing"
VERTICAL_GENERAL = "general"

_BOT_TOKEN_RE = re.compile(
    r"(?i)(?:telegram|телеграм|aiogram)|(?<![а-яёa-z])бот(?:ы|ов|а|у|ом|е)?(?![а-яёa-z])"
)

_SUGGESTED_TYPE_MAP: dict[str, str] = {
    "telegram-бот": VERTICAL_TELEGRAM_AI,
    "telegram бот": VERTICAL_TELEGRAM_AI,
    "telegram": VERTICAL_TELEGRAM_AI,
    "ai/rag": VERTICAL_AUTOMATION_AI,
    "ai-rag": VERTICAL_AUTOMATION_AI,
    "автоматизация": VERTICAL_AUTOMATION_AI,
    "интеграция": VERTICAL_INTEGRATION,
    "веб-mvp": VERTICAL_WEB_MVP,
    "веб mvp": VERTICAL_WEB_MVP,
    "web-mvp": VERTICAL_WEB_MVP,
    "сайт": VERTICAL_WEB_MVP,
    "парсинг": VERTICAL_PARSING,
}

VERTICAL_HINTS: dict[str, str] = {
    VERTICAL_TELEGRAM_AI: (
        "Акценты Telegram/AI-бот:\n"
        "- сценарии и состояния диалога, не «просто бот»\n"
        "- заявка → менеджер / очередь обработки\n"
        "- CRM / уведомления админу при необходимости\n"
        "- aiogram / webhook, если уместно по ТЗ\n"
        "- измеримый исход: заявки, статусы, ответы"
    ),
    VERTICAL_AUTOMATION_AI: (
        "Акценты автоматизации / AI-пайплайна:\n"
        "- документы / Excel / OCR / RAG / классификация — по ТЗ\n"
        "- понятный поток: вход → обработка → артефакт\n"
        "- измеримый выход (файл, отчёт, метка, ответ)\n"
        "- без воды про «умные нейросети»\n"
        "- стек только если нужен для доверия"
    ),
    VERTICAL_INTEGRATION: (
        "Акценты интеграций:\n"
        "- API-контракты и стабильность обменов\n"
        "- идемпотентность / ретраи / явные ошибки\n"
        "- CRM / ERP / webhook / PostgreSQL — словами ТЗ\n"
        "- без бренд-дропа Dynamics и био-списка\n"
        "- результат: рабочие обмены, не «подключу всё»"
    ),
    VERTICAL_WEB_MVP: (
        "Акценты веб-MVP / лендинг (голос Python-фрилансера):\n"
        "- структура лендинга / одностраничника\n"
        "- Next.js — только если в ТЗ\n"
        "- форма заявки / лид\n"
        "- базовый SEO и деплой\n"
        "- без упора на «чистый дизайн»"
    ),
    VERTICAL_PARSING: (
        "Акценты парсинга:\n"
        "- источники и поля выгрузки\n"
        "- антибот / стабильность обхода — коротко\n"
        "- формат результата (Excel/CSV/JSON/БД)\n"
        "- объём и обновление данных\n"
        "- без лишних URL в тексте отклика"
    ),
    VERTICAL_GENERAL: "Держись ТЗ заказчика; не раздувай лишние акценты.",
}


def vertical_hints_for(vertical: str) -> str:
    return VERTICAL_HINTS.get(vertical, VERTICAL_HINTS[VERTICAL_GENERAL])


def _normalize_suggested(raw: str | None) -> str | None:
    if not raw:
        return None
    key = " ".join(str(raw).strip().lower().replace("_", " ").split())
    if not key or key in {"другое", "other", "unknown"}:
        return None
    if key in _SUGGESTED_TYPE_MAP:
        return _SUGGESTED_TYPE_MAP[key]
    for needle, vertical in _SUGGESTED_TYPE_MAP.items():
        if needle in key:
            return vertical
    return None


def _extract_suggested(score_or_type: Any) -> str | None:
    if score_or_type is None:
        return None
    if isinstance(score_or_type, str):
        return score_or_type
    if isinstance(score_or_type, dict):
        value = score_or_type.get("suggested_project_type")
        return str(value) if value is not None else None
    value = getattr(score_or_type, "suggested_project_type", None)
    return str(value) if value is not None else None


def _infer_from_text(text: str) -> str:
    lower = text.lower()
    if _BOT_TOKEN_RE.search(lower):
        return VERTICAL_TELEGRAM_AI
    if any(
        k in lower
        for k in (
            "rag",
            "llm",
            "ocr",
            "excel",
            "классификац",
            "документ",
        )
    ):
        return VERTICAL_AUTOMATION_AI
    if any(
        k in lower
        for k in (
            "api",
            "crm",
            "erp",
            "интеграц",
            "postgresql",
            "postgres",
            "webhook",
        )
    ):
        return VERTICAL_INTEGRATION
    if any(
        k in lower
        for k in (
            "лендинг",
            "одностранич",
            "next.js",
            "nextjs",
            "сайт-визитк",
            "сайт визитк",
        )
    ):
        return VERTICAL_WEB_MVP
    if any(k in lower for k in ("парсинг", "скрап", "каталог цен")):
        return VERTICAL_PARSING
    return VERTICAL_GENERAL


def resolve_response_vertical(
    project: ProjectFull,
    score_or_type: Any = None,
) -> str:
    """Pick draft vertical id from suggested type or title+description keywords."""
    mapped = _normalize_suggested(_extract_suggested(score_or_type))
    if mapped is not None:
        return mapped
    blob = f"{project.title or ''}\n{project.full_description or ''}"
    return _infer_from_text(blob)
