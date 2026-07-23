"""Platforms with manual copy flow (TG text → user submits on site)."""
from __future__ import annotations

MANUAL_COPY_PLATFORMS = frozenset({"yandex_uslugi", "flru", "telegram"})

MANUAL_COPY_HEADERS: dict[str, tuple[str, str]] = {
    "yandex_uslugi": ("Яндекс Услуги", "заказ"),
    "flru": ("FL.ru", "проект"),
    "telegram": ("ТЗ из TG", "заказ"),
}


def is_manual_copy_platform(platform: str) -> bool:
    return platform in MANUAL_COPY_PLATFORMS


def journal_status_for_confirm(platform: str) -> tuple[str, str]:
    if is_manual_copy_platform(platform):
        return "Отправлен", "Жду ответа"
    return "Подготовлен", "Жду ответа"
