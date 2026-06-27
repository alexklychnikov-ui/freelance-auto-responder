from __future__ import annotations

from src.adapters.kwork import _normalize_editor_plaintext


def test_normalize_splits_newlines_into_paragraphs() -> None:
    text = "передача\nРеализация\n\nТЗ\nАудит"
    out = _normalize_editor_plaintext(text)
    assert out == "передача\n\nРеализация\n\nТЗ\n\nАудит"


def test_normalize_single_line_collapses_whitespace() -> None:
    assert _normalize_editor_plaintext("  a\n b ", single_line=True) == "a b"
