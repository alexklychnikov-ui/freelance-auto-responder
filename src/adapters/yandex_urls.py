"""Yandex Uslugi (Исполнители) URL helpers."""
from __future__ import annotations

import re

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_ORDER_URL_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)?uslugi\.yandex\.ru/order/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)

YANDEX_USLUGI_ORIGIN = "https://uslugi.yandex.ru"


def extract_yandex_order_id(text: str) -> str | None:
    """Extract order UUID from uslugi.yandex.ru/order/{uuid} or bare UUID."""
    if not text:
        return None
    m = _ORDER_URL_RE.search(text)
    if m:
        return m.group(1).lower()
    m = _UUID_RE.search(text.strip())
    if m and "uslugi.yandex" in text.lower():
        return m.group(0).lower()
    # bare UUID only if the whole token looks like one
    stripped = text.strip()
    if _UUID_RE.fullmatch(stripped):
        return stripped.lower()
    return None


def yandex_order_url(order_id: str) -> str:
    return f"{YANDEX_USLUGI_ORIGIN}/order/{order_id}"
