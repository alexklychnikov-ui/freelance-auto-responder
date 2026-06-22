from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.adapters.kwork import (
    KworkAdapter,
    parse_listing_from_html,
    parse_project_from_html,
)
from src.models import ProjectFull, ProjectPreview


FIXTURES = Path(__file__).parent / "fixtures"


class FakeBrowser:
    def __init__(self, listing_html: str, project_html: str) -> None:
        self.listing_html = listing_html
        self.project_html = project_html
        self.last_url: str | None = None

    def navigate(self, url: str) -> None:
        self.last_url = url

    def snapshot(self) -> str:
        if self.last_url and "/projects/" in self.last_url and "?" not in self.last_url:
            return self.project_html
        return self.listing_html

    def click(self, ref_or_selector: str) -> None:
        pass

    def fill(self, ref_or_selector: str, text: str) -> None:
        pass

    def evaluate(self, js: str) -> Any:
        if self.last_url and "/projects/" in self.last_url and "?" not in self.last_url:
            return parse_project_from_html(self.project_html, project_id="3201949")
        return parse_listing_from_html(self.listing_html)

    def screenshot(self) -> bytes:
        return b""


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "kwork_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def project_html() -> str:
    return (FIXTURES / "kwork_project.html").read_text(encoding="utf-8")


def test_parse_listing_fixture(listing_html: str) -> None:
    cards = parse_listing_from_html(listing_html)
    assert len(cards) == 2
    assert cards[0]["project_id"] == "3201949"
    assert cards[0]["title"] == "Telegram-бот для парсинга"
    assert cards[0]["responses_count"] == 16


def test_parse_project_fixture(project_html: str) -> None:
    data = parse_project_from_html(project_html, project_id="3201949")
    assert data["project_id"] == "3201949"
    assert "Python/aiogram" in data["full_description"]
    assert data["desired_budget"] == "до 5 000 ₽"
    assert data["tags"] == ["Python", "Telegram"]


def test_kwork_adapter_scan_new(listing_html: str, project_html: str) -> None:
    browser = FakeBrowser(listing_html, project_html)
    adapter = KworkAdapter(
        source_key="kwork_dev_it",
        listing_url="https://kwork.ru/projects?c=41",
        browser=browser,
        auto_login=False,
    )
    previews = adapter.scan_new()
    assert len(previews) == 2
    assert isinstance(previews[0], ProjectPreview)
    assert previews[0].platform == "kwork"
    assert previews[0].source_key == "kwork_dev_it"


def test_kwork_adapter_read_full(listing_html: str, project_html: str) -> None:
    browser = FakeBrowser(listing_html, project_html)
    adapter = KworkAdapter(
        source_key="kwork_dev_it",
        listing_url="https://kwork.ru/projects?c=41",
        browser=browser,
        auto_login=False,
    )
    full = adapter.read_full("3201949")
    assert isinstance(full, ProjectFull)
    assert full.buyer == "buyer_username"
    assert full.offers_count == 16


def test_kwork_submit_dry_run(listing_html: str, project_html: str) -> None:
    browser = FakeBrowser(listing_html, project_html)
    browser.clicks: list[str] = []
    browser.fills: list[tuple[str, str]] = []

    def click(sel: str) -> None:
        browser.clicks.append(sel)

    def fill(sel: str, text: str) -> None:
        browser.fills.append((sel, text))

    browser.click = click  # type: ignore[method-assign]
    browser.fill = fill  # type: ignore[method-assign]

    adapter = KworkAdapter(
        source_key="kwork_dev_it",
        listing_url="https://kwork.ru/projects?c=41",
        browser=browser,
        dry_run_submit=True,
        auto_login=False,
    )
    result = adapter.submit_response("3201949", "Мой отклик", "5000")
    assert result.success is True
    assert "dry_run" in (result.message or "")
    assert len(browser.clicks) == 1
    assert len(browser.fills) == 2
    assert browser.fills[0][1] == "Мой отклик"


def test_kwork_submit_live_click(listing_html: str, project_html: str) -> None:
    browser = FakeBrowser(listing_html, project_html)
    clicks: list[str] = []

    def click(sel: str) -> None:
        clicks.append(sel)

    browser.click = click  # type: ignore[method-assign]

    adapter = KworkAdapter(
        source_key="kwork_dev_it",
        listing_url="https://kwork.ru/projects?c=41",
        browser=browser,
        dry_run_submit=False,
        auto_login=False,
    )
    result = adapter.submit_response("3201949", "text", None)
    assert result.success is True
    assert len(clicks) == 2
