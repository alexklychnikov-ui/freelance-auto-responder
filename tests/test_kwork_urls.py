from __future__ import annotations

import pytest

from src.adapters.kwork_urls import extract_kwork_project_id, kwork_project_view_url


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://kwork.ru/projects/3204427/view", "3204427"),
        ("https://kwork.ru/projects/3204427", "3204427"),
        ("https://kwork.ru/new_offer?project=3205065", "3205065"),
        ("Смотри https://kwork.ru/projects/123/view пожалуйста", "123"),
        ("/project https://kwork.ru/new_offer?project=999", "999"),
        ("https://kwork.ru/projects?c=11", None),
        ("hello", None),
        ("", None),
    ],
)
def test_extract_kwork_project_id(text: str, expected: str | None) -> None:
    assert extract_kwork_project_id(text) == expected


def test_kwork_project_view_url() -> None:
    assert kwork_project_view_url("42") == "https://kwork.ru/projects/42/view"
