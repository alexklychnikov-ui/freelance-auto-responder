"""Сборка ProjectFull из текста ТЗ без ссылки на площадку."""
from __future__ import annotations

import time

from src.models import ProjectFull

TZ_PLATFORM = "telegram"
TZ_MANUAL_SOURCE_KEY = "tz_manual"
TZ_MIN_CHARS = 30


def build_tz_project(text: str) -> ProjectFull:
    body = text.strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    title = (lines[0] if lines else body)[:120]
    if len(title) < 5:
        title = body[:120]
    project_id = f"tz_{int(time.time() * 1000)}"
    return ProjectFull(
        platform=TZ_PLATFORM,
        source_key=TZ_MANUAL_SOURCE_KEY,
        project_id=project_id,
        url="",
        title=title,
        full_description=body,
    )
