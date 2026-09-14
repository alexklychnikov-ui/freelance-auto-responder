from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from src.models import ProjectFull

Kind = Literal["url", "attachment"]
Role = Literal[
    "reference",
    "data_source",
    "documentation",
    "repository",
    "unknown",
]

PLATFORM_HOST_SUFFIXES = (
    "kwork.ru",
    "fl.ru",
    "uslugi.yandex.ru",
)

_SKIP_SCHEMES = ("javascript:", "data:", "file:")

# Bare "host.ext" tokens that are filenames, not domains
_FILE_EXT_TLDS = frozenset(
    {
        "txt",
        "csv",
        "tsv",
        "md",
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "xlsm",
        "json",
        "xml",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "svg",
        "zip",
        "rar",
        "7z",
        "gz",
        "tar",
        "py",
        "js",
        "ts",
        "tsx",
        "jsx",
        "css",
        "html",
        "htm",
        "log",
        "sql",
        "yml",
        "yaml",
        "ini",
        "conf",
        "cfg",
        "map",
        "lock",
        "bak",
        "bin",
        "exe",
        "dll",
        "so",
        "dmg",
        "iso",
        "mp3",
        "mp4",
        "wav",
        "avi",
        "mov",
        "pptx",
        "ppt",
        "odt",
        "rtf",
    }
)

_TRAIL_PUNCT = ".,;:!?)]}>\"'»…”"

_ATTACHMENT_MARKER_RE = re.compile(
    r"^---\s*Вложение:\s*(.+?)\s*---\s*$",
    re.MULTILINE,
)

# http(s)://… or www.…
_SCHEMED_OR_WWW_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\[\]{}\|\"']+",
    re.IGNORECASE,
)

# Bare domains incl. Cyrillic / .рф (IDN-ish labels)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w./-])"
    r"(?:"
    r"[a-zA-Zа-яА-ЯёЁ0-9]"
    r"(?:[a-zA-Zа-яА-ЯёЁ0-9-]{0,61}[a-zA-Zа-яА-ЯёЁ0-9])?"
    r"\."
    r")+"
    r"(?:рф|[a-zA-Z]{2,24})"
    r"(?:/[^\s<>\[\]{}\|\"']*)?",
    re.IGNORECASE,
)

_DATA_SOURCE_HINTS = (
    "парсится",
    "парсинг",
    "парсить",
    "источник",
    "отсюда",
    "собирать с",
    "данные с",
)
_REFERENCE_HINTS = (
    "аналог",
    "пример",
    "похож",
    "как на",
    "типа",
    "reference",
    "similar",
)
_DOCUMENTATION_HINTS = (
    "документац",
    "docs",
    "swagger",
    "openapi",
    "/api",
    " api ",
    "readme",
)
_REPOSITORY_HINTS = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "репозитор",
)
# Whole-word only — bare "repo" is a substring of "report"
_REPO_WORD_RE = re.compile(r"(?<![a-zа-яё])repo(?![a-zа-яё])", re.IGNORECASE)

_ROLE_PRIORITY: dict[Role, int] = {
    "data_source": 100,
    "documentation": 80,
    "repository": 70,
    "reference": 60,
    "unknown": 40,
}


@dataclass(frozen=True)
class ResourceCandidate:
    kind: Kind
    role: Role
    input_ref: str
    title_hint: str | None = None
    priority: int = 0


def _strip_trailing_punct(raw: str) -> str:
    s = raw.strip()
    while s and s[-1] in _TRAIL_PUNCT:
        s = s[:-1]
    return s.strip()

def _host_of(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.hostname or "").lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _is_skipped_scheme(raw: str) -> bool:
    lower = raw.strip().lower()
    return any(lower.startswith(s) for s in _SKIP_SCHEMES)


def _is_platform_self_url(url: str) -> bool:
    host = _host_of(url)
    if not host:
        return False
    for suffix in PLATFORM_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def _is_file_ext_host(url: str) -> bool:
    """Reject filename-like hosts (report.txt, README.md, archive.zip)."""
    host = _host_of(url)
    if not host or "." not in host:
        return False
    tld = host.rsplit(".", 1)[-1].lower()
    return tld in _FILE_EXT_TLDS


def _normalize_url(raw: str) -> str:
    s = _strip_trailing_punct(raw)
    if not s:
        return ""
    if s.lower().startswith("www."):
        s = "https://" + s
    elif "://" not in s:
        s = "https://" + s
    return s


def _dedupe_key(url: str) -> str:
    norm = _normalize_url(url)
    try:
        p = urlparse(norm)
        host = (p.hostname or "").lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        path = (p.path or "").rstrip("/")
        query = f"?{p.query}" if p.query else ""
        return f"{host}{path}{query}".lower()
    except Exception:
        return norm.lower()


def _context_window(text: str, start: int, end: int, radius: int = 80) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi].lower()


def _classify_role(url: str, context: str) -> Role:
    ctx = f" {context} "
    url_l = url.lower()
    if any(h in ctx for h in _DATA_SOURCE_HINTS):
        return "data_source"
    if any(h in ctx or h in url_l for h in _REPOSITORY_HINTS) or _REPO_WORD_RE.search(
        ctx
    ):
        return "repository"
    if any(h in ctx or h in url_l for h in _DOCUMENTATION_HINTS):
        return "documentation"
    if any(h in ctx for h in _REFERENCE_HINTS):
        return "reference"
    return "unknown"


def _iter_url_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []

    def _overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in occupied)

    for m in _SCHEMED_OR_WWW_RE.finditer(text):
        raw = _strip_trailing_punct(m.group(0))
        if not raw:
            continue
        start = m.start()
        end = start + len(raw)
        spans.append((start, end, raw))
        occupied.append((start, end))

    for m in _BARE_DOMAIN_RE.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        raw = _strip_trailing_punct(m.group(0))
        if not raw:
            continue
        # Avoid matching file-like tokens without real host structure
        if raw.count(".") < 1:
            continue
        start = m.start()
        end = start + len(raw)
        spans.append((start, end, raw))
        occupied.append((start, end))

    spans.sort(key=lambda x: x[0])
    return spans


def extract_attachment_markers(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for m in _ATTACHMENT_MARKER_RE.finditer(text or ""):
        name = (m.group(1) or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def extract_inlined_attachment(text: str, name: str) -> str | None:
    """Return body under ``--- Вложение: {name} ---`` if already enriched into TZ."""
    target = (name or "").strip().lower()
    if not target or not text:
        return None
    matches = list(_ATTACHMENT_MARKER_RE.finditer(text))
    for i, m in enumerate(matches):
        marker_name = (m.group(1) or "").strip()
        if marker_name.lower() != target:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        return body or None
    return None


def extract_url_candidates(
    text: str,
    *,
    max_urls: int = 3,
) -> list[ResourceCandidate]:
    if not text or max_urls <= 0:
        return []

    found: list[ResourceCandidate] = []
    seen: set[str] = set()

    for start, end, raw in _iter_url_spans(text):
        if _is_skipped_scheme(raw):
            continue
        norm = _normalize_url(raw)
        if not norm or _is_platform_self_url(norm) or _is_file_ext_host(norm):
            continue
        key = _dedupe_key(norm)
        if not key or key in seen:
            continue
        seen.add(key)
        ctx = _context_window(text, start, end)
        role = _classify_role(norm, ctx)
        found.append(
            ResourceCandidate(
                kind="url",
                role=role,
                input_ref=norm,
                title_hint=_host_of(norm) or None,
                priority=_ROLE_PRIORITY[role],
            )
        )

    found.sort(key=lambda c: (-c.priority, c.input_ref))
    return found[:max_urls]


def discover(
    project: ProjectFull,
    *,
    max_urls: int = 3,
) -> list[ResourceCandidate]:
    """Discover URL/attachment candidates from project text. No network."""
    parts = [
        project.title or "",
        project.full_description or "",
        " ".join(project.tags or []),
    ]
    # Project listing URL is platform self — never a research target
    text = "\n".join(p for p in parts if p)

    urls = extract_url_candidates(text, max_urls=max_urls)
    # Drop candidates that match the project page itself (extra safety)
    project_key = _dedupe_key(project.url) if project.url else ""
    if project_key:
        urls = [u for u in urls if _dedupe_key(u.input_ref) != project_key]

    attachments: list[ResourceCandidate] = []
    for name in extract_attachment_markers(project.full_description or ""):
        attachments.append(
            ResourceCandidate(
                kind="attachment",
                role="documentation",
                input_ref=name,
                title_hint=name,
                priority=50,
            )
        )

    out = [*urls, *attachments]
    # Stable: URLs already priority-sorted; attachments keep marker order.
    out.sort(key=lambda c: (-c.priority, 0 if c.kind == "url" else 1))
    return out
