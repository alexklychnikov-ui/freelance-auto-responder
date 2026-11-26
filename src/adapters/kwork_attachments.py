# Quark platform in product language maps to Kwork adapter (no separate quark module).
from __future__ import annotations

import logging
from typing import Any

from src.adapters.attachments_common import (
    DEFAULT_MAX_CHARS_TOTAL,
    DEFAULT_MAX_FILES,
    MAX_ATTACHMENT_BYTES,
    AttachmentRef,
    download_via_browser_request,
    enrich_with_downloaded,
    extract_text_from_bytes,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AttachmentRef",
    "DEFAULT_MAX_CHARS_TOTAL",
    "DEFAULT_MAX_FILES",
    "MAX_ATTACHMENT_BYTES",
    "LIST_ATTACHMENTS_JS",
    "download_attachment",
    "enrich_description_with_attachments",
    "extract_text_from_bytes",
    "list_project_attachments",
]

LIST_ATTACHMENTS_JS = """
(() => {
  const out = [];
  const seen = new Set();
  const add = (name, url, size) => {
    if (!url) return;
    const u = String(url).trim();
    if (!u || u.startsWith('javascript:') || !u.includes('/files/uploaded/')) return;
    if (seen.has(u)) return;
    seen.add(u);
    let n = String(name || '').trim();
    if (!n) {
      try { n = decodeURIComponent(u.split('/').pop() || 'file'); }
      catch (_) { n = u.split('/').pop() || 'file'; }
    }
    let sz = null;
    if (size != null && size !== '' && !Number.isNaN(Number(size))) sz = Number(size);
    out.push({ name: n, url: u, size: sz });
  };

  for (const a of document.querySelectorAll('a[href*="/files/uploaded/"]')) {
    const href = a.getAttribute('href') || a.href || '';
    if (!href || href.startsWith('javascript:')) continue;
    const label = (a.textContent || '').trim()
      || a.getAttribute('download')
      || a.getAttribute('title')
      || '';
    add(label, href, null);
  }

  const collectFiles = (files) => {
    if (!Array.isArray(files)) return;
    for (const f of files) {
      if (!f || typeof f !== 'object') continue;
      add(f.fname || f.name || f.filename || '', f.url || f.href || '', f.size);
    }
  };

  try {
    const sd = window.stateData || {};
    const want = sd.wantModel || sd.want || sd.wantData || {};
    collectFiles(want.files);
    collectFiles(sd.files);
    for (const key of Object.keys(sd)) {
      if (!/wants_.*_data/i.test(key)) continue;
      const block = sd[key];
      if (block && typeof block === 'object') collectFiles(block.files);
    }
  } catch (_) {}

  return out;
})()
"""


def list_project_attachments(browser: Any) -> list[AttachmentRef]:
    try:
        raw = browser.evaluate(LIST_ATTACHMENTS_JS)
    except Exception:
        logger.exception("list_project_attachments evaluate failed")
        return []
    if not isinstance(raw, list):
        return []
    out: list[AttachmentRef] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url.startswith("javascript:") or "/files/uploaded/" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        name = str(item.get("name") or "").strip() or url.rsplit("/", 1)[-1] or "file"
        size_raw = item.get("size")
        size: int | None
        try:
            size = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError):
            size = None
        out.append(AttachmentRef(name=name, url=url, size=size))
    return out


def download_attachment(
    browser: Any,
    url: str,
    *,
    max_size: int = MAX_ATTACHMENT_BYTES,
) -> bytes | None:
    return download_via_browser_request(browser, url, max_size=max_size)


def enrich_description_with_attachments(
    browser: Any,
    description: str,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
) -> str:
    base = description or ""
    try:
        attachments = list_project_attachments(browser)
    except Exception:
        logger.exception("enrich_attachments_list_failed")
        return base

    if not attachments:
        logger.info("attachments_found=0")
        return base

    logger.info("attachments_found=%s", len(attachments))
    return enrich_with_downloaded(
        base,
        attachments,
        lambda att: download_attachment(browser, att.url),
        max_files=max_files,
        max_chars_total=max_chars_total,
    )
