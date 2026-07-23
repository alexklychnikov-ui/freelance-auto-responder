"""FL.ru URL helpers."""
from __future__ import annotations

import re

_PROJECT_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?fl\.ru/projects/(\d+)(?:/[^/\s\"']*)?",
    re.I,
)

FLRU_ORIGIN = "https://www.fl.ru"


def extract_flru_project_id(text: str) -> str | None:
    if not text:
        return None
    m = _PROJECT_URL_RE.search(text.strip())
    if m:
        return m.group(1)
    stripped = text.strip()
    if stripped.isdigit() and len(stripped) >= 6:
        return stripped
    return None


def flru_project_url(project_id: str) -> str:
    pid = str(project_id).strip()
    return f"{FLRU_ORIGIN}/projects/{pid}/"
