from __future__ import annotations

from src.adapters.kwork_pricing import pick_listed_offer_price
from src.adapters.offer_scope import (
    catalog_item_count_from_evidence,
    listed_price_mix_for_scope,
    suggest_delivery_days_for_scope,
)
from src.evidence.models import EvidenceBundle, EvidenceFact
from src.models import ProjectFull


def _parse_project(**kwargs: str) -> ProjectFull:
    base = dict(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3252551",
        url="https://kwork.ru/projects/3252551",
        title="Спарсить контент с сайта",
        full_description="Парсер категорий и товаров, выкачать изображения в папку.",
        desired_budget="до 2 000 ₽",
        max_budget="до 6 000 ₽",
    )
    base.update(kwargs)
    return ProjectFull(**base)  # type: ignore[arg-type]


def test_catalog_item_count_from_evidence_prefers_item_pages() -> None:
    bundle = EvidenceBundle(
        status="complete",
        required=True,
        project_hash="x",
        facts=[
            EvidenceFact(
                id="1",
                source_id="s",
                claim="В sitemap 9863 URL, из них 9730 страниц-элементов (*.html/*.php)",
                quote="9863",
                verification="structured_value",
                eligible_for_response=True,
            )
        ],
    )
    assert catalog_item_count_from_evidence(bundle) == 9730


def test_large_parse_mix_raises_listed_toward_fair() -> None:
    project = _parse_project()
    volume = 9730
    mix = listed_price_mix_for_scope(project, volume=volume)
    assert mix == 0.65
    old = pick_listed_offer_price(project, fair=3600, mix=0.15)
    new = pick_listed_offer_price(project, fair=3600, mix=mix)
    assert old == 2240
    assert new == 3040
    assert new > old
    high_fair = pick_listed_offer_price(project, fair=8000, mix=mix)
    assert high_fair == 4600
    assert high_fair <= 6000


def test_normal_bot_keeps_low_mix() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Telegram-бот",
        full_description="Нужен Telegram-бот для заявок менеджеру.",
        desired_budget="до 15 000 ₽",
        max_budget="до 45 000 ₽",
    )
    assert listed_price_mix_for_scope(project, volume=None) == 0.15
    assert suggest_delivery_days_for_scope(project, volume=None) is None


def test_large_catalog_delivery_days_not_inflated() -> None:
    project = _parse_project()
    days = suggest_delivery_days_for_scope(project, volume=9730)
    assert days == 5
    small = suggest_delivery_days_for_scope(project, volume=100)
    assert small == 3
