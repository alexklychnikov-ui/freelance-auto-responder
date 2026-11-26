from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile, ZipInfo

from src.adapters.attachments_common import (
    AttachmentRef,
    enrich_with_downloaded,
    extract_text_from_bytes,
)


def test_extract_zip_text_members() -> None:
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr(ZipInfo("readme.txt"), "ТЗ: парсер каталога".encode("utf-8"))
        zf.writestr(ZipInfo("nested/notes.md"), "# scope\nбез цены".encode("utf-8"))
        zf.writestr(ZipInfo("skip.bin"), b"\x00\x01\x02")
    text = extract_text_from_bytes(buf.getvalue(), "pack.zip")
    assert "парсер каталога" in text
    assert "без цены" in text
    assert "skip.bin" not in text


def test_extract_rar_skipped() -> None:
    assert extract_text_from_bytes(b"Rar!\x00", "archive.rar") == ""


def test_enrich_with_downloaded_marker() -> None:
    refs = [AttachmentRef(name="a.txt", url="https://example.com/a.txt")]
    out = enrich_with_downloaded(
        "base",
        refs,
        lambda _att: "тело вложения".encode("utf-8"),
    )
    assert "base" in out
    assert "--- Вложение: a.txt ---" in out
    assert "тело вложения" in out
