"""Parse /project and /force command arguments (URL or bare id)."""
from __future__ import annotations

from src.adapters.flru_urls import extract_flru_project_id
from src.adapters.kwork_urls import extract_kwork_project_id
from src.adapters.yandex_urls import extract_yandex_order_id

PlatformKey = str  # "kwork" | "flru" | "yandex_uslugi"


def parse_project_command_arg(text: str) -> tuple[str, PlatformKey] | None:
    """Return (project_id, platform) from a URL or bare id token."""
    raw = (text or "").strip()
    if not raw:
        return None

    lower = raw.lower()
    if "fl.ru" in lower:
        fl_id = extract_flru_project_id(raw)
        if fl_id:
            return fl_id, "flru"

    yandex_id = extract_yandex_order_id(raw)
    if yandex_id:
        return yandex_id, "yandex_uslugi"

    kwork_id = extract_kwork_project_id(raw)
    if kwork_id:
        return kwork_id, "kwork"

    if raw.isdigit():
        return raw, "kwork"

    return None
