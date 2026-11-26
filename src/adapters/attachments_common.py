"""Shared attachment extract/enrich for Kwork, FL.ru, Yandex Uslugi."""
# Quark platform in product language maps to Kwork adapter (no separate quark module).
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
MAX_ZIP_MEMBERS = 20
MAX_ZIP_MEMBER_BYTES = 2_000_000
MAX_ZIP_MEMBER_CHARS = 8_000

_TEXT_LIKE_SUFFIXES = (
    ".txt",
    ".md",
    ".csv",
    ".log",
    ".pdf",
    ".docx",
    ".json",
    ".xml",
)
_SKIP_ARCHIVE_SUFFIXES = (".rar", ".7z")


@dataclass(frozen=True)
class AttachmentRef:
    name: str
    url: str
    size: int | None = None


def format_attachment_block(name: str, text: str) -> str:
    return f"--- Вложение: {name} ---\n{text}"


def append_attachment_blocks(description: str, blocks: list[str]) -> str:
    base = description or ""
    if not blocks:
        return base
    suffix = "\n\n".join(blocks)
    if base.strip():
        return f"{base.rstrip()}\n\n{suffix}"
    return suffix


def download_via_browser_request(
    browser: Any,
    url: str,
    *,
    max_size: int = MAX_ATTACHMENT_BYTES,
) -> bytes | None:
    """Playwright ``page.request.get`` with soft-fail."""
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
            logger.warning(
                "attachment_download_bad_status url=%s status=%s", url, status
            )
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


def _is_text_like_member(name: str) -> bool:
    low = (name or "").lower().split("?")[0]
    if any(low.endswith(sfx) for sfx in _SKIP_ARCHIVE_SUFFIXES):
        return False
    return any(low.endswith(sfx) for sfx in _TEXT_LIKE_SUFFIXES)


def _extract_zip(data: bytes) -> str:
    """Unpack text-like zip members; skip rar/7z and nested archives."""
    parts: list[str] = []
    used = 0
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            members = [
                info
                for info in zf.infolist()
                if not info.is_dir() and _is_text_like_member(info.filename)
            ]
            for info in members[:MAX_ZIP_MEMBERS]:
                if info.file_size > MAX_ZIP_MEMBER_BYTES:
                    continue
                try:
                    raw = zf.read(info)
                except Exception:
                    continue
                if len(raw) > MAX_ZIP_MEMBER_BYTES:
                    continue
                member_name = info.filename.rsplit("/", 1)[-1] or info.filename
                text = extract_text_from_bytes(
                    raw, member_name, _allow_zip=False
                )
                if not text.strip():
                    continue
                clipped = text[:MAX_ZIP_MEMBER_CHARS]
                parts.append(f"[zip:{member_name}]\n{clipped}")
                used += len(clipped)
                if used >= DEFAULT_MAX_CHARS_TOTAL:
                    break
    except zipfile.BadZipFile:
        return ""
    except Exception:
        logger.exception("attachment_zip_extract_failed")
        return ""
    return "\n\n".join(parts).strip()


def extract_text_from_bytes(
    data: bytes,
    filename: str,
    *,
    _allow_zip: bool = True,
) -> str:
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
        if name.endswith((".json", ".xml")):
            return _decode_text(data).strip()
        if _allow_zip and name.endswith(".zip"):
            return _extract_zip(data)
        if any(name.endswith(sfx) for sfx in _SKIP_ARCHIVE_SUFFIXES):
            return ""
    except Exception:
        logger.exception("attachment_extract_failed filename=%s", filename)
        return ""
    return ""


def enrich_with_downloaded(
    description: str,
    refs: list[AttachmentRef],
    download_fn: Any,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_chars_total: int = DEFAULT_MAX_CHARS_TOTAL,
) -> str:
    """Download+extract refs and append ``--- Вложение ---`` blocks. Fail-open.

    ``download_fn`` may be ``(ref) -> bytes|None`` or ``(url) -> bytes|None``.
    """
    base = description or ""
    if not refs:
        return base
    chunks: list[str] = []
    used_chars = 0
    ok = 0
    for att in refs[: max(0, int(max_files))]:
        if used_chars >= max_chars_total:
            break
        try:
            data: bytes | None = None
            try:
                data = download_fn(att)
            except TypeError:
                data = download_fn(att.url)
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
            chunks.append(format_attachment_block(att.name, clipped))
            ok += 1
        except Exception:
            logger.exception("attachment_enrich_item_failed name=%s", att.name)
            continue
    logger.info("attachments_enriched=%s chars=%s", ok, used_chars)
    return append_attachment_blocks(base, chunks)
