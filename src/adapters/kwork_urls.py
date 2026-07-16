from __future__ import annotations

import re

_KWORK_HOST = re.compile(r"(?:https?://)?(?:www\.)?kwork\.ru", re.I)
_PROJECT_PATH = re.compile(
    r"kwork\.ru/projects/(\d+)(?:/view)?(?:[/?#]|$)",
    re.I,
)
_NEW_OFFER_QUERY = re.compile(
    r"kwork\.ru/new_offer\?(?:[^#]*&)?project=(\d+)",
    re.I,
)
_PROJECT_QUERY = re.compile(
    r"[?&]project=(\d+)",
    re.I,
)


def extract_kwork_project_id(text: str) -> str | None:
    """Extract exchange project id from Kwork URL embedded in free text."""
    raw = (text or "").strip()
    if not raw:
        return None

    for pattern in (_PROJECT_PATH, _NEW_OFFER_QUERY):
        match = pattern.search(raw)
        if match:
            return match.group(1)

    if _KWORK_HOST.search(raw):
        match = _PROJECT_QUERY.search(raw)
        if match:
            return match.group(1)

    return None


def kwork_project_view_url(project_id: str) -> str:
    return f"https://kwork.ru/projects/{project_id}/view"
