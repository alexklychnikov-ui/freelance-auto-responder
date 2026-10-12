from __future__ import annotations

import pytest

from src.telegram_bot.project_input import parse_project_command_arg


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3266288", ("3266288", "kwork")),
        ("https://kwork.ru/projects/3266288/view", ("3266288", "kwork")),
        (
            "72626afd-6e33-44f8-86ba-c24a1bc4bcb5",
            ("72626afd-6e33-44f8-86ba-c24a1bc4bcb5", "yandex_uslugi"),
        ),
        (
            "https://uslugi.yandex.ru/order/72626afd-6e33-44f8-86ba-c24a1bc4bcb5",
            ("72626afd-6e33-44f8-86ba-c24a1bc4bcb5", "yandex_uslugi"),
        ),
        (
            "https://www.fl.ru/projects/5514790/slug/",
            ("5514790", "flru"),
        ),
        ("hello", None),
    ],
)
def test_parse_project_command_arg(
    text: str, expected: tuple[str, str] | None
) -> None:
    assert parse_project_command_arg(text) == expected
