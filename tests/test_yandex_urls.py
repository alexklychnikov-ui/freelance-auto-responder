from __future__ import annotations

import pytest

from src.adapters.yandex_urls import extract_yandex_order_id, yandex_order_url

UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"https://uslugi.yandex.ru/order/{UUID}", UUID),
        (f"https://uslugi.yandex.ru/order/{UUID}?foo=1", UUID),
        (f"Смотри https://uslugi.yandex.ru/order/{UUID} пожалуйста", UUID),
        (UUID, UUID),
        ("https://kwork.ru/projects/123", None),
        ("hello", None),
        ("", None),
    ],
)
def test_extract_yandex_order_id(text: str, expected: str | None) -> None:
    assert extract_yandex_order_id(text) == expected


def test_yandex_order_url() -> None:
    assert yandex_order_url(UUID) == f"https://uslugi.yandex.ru/order/{UUID}"
