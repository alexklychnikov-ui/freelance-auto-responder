from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.adapters.yandex_attachments import (
    enrich_description_with_attachments,
    list_attachments_from_html,
)


def test_list_attachments_file_ext_and_yadi() -> None:
    html = """
    <main>
      <a href="/files/order/abc/tz.docx" download="tz.docx">ТЗ.docx</a>
      <a href="https://yadi.sk/i/ExamplePublicKey">файл на Диске</a>
      <a href="/order/11111111-2222-3333-4444-555555555555">не файл</a>
    </main>
    """
    refs = list_attachments_from_html(html)
    urls = [r.url for r in refs]
    assert any(u.endswith(".docx") or "tz.docx" in u for u in urls)
    assert any("yadi.sk" in u for u in urls)
    assert not any("/order/" in u and "docx" not in u for u in urls)


def test_enrich_with_mock_download() -> None:
    html = '<a href="https://uslugi.yandex.ru/files/x/brief.txt">brief.txt</a>'
    browser = MagicMock()
    with patch(
        "src.adapters.yandex_attachments.download_attachment",
        return_value="Сделать сайт-каталог".encode("utf-8"),
    ):
        out = enrich_description_with_attachments(
            "см. файл", html=html, browser=browser
        )
    assert "--- Вложение:" in out
    assert "сайт-каталог" in out


def test_enrich_without_browser_keeps_base() -> None:
    html = '<a href="https://uslugi.yandex.ru/files/x/brief.txt">brief.txt</a>'
    out = enrich_description_with_attachments("только текст", html=html, browser=None)
    assert out == "только текст"
