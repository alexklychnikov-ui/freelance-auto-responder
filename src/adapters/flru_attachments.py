"""FL.ru project attachments — list /download/files + enrich description."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import unquote, urljoin

import httpx

from src.adapters.attachments_common import (
    DEFAULT_MAX_CHARS_TOTAL,
    DEFAULT_MAX_FILES,
    MAX_ATTACHMENT_BYTES,
    AttachmentRef,
    download_via_browser_request,
    enrich_with_downloaded,
)
from src.adapters.flru import cookie_header_from_storage
from src.adapters.flru_urls import FLRU_ORIGIN

logger = logging.getLogger(__name__)

_FILE_HREF_RE = re.compile(
    r"""href=["']([^"']*(?:/download/files/|/files/download/|/download/file/)[^"']*)["']""",
    re.I,
)
_FILE_EXT_NAME_RE = re.compile(
    r"([^/\\?]+\.(?:pdf|docx?|txt|md|csv|zip|xlsx?|json|xml))(?:\?|$)",
    re.I,
)

LIST_ATTACHMENTS_JS = """
(() => {
  const out = [];
  const seen = new Set();
  const add = (name, url) => {
    if (!url) return;
    let u = String(url).trim();
    if (!u || u.startsWith('javascript:')) return;
    if (!/\\/download\\/files\\/|\\/files\\/download\\/|\\/download\\/file\\//i.test(u)) return;
    if (u.startsWith('/')) u = 'https://www.fl.ru' + u;
    if (seen.has(u)) return;
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
    add(label, href);
  }
  return out;
})()
"""


def _abs_flru_url(href: str) -> str:
    h = (href or "").strip()
    if not h or h.startswith("javascript:"):
        return ""
    if h.startswith("http://") or h.startswith("https://"):
        return h.split("#")[0]
    return urljoin(FLRU_ORIGIN + "/", h.lstrip("/")).split("#")[0]


def _name_from_url(url: str, fallback: str = "file") -> str:
    m = _FILE_EXT_NAME_RE.search(url or "")
    if m:
        try:
            return unquote(m.group(1))
        except Exception:
            return m.group(1)
    tail = (url or "").rstrip("/").split("/")[-1].split("?")[0]
    if tail:
        try:
            return unquote(tail)
        except Exception:
            return tail
    return fallback


def list_attachments_from_html(html: str) -> list[AttachmentRef]:
    if not html:
        return []
    out: list[AttachmentRef] = []
    seen: set[str] = set()
    for m in _FILE_HREF_RE.finditer(html):
        url = _abs_flru_url(m.group(1))
        if not url or url in seen:
            continue
        seen.add(url)
        window = html[max(0, m.start() - 80) : m.end() + 120]
        label = ""
        tm = re.search(r">([^<]{1,120})</a>", window, flags=re.I)
        if tm:
            label = re.sub(r"\s+", " ", tm.group(1)).strip()
        name = label or _name_from_url(url)
        out.append(AttachmentRef(name=name, url=url))
    return out


def list_attachments_via_browser(browser: Any) -> list[AttachmentRef]:
    try:
        raw = browser.evaluate(LIST_ATTACHMENTS_JS)
    except Exception:
        logger.exception("flru_list_attachments_evaluate_failed")
        return []
    if not isinstance(raw, list):
        return []
    out: list[AttachmentRef] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _abs_flru_url(str(item.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        name = str(item.get("name") or "").strip() or _name_from_url(url)
        out.append(AttachmentRef(name=name, url=url))
    return out


def download_attachment_http(
    url: str,
    *,
    storage_state_path: str | None = None,
    proxy_url: str | None = None,
    max_size: int = MAX_ATTACHMENT_BYTES,
    timeout_s: float = 40.0,
) -> bytes | None:
    """GET file with FL storage cookies (auth required for many docx)."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }
        cookie = cookie_header_from_storage(storage_state_path)
        if cookie:
            headers["Cookie"] = cookie
        proxy = (proxy_url or "").strip() or None
        with httpx.Client(
            timeout=timeout_s, follow_redirects=True, proxy=proxy
        ) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "flru_attach_http_status url=%s status=%s",
                    url,
                    resp.status_code,
                )
                return None
            data = resp.content
            if len(data) > max_size:
                logger.warning(
                    "flru_attach_too_large url=%s size=%s", url, len(data)
                )
                return None
            return data
    except Exception:
        logger.exception("flru_attach_http_failed url=%s", url)
        return None


def download_attachment(
    url: str,
    *,
    browser: Any | None = None,
    storage_state_path: str | None = None,
    proxy_url: str | None = None,
    max_size: int = MAX_ATTACHMENT_BYTES,
) -> bytes | None:
    if browser is not None:
        data = download_via_browser_request(browser, url, max_size=max_size)
        if data:
            return data
    return download_attachment_http(
        url,
        storage_state_path=storage_state_path,
        proxy_url=proxy_url,
        max_size=max_size,
    )


def enrich_description_with_attachments(
    description: str,
    *,
    html: str | None = None,
    browser: Any | None = None,
    storage_state_path: str | None = None,
    proxy_url: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
) -> str:
    """Append FL file texts to description. Fail-open on any error."""
    base = description or ""
    try:
        refs = list_attachments_from_html(html or "")
        if not refs and browser is not None:
            refs = list_attachments_via_browser(browser)
        if not refs:
            logger.info("flru_attachments_found=0")
            return base
        logger.info("flru_attachments_found=%s", len(refs))
        return enrich_with_downloaded(
            base,
            refs,
            lambda att: download_attachment(
                att.url,
                browser=browser,
                storage_state_path=storage_state_path,
                proxy_url=proxy_url,
            ),
            max_files=max_files,
            max_chars_total=max_chars_total,
        )
    except Exception:
        logger.exception("flru_enrich_attachments_failed")
        return base
