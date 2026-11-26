"""Yandex Uslugi order attachments — list file links + enrich description.

URL patterns are conservative and based on observable HTML conventions
(file extensions, ``download`` attr, yadi.sk / disk.yandex hosts). No invented
platform-private download paths — live SPA selectors were unavailable
(stale local session → passport).
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from src.adapters.attachments_common import (
    DEFAULT_MAX_CHARS_TOTAL,
    DEFAULT_MAX_FILES,
    MAX_ATTACHMENT_BYTES,
    AttachmentRef,
    download_via_browser_request,
    enrich_with_downloaded,
)
from src.adapters.yandex_urls import YANDEX_USLUGI_ORIGIN

logger = logging.getLogger(__name__)

_FILE_EXT_RE = re.compile(
    r"\.(?:pdf|docx?|txt|md|csv|zip|xlsx?|json|xml|rtf)(?:\?|$)",
    re.I,
)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_YANDEX_FILE_HOST_RE = re.compile(
    r"(?:^|\.)(?:yadi\.sk|disk\.yandex\.(?:ru|com)|downloader\.dst\.yandex\.ru|"
    r"getfile\.dokpub\.com|docviewer\.yandex\.(?:ru|com))$",
    re.I,
)

LIST_ATTACHMENTS_JS = """
(() => {
  const out = [];
  const seen = new Set();
  const fileExt = /\\.(?:pdf|docx?|txt|md|csv|zip|xlsx?|json|xml|rtf)(?:\\?|$)/i;
  const fileHost = /(?:yadi\\.sk|disk\\.yandex\\.(?:ru|com)|downloader\\.dst\\.yandex\\.ru|docviewer\\.yandex)/i;
  const add = (name, url) => {
    if (!url) return;
    let u = String(url).trim();
    if (!u || u.startsWith('javascript:') || u.startsWith('#')) return;
    if (u.startsWith('/')) u = 'https://uslugi.yandex.ru' + u;
    if (seen.has(u)) return;
    const looksFile = fileExt.test(u) || fileHost.test(u);
    if (!looksFile) return;
    seen.add(u);
    let n = String(name || '').trim();
    if (!n) {
      try { n = decodeURIComponent((u.split('/').pop() || 'file').split('?')[0]); }
      catch (_) { n = (u.split('/').pop() || 'file').split('?')[0]; }
    }
    out.push({ name: n, url: u });
  };
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || a.href || '';
    const label = (a.textContent || '').trim()
      || a.getAttribute('download')
      || a.getAttribute('title')
      || '';
    const dl = a.getAttribute('download');
    if (dl || fileExt.test(href) || fileExt.test(label) || fileHost.test(href)) {
      add(label || dl || '', href);
    }
  }
  return out;
})()
"""


def _abs_url(href: str) -> str:
    h = (href or "").strip()
    if not h or h.startswith(("javascript:", "#", "mailto:")):
        return ""
    if h.startswith("http://") or h.startswith("https://"):
        return h.split("#")[0]
    return urljoin(YANDEX_USLUGI_ORIGIN + "/", h.lstrip("/")).split("#")[0]


def _host_ok(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host.endswith("uslugi.yandex.ru") or host == "yandex.ru":
        # Same-origin relative file links / CDN under yandex.
        return True
    return bool(_YANDEX_FILE_HOST_RE.search(host))


def _looks_like_file(url: str, label: str = "") -> bool:
    blob = f"{url} {label}"
    if _FILE_EXT_RE.search(blob):
        return True
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return bool(host and _YANDEX_FILE_HOST_RE.search(host))


def _name_from(url: str, label: str = "") -> str:
    if label and _FILE_EXT_RE.search(label):
        return re.sub(r"\s+", " ", label).strip()[:200]
    m = _FILE_EXT_RE.search(url or "")
    if m:
        tail = (url or "").split("?")[0].rstrip("/").split("/")[-1]
        try:
            return unquote(tail) or "file"
        except Exception:
            return tail or "file"
    if label.strip():
        return re.sub(r"\s+", " ", label).strip()[:200]
    return "file"


def list_attachments_from_html(html: str) -> list[AttachmentRef]:
    if not html:
        return []
    out: list[AttachmentRef] = []
    seen: set[str] = set()
    for m in _HREF_RE.finditer(html):
        url = _abs_url(m.group(1))
        if not url or url in seen:
            continue
        window = html[max(0, m.start() - 60) : m.end() + 100]
        label = ""
        tm = re.search(r">([^<]{1,120})</a>", window, flags=re.I)
        if tm:
            label = re.sub(r"\s+", " ", tm.group(1)).strip()
        dl = re.search(r'download=["\']([^"\']*)["\']', window, flags=re.I)
        if dl and dl.group(1):
            label = label or dl.group(1)
        if not _looks_like_file(url, label):
            continue
        if not _host_ok(url):
            # External http file links still useful (public TZ).
            if not _FILE_EXT_RE.search(url) and not _FILE_EXT_RE.search(label):
                continue
        seen.add(url)
        out.append(AttachmentRef(name=_name_from(url, label), url=url))
    return out


def list_attachments_via_browser(browser: Any) -> list[AttachmentRef]:
    try:
        raw = browser.evaluate(LIST_ATTACHMENTS_JS)
    except Exception:
        logger.exception("yandex_list_attachments_evaluate_failed")
        return []
    if not isinstance(raw, list):
        return []
    out: list[AttachmentRef] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _abs_url(str(item.get("url") or ""))
        if not url or url in seen:
            continue
        name = str(item.get("name") or "").strip() or _name_from(url)
        if not _looks_like_file(url, name):
            continue
        seen.add(url)
        out.append(AttachmentRef(name=name, url=url))
    return out


def download_attachment(
    browser: Any,
    url: str,
    *,
    max_size: int = MAX_ATTACHMENT_BYTES,
) -> bytes | None:
    return download_via_browser_request(browser, url, max_size=max_size)


def enrich_description_with_attachments(
    description: str,
    *,
    html: str | None = None,
    browser: Any | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
) -> str:
    """Append Yandex order file texts. Fail-open."""
    base = description or ""
    try:
        refs = list_attachments_from_html(html or "")
        if not refs and browser is not None:
            refs = list_attachments_via_browser(browser)
        if not refs:
            logger.info("yandex_attachments_found=0")
            return base
        if browser is None:
            logger.warning("yandex_attachments_no_browser refs=%s", len(refs))
            return base
        logger.info("yandex_attachments_found=%s", len(refs))
        return enrich_with_downloaded(
            base,
            refs,
            lambda att: download_attachment(browser, att.url),
            max_files=max_files,
            max_chars_total=max_chars_total,
        )
    except Exception:
        logger.exception("yandex_enrich_attachments_failed")
        return base
