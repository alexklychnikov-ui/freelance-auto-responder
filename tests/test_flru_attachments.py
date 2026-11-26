from __future__ import annotations

from unittest.mock import patch

from src.adapters.flru_attachments import (
    enrich_description_with_attachments,
    list_attachments_from_html,
)


def test_list_attachments_from_html_download_files() -> None:
    html = """
    <div class="files">
      <a href="/download/files/eff67d27-5d84-5a3b-803c-c4f7e91893ed/projects/f_4306b07275e352af.docx">
        ТЗ.docx
      </a>
      <a href="https://www.fl.ru/download/files/aaa/bbb/brief.pdf">brief.pdf</a>
    </div>
    """
    refs = list_attachments_from_html(html)
    assert len(refs) == 2
    assert all("/download/files/" in r.url for r in refs)
    assert any("docx" in r.name.lower() or "тз" in r.name.lower() for r in refs)


def test_enrich_description_with_mock_download() -> None:
    html = (
        '<a href="https://www.fl.ru/download/files/x/y/tz.txt">tz.txt</a>'
    )
    with patch(
        "src.adapters.flru_attachments.download_attachment",
        return_value="Нужен бот, в ТЗ денег нет".encode("utf-8"),
    ):
        out = enrich_description_with_attachments(
            "Краткое описание",
            html=html,
            storage_state_path=None,
        )
    assert "Краткое описание" in out
    assert "--- Вложение: tz.txt ---" in out
    assert "в ТЗ денег нет" in out


def test_enrich_soft_fail() -> None:
    html = '<a href="/download/files/a/b/x.pdf">x.pdf</a>'
    with patch(
        "src.adapters.flru_attachments.download_attachment",
        side_effect=RuntimeError("boom"),
    ):
        out = enrich_description_with_attachments("base", html=html)
    assert out == "base"
