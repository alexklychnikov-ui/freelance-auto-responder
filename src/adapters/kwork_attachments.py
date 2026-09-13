from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 5_000_000
DEFAULT_MAX_FILES = 3
DEFAULT_MAX_CHARS_TOTAL = 20_000

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


@dataclass(frozen=True)
class AttachmentRef:
    name: str
    url: str
    size: int | None = None


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
    try:
        ensure = getattr(browser, "_ensure_page", None)
        if not callable(ensure):
            return None
        page = ensure()
        if page is None or not hasattr(page, "request"):
            return None
        resp = page.request.get(url)
        status = getattr(resp, "status", None)
        if status is not None and int(status) != 200:
            logger.warning("attachment_download_bad_status url=%s status=%s", url, status)
            return None
        body = resp.body() if callable(getattr(resp, "body", None)) else None
        if not isinstance(body, (bytes, bytearray)):
            return None
        data = bytes(body)
        if len(data) > max_size:
            logger.warning(
                "attachment_too_large url=%s size=%s max=%s",
                url,
                len(data),
                max_size,
            )
            return None
        return data
    except Exception:
        logger.exception("attachment_download_failed url=%s", url)
        return None


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError):
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return re.sub(r"<[^>]+>", " ", xml.decode("utf-8", errors="replace"))
    texts: list[str] = []
    for el in root.iter():
        if el.tag.endswith("}t") and el.text:
            texts.append(el.text)
        elif el.tag.endswith("}tab"):
            texts.append("\t")
        elif el.tag.endswith("}br") or el.tag.endswith("}cr"):
            texts.append("\n")
        elif el.tag.endswith("}p"):
            texts.append("\n")
    return re.sub(r"[ \t]+\n", "\n", "".join(texts)).strip()


def extract_text_from_bytes(data: bytes, filename: str) -> str:
    if not data:
        return ""
    name = (filename or "").lower().split("?")[0]
    try:
        if name.endswith(".pdf"):
            return _extract_pdf(data)
        if name.endswith(".docx"):
            return _extract_docx(data)
        if name.endswith((".txt", ".md", ".csv", ".log")):
            return _decode_text(data).strip()
    except Exception:
        logger.exception("attachment_extract_failed filename=%s", filename)
        return ""
    return ""


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
    chunks: list[str] = []
    used_chars = 0
    ok = 0

    for att in attachments[: max(0, int(max_files))]:
        if used_chars >= max_chars_total:
            break
        try:
            data = download_attachment(browser, att.url)
            if not data:
                logger.warning("attachment_download_empty name=%s", att.name)
                continue
            text = extract_text_from_bytes(data, att.name)
            if not text.strip():
                logger.warning("attachment_extract_empty name=%s", att.name)
                continue
            remain = max_chars_total - used_chars
            clipped = text[:remain]
            used_chars += len(clipped)
            chunks.append(f"--- Вложение: {att.name} ---\n{clipped}")
            ok += 1
        except Exception:
            logger.exception("attachment_enrich_item_failed name=%s", att.name)
            continue

    logger.info("attachments_enriched=%s chars=%s", ok, used_chars)
    if not chunks:
        return base
    suffix = "\n\n".join(chunks)
    if base.strip():
        return f"{base.rstrip()}\n\n{suffix}"
    return suffix
