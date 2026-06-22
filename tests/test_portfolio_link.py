from __future__ import annotations

from src.responses.portfolio_link import PORTFOLIO_URL, ensure_portfolio_link


def test_ensure_portfolio_link_appends() -> None:
    text = ensure_portfolio_link("Готов взяться за задачу.")
    assert PORTFOLIO_URL in text
    assert "Портфолио:" in text


def test_ensure_portfolio_link_skips_if_present() -> None:
    original = f"Кейсы: {PORTFOLIO_URL}"
    assert ensure_portfolio_link(original) == original
