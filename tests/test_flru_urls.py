from __future__ import annotations

import pytest

from src.adapters.flru_urls import extract_flru_project_id, flru_project_url


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.fl.ru/projects/5514795/parsing.html", "5514795"),
        ("fl.ru/projects/5514795", "5514795"),
        ("5514795", "5514795"),
    ],
)
def test_extract_flru_project_id(text: str, expected: str | None) -> None:
    assert extract_flru_project_id(text) == expected


def test_flru_project_url() -> None:
    assert flru_project_url("5514795") == "https://www.fl.ru/projects/5514795/"
