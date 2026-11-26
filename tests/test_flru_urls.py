from __future__ import annotations

import pytest

from src.adapters.flru_urls import (
    ensure_flru_for_all,
    extract_flru_project_id,
    flru_listing_page_url,
    flru_project_url,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.fl.ru/projects/5514795/parsing.html", "5514795"),
        ("fl.ru/projects/5514795", "5514795"),
        ("5514795", None),
    ],
)
def test_extract_flru_project_id(text: str, expected: str | None) -> None:
    assert extract_flru_project_id(text) == expected


def test_flru_project_url() -> None:
    assert flru_project_url("5514795") == "https://www.fl.ru/projects/5514795/"


def test_ensure_flru_for_all() -> None:
    assert (
        ensure_flru_for_all("https://www.fl.ru/projects/?kind=1")
        == "https://www.fl.ru/projects/?kind=1&for_all=1"
    )
    assert (
        ensure_flru_for_all("https://www.fl.ru/projects/?kind=1&for_all=0")
        == "https://www.fl.ru/projects/?kind=1&for_all=1"
    )
    assert ensure_flru_for_all("") == "https://www.fl.ru/projects/?kind=5&for_all=1"


@pytest.mark.parametrize(
    "page,expected",
    [
        (1, "https://www.fl.ru/projects/?kind=5&for_all=1"),
        (0, "https://www.fl.ru/projects/?kind=5&for_all=1"),
        (2, "https://www.fl.ru/projects/?kind=5&for_all=1&page=2"),
        (3, "https://www.fl.ru/projects/?kind=5&for_all=1&page=3"),
    ],
)
def test_flru_listing_page_url(page: int, expected: str) -> None:
    base = "https://www.fl.ru/projects/?kind=5&for_all=1"
    assert flru_listing_page_url(base, page) == expected


def test_flru_listing_page_url_replaces_existing_page() -> None:
    from urllib.parse import parse_qs, urlparse

    url = "https://www.fl.ru/projects/?kind=1&page=9&for_all=1"
    page2 = flru_listing_page_url(url, 2)
    q2 = parse_qs(urlparse(page2).query)
    assert q2.get("page") == ["2"]
    assert q2.get("for_all") == ["1"]
    page1 = flru_listing_page_url(url, 1)
    q1 = parse_qs(urlparse(page1).query)
    assert "page" not in q1
    assert q1.get("for_all") == ["1"]
