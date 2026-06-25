from __future__ import annotations

from src.adapters.kwork_pricing import clamp_price_to_budget, suggest_offer_price
from src.models import ProjectFull


def test_suggest_offer_price_with_range() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Site",
        full_description="10 pages",
        desired_budget="8 000 ₽",
        max_budget="до 24 000 ₽",
    )
    price = int(suggest_offer_price(project))
    assert 8000 <= price <= 24000
    assert price != 5000


def test_suggest_offer_price_ignores_title_digits() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3204427",
        url="https://kwork.ru/projects/3204427",
        title="Доработать 2 Telegram-бота по готовому ТЗ",
        full_description="Проект №1 и Проект №2 без бюджета в тексте",
        desired_budget="до 35 000 ₽",
        max_budget="до 105 000 ₽",
    )
    price = int(suggest_offer_price(project))
    assert price >= 7000
    assert price <= 105000


def test_parse_form_price_bounds() -> None:
    from src.adapters.kwork_pricing import parse_form_price_bounds

    lo, hi = parse_form_price_bounds("Стоимость может быть от 7 000 руб. до 105 000 руб.")
    assert lo == 7000
    assert hi == 105000


def test_clamp_price_to_budget() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Site",
        full_description="",
        desired_budget="8 000 ₽",
        max_budget="до 24 000 ₽",
    )
    assert clamp_price_to_budget(30000, project) == 24000
    assert clamp_price_to_budget(5000, project) == 8000
