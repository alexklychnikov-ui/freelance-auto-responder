from __future__ import annotations

import pytest

from src.adapters.flru_urls import (
    ensure_flru_for_all,
    extract_flru_project_id,
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
    assert ensure_flru_for_all("") == "https://www.fl.ru/projects/?kind=1&for_all=1"
