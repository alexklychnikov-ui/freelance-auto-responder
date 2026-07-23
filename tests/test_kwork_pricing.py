from __future__ import annotations

from src.adapters.kwork_pricing import (
    apply_competitive_price,
    budget_gap,
    budget_mismatch_issues,
    clamp_price_to_budget,
    ensure_budget_mismatch_note,
    format_budget_mismatch_sentence,
    parse_budget_ceiling_rub,
    pick_commercial_price,
    suggest_offer_price,
)
from src.models import ProjectFull


def test_apply_competitive_price() -> None:
    assert apply_competitive_price(28000, 0.8) == 22400
    assert apply_competitive_price(25000, 0.8) == 20000
    assert apply_competitive_price(400, 0.8) == 500


def test_pick_commercial_price() -> None:
    assert pick_commercial_price(40_000, 60_000) == 40_000
    assert pick_commercial_price(60_000, 40_000) == 40_000
    assert pick_commercial_price(40_000, 0) == 40_000
    assert pick_commercial_price(0, 60_000) == 60_000
    assert pick_commercial_price(0, 0) == 0


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


def test_clamp_respects_max_budget_when_desired_higher() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Bot",
        full_description="",
        desired_budget="от 20 000 ₽",
        max_budget="до 1 500 ₽",
    )
    assert clamp_price_to_budget(20_000, project) == 1500
    assert clamp_price_to_budget(20_000, project, form_max=1500) == 1500


def test_budget_gap_true_when_fair_above_ceiling() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Bot",
        full_description="",
        max_budget="до 1 500 ₽",
    )
    gap = budget_gap(20_000, project)
    assert gap is not None
    assert gap["ceiling"] == 1500
    assert gap["fair_price"] == 20_000
    assert gap["fill_price"] == 1500
    assert gap["ratio"] > 1


def test_budget_gap_false_when_fair_within_ceiling() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Bot",
        full_description="",
        max_budget="до 1 500 ₽",
    )
    assert budget_gap(1200, project) is None
    assert budget_gap(1500, project) is None


def test_parse_ceiling_from_desired_do_only() -> None:
    """3218308-like: only desired_budget=«до 1 500 ₽», max_budget missing."""
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3218308",
        url="https://kwork.ru/projects/3218308",
        title="Checko Python",
        full_description="",
        desired_budget="до 1 500 ₽",
        max_budget=None,
    )
    assert parse_budget_ceiling_rub(project) == 1500
    gap = budget_gap(25_000, project)
    assert gap is not None
    assert gap["ceiling"] == 1500
    assert gap["fill_price"] == 1500


def test_parse_ceiling_prefers_dopustimy_over_desired() -> None:
    """3218832: желаемый 15k + допустимый 45k → ceiling 45000."""
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3218832",
        url="https://kwork.ru/projects/3218832",
        title="Bot",
        full_description="",
        desired_budget="до 15 000 ₽",
        max_budget="до 45 000 ₽",
    )
    assert parse_budget_ceiling_rub(project) == 45_000
    gap = budget_gap(60_000, project, form_max=15_000)
    assert gap is not None
    assert gap["ceiling"] == 45_000
    assert gap["fill_price"] == 45_000
    # Form still clamps to form_max; gap messaging keeps project ceiling
    assert clamp_price_to_budget(60_000, project, form_max=15_000) == 15_000
    # Fair within допустимый → no soft gap even if form_max tighter
    assert budget_gap(20_000, project, form_max=15_000) is None


def test_budget_gap_uses_form_max_when_project_ceiling_missing() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3218308",
        url="https://kwork.ru/projects/3218308",
        title="Checko Python",
        full_description="",
        desired_budget=None,
        max_budget=None,
    )
    assert budget_gap(25_000, project) is None
    gap = budget_gap(25_000, project, form_max=1500)
    assert gap is not None
    assert gap["ceiling"] == 1500
    assert gap["fair_price"] == 25_000
    note = ensure_budget_mismatch_note("Срок — 7 дней. Стоимость — от 25 000 ₽.", gap)
    assert "обсудить сумму" in note.lower()
    assert "1 500" in note


def test_format_budget_mismatch_sentence_has_discuss_cta() -> None:
    gap = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    sentence = format_budget_mismatch_sentence(gap)
    assert "20 000" in sentence
    assert "1 500" in sentence
    assert "обсудить сумму" in sentence.lower()
    assert "занижен" in sentence.lower()


def test_ensure_budget_mismatch_note_appends_once() -> None:
    gap = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    base = "Соберу бота. Срок — 5 дней. Стоимость — от 20 000 ₽."
    out = ensure_budget_mismatch_note(base, gap)
    assert "обсудить сумму" in out.lower()
    assert out.count("выглядит заниженным") == 1
    out2 = ensure_budget_mismatch_note(out, gap)
    assert out2 == out.rstrip()


def test_budget_mismatch_issues_ceiling_echo() -> None:
    gap = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    bad = "Срок — 5 дней. Стоимость — от 1 500 ₽. Предлагаю обсудить детали."
    assert budget_mismatch_issues(bad, gap)
    good = (
        "Стоимость — от 20 000 ₽. Указанный бюджет выглядит заниженным. "
        "Предлагаю обсудить сумму под ваш результат."
    )
    assert not budget_mismatch_issues(good, gap)
