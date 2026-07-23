"""FL.ru URL helpers."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
    return None


def flru_project_url(project_id: str) -> str:
    pid = str(project_id).strip()
    return f"{FLRU_ORIGIN}/projects/{pid}/"


def ensure_flru_for_all(url: str) -> str:
    """Добавить for_all=1 — фильтр «Не требуется оплата отклика»."""
    raw = (url or "").strip()
    if not raw:
        return f"{FLRU_ORIGIN}/projects/?kind=1&for_all=1"
    parsed = urlparse(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["for_all"] = "1"
    return urlunparse(parsed._replace(query=urlencode(query)))
