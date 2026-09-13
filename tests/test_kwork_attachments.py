from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch
from zipfile import ZipFile, ZipInfo

from src.adapters.kwork_attachments import (
    AttachmentRef,
    download_attachment,
    enrich_description_with_attachments,
    extract_text_from_bytes,
    list_project_attachments,
)


def _minimal_docx_bytes(text: str) -> bytes:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>"""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr(ZipInfo("word/document.xml"), document_xml.encode("utf-8"))
        zf.writestr("[Content_Types].xml", b"<Types></Types>")
    return buf.getvalue()


def test_extract_text_txt() -> None:
    data = "Нужна админка и комиссия".encode("utf-8")
    assert "админка" in extract_text_from_bytes(data, "tz.txt")


def test_extract_text_txt_cp1251() -> None:
    data = "ТЗ для оплаты".encode("cp1251")
    assert "оплаты" in extract_text_from_bytes(data, "brief.txt")


def test_extract_text_pdf() -> None:
    page = MagicMock()
    page.extract_text.return_value = "Platega.io / комиссия / админка"
    reader = MagicMock()
    reader.pages = [page]
    with patch("pypdf.PdfReader", return_value=reader) as pdf_reader:
        out = extract_text_from_bytes(b"%PDF-fake", "Техническое задание.pdf")
    pdf_reader.assert_called_once()
    assert "Platega.io" in out
    assert "комиссия" in out
    assert "админка" in out


def test_extract_text_docx() -> None:
    data = _minimal_docx_bytes("Интеграция Platega.io и админка")
    text = extract_text_from_bytes(data, "tz.docx")
    assert "Platega.io" in text
    assert "админка" in text


def test_extract_unknown_extension_empty() -> None:
    assert extract_text_from_bytes(b"\x00\x01\x02", "photo.png") == ""


def test_list_project_attachments_from_evaluate() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = [
        {
            "name": "Техническое задание.pdf",
            "url": "https://kwork.ru/files/uploaded/1/tz.pdf",
            "size": 100,
        },
        {
            "name": "skip",
            "url": "javascript:void(0)",
            "size": None,
        },
        {
            "name": "dup",
            "url": "https://kwork.ru/files/uploaded/1/tz.pdf",
            "size": 100,
        },
    ]
    refs = list_project_attachments(browser)
    assert len(refs) == 1
    assert refs[0].name.startswith("Техническое")
    assert "/files/uploaded/" in refs[0].url


def test_list_project_attachments_non_list() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = {"full_description": "x"}
    assert list_project_attachments(browser) == []


def test_download_soft_fail_without_ensure_page() -> None:
    browser = MagicMock(spec=[])
    assert download_attachment(browser, "https://kwork.ru/files/uploaded/x.pdf") is None


def test_download_via_page_request() -> None:
    resp = MagicMock()
    resp.status = 200
    resp.body.return_value = b"%PDF-1.4"
    page = MagicMock()
    page.request.get.return_value = resp
    browser = MagicMock()
    browser._ensure_page.return_value = page
    data = download_attachment(browser, "https://kwork.ru/files/uploaded/x.pdf")
    assert data == b"%PDF-1.4"
    page.request.get.assert_called_once()


def test_enrich_description_appends_attachment() -> None:
    pdf_text = "Platega.io комиссия админка Steam API"
    browser = MagicMock()
    browser.evaluate.return_value = [
        {
            "name": "Техническое задание.pdf",
            "url": "https://kwork.ru/files/uploaded/abc/tz.pdf",
            "size": 10,
        }
    ]

    with (
        patch(
            "src.adapters.kwork_attachments.download_attachment",
            return_value=b"%PDF",
        ),
        patch(
            "src.adapters.kwork_attachments.extract_text_from_bytes",
            return_value=pdf_text,
        ),
    ):
        short = "ТЗ в прикрепленном файле"
        out = enrich_description_with_attachments(browser, short)

    assert short in out
    assert "--- Вложение: Техническое задание.pdf ---" in out
    assert "Platega.io" in out
    assert "комиссия" in out


def test_enrich_weak_desc_still_gets_body() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = [
        {
            "name": "tz.txt",
            "url": "https://kwork.ru/files/uploaded/1/tz.txt",
            "size": 5,
        }
    ]
    with (
        patch(
            "src.adapters.kwork_attachments.download_attachment",
            return_value="Полное ТЗ: админка платежей".encode("utf-8"),
        ),
        patch(
            "src.adapters.kwork_attachments.extract_text_from_bytes",
            side_effect=extract_text_from_bytes,
        ),
    ):
        out = enrich_description_with_attachments(browser, "см. файл")
    assert "админка платежей" in out
    assert "--- Вложение: tz.txt ---" in out


def test_enrich_truncates_total_chars() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = [
        {
            "name": "big.txt",
            "url": "https://kwork.ru/files/uploaded/1/big.txt",
            "size": 1000,
        }
    ]
    with (
        patch(
            "src.adapters.kwork_attachments.download_attachment",
            return_value=("X" * 500).encode("utf-8"),
        ),
    ):
        out = enrich_description_with_attachments(
            browser,
            "base",
            max_chars_total=50,
        )
    marker = "--- Вложение: big.txt ---\n"
    assert marker in out
    attached = out.split(marker, 1)[1]
    assert len(attached) == 50


def test_enrich_soft_fail_on_download_error() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = [
        {
            "name": "tz.pdf",
            "url": "https://kwork.ru/files/uploaded/1/tz.pdf",
            "size": 1,
        }
    ]
    with patch(
        "src.adapters.kwork_attachments.download_attachment",
        side_effect=RuntimeError("boom"),
    ):
        out = enrich_description_with_attachments(browser, "short desc")
    assert out == "short desc"


def test_list_returns_attachment_ref_type() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = [
        {"name": "a.txt", "url": "https://kwork.ru/files/uploaded/a.txt", "size": None}
    ]
    refs = list_project_attachments(browser)
    assert isinstance(refs[0], AttachmentRef)
