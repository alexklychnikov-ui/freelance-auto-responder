from __future__ import annotations

from src.adapters.kwork_pricing import (
    align_response_commercials,
    apply_competitive_price,
    budget_gap,
    budget_mismatch_issues,
    clamp_price_to_budget,
    customer_expansion_hint_rub,
    ensure_budget_mismatch_note,
    format_budget_mismatch_sentence,
    parse_budget_ceiling_rub,
    parse_desired_budget_rub,
    pick_commercial_price,
    pick_listed_offer_price,
    response_defers_price_or_days,
    response_has_budget_discuss_note,
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
    assert price == 8000
    assert abs(price - 8000) < abs(price - 24000)
    assert price <= 12_000


def test_pick_listed_closer_to_desired_than_fair_or_max() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3229782",
        url="https://kwork.ru/projects/3229782",
        title="Bot",
        full_description="",
        desired_budget="до 15 000 ₽",
        max_budget="до 45 000 ₽",
    )
    listed = pick_listed_offer_price(project, fair=19_000)
    assert 15_000 <= listed <= 18_000
    assert abs(listed - 15_000) < abs(listed - 19_000)
    assert abs(listed - 15_000) < abs(listed - 45_000)
    assert listed != 19_000
    assert listed != 45_000
    listed_no_fair = pick_listed_offer_price(project)
    assert listed_no_fair == 15_000
    gap = budget_gap(20_000, project)
    assert gap is not None
    assert gap["ceiling"] == 15_000
    assert 15_000 <= int(gap["fill_price"]) <= 18_000
    assert int(gap["fill_price"]) != 45_000


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
    assert parse_desired_budget_rub(project) == 15_000
    gap = budget_gap(60_000, project, form_max=15_000)
    assert gap is not None
    assert gap["ceiling"] == 15_000
    assert gap["fill_price"] == 15_000
    assert gap["max_ceiling"] == 45_000
    # Form still clamps to form_max; gap messaging stays in listed/desired
    assert clamp_price_to_budget(60_000, project, form_max=15_000) == 15_000
    # 3229782: fair inside допустимый but above desired → gap, fill near 15k not 45k
    gap_mid = budget_gap(20_000, project, form_max=15_000)
    assert gap_mid is not None
    assert gap_mid["ceiling"] == 15_000
    assert gap_mid["fill_price"] == 15_000
    assert gap_mid["fair_price"] == 20_000


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
    note = ensure_budget_mismatch_note(
        "Срок — 7 дней. Стоимость — от 25 000 ₽.", gap, seed="form-max"
    )
    assert response_has_budget_discuss_note(note)
    assert "1 500" in note
    assert "занижен" not in note.lower()
    assert "обсудить сумму" not in note.lower()


def test_customer_expansion_hint_hides_wild_fair() -> None:
    wild = {
        "ceiling": 13_000,
        "fair_price": 144_000,
        "fill_price": 13_000,
        "ratio": 11.0769,
    }
    assert customer_expansion_hint_rub(wild) is None
    sentence = format_budget_mismatch_sentence(wild, seed="wild")
    assert "13 000" in sentence
    assert "144" not in sentence
    assert "144 000" not in sentence
    assert response_has_budget_discuss_note(sentence)
    assert "обсудим отдельно" in sentence.lower() or "расширение" in sentence.lower()


def test_customer_expansion_hint_caps_at_max_ceiling() -> None:
    gap = {
        "ceiling": 13_000,
        "fair_price": 144_000,
        "fill_price": 13_000,
        "max_ceiling": 30_000,
        "ratio": 11.0769,
    }
    assert customer_expansion_hint_rub(gap) == 30_000
    sentence = format_budget_mismatch_sentence(gap, seed="cap")
    assert "30 000" in sentence
    assert "144" not in sentence
    assert "ориентир от 30 000" in sentence


def test_format_budget_mismatch_sentence_has_scope_note() -> None:
    gap = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    sentence = format_budget_mismatch_sentence(gap, seed="soft-scope")
    assert "20 000" not in sentence
    assert "1 500" in sentence
    assert response_has_budget_discuss_note(sentence)
    assert "занижен" not in sentence.lower()
    assert "обсудить сумму" not in sentence.lower()


def test_mismatch_variants_stable_and_diverse() -> None:
    gap_soft = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    gap_hint = {
        "ceiling": 13_000,
        "fair_price": 40_000,
        "fill_price": 13_000,
        "max_ceiling": 30_000,
        "ratio": 3.077,
    }
    a = format_budget_mismatch_sentence(gap_soft, seed="proj-a")
    b = format_budget_mismatch_sentence(gap_soft, seed="proj-a")
    c = format_budget_mismatch_sentence(gap_soft, seed="proj-b")
    assert a == b
    soft_set = {
        format_budget_mismatch_sentence(gap_soft, seed=f"s{i}") for i in range(40)
    }
    assert len(soft_set) >= 3
    assert all(response_has_budget_discuss_note(s) for s in soft_set)
    hint_set = {
        format_budget_mismatch_sentence(gap_hint, seed=f"h{i}") for i in range(40)
    }
    assert len(hint_set) >= 3
    assert all("ориентир от" in s.lower() for s in hint_set)
    assert a != c or len(soft_set) > 1


def test_normalize_keeps_body_when_scope_note_leads() -> None:
    """Regression: note at top must not wipe the real response body."""
    gap = {
        "ceiling": 3000,
        "fair_price": 18_000,
        "fill_price": 3000,
        "ratio": 6.0,
    }
    base = (
        "В бюджете заказа (3 000 ₽) сделаю MVP. "
        "Расширение сверх MVP обсудим отдельно — после уточнения объёма.\n"
        "Здравствуйте! Сделаю Telegram-бота для анонсов мероприятий: меню, "
        "роль админа и карточки событий со ссылками. Срок — 5 дней. "
        "Стоимость — от 3 000 ₽. Если пришлёте список разделов меню — "
        "сразу зафиксирую структуру."
    )
    assert response_has_budget_discuss_note(base) is True
    out = ensure_budget_mismatch_note(base, gap, seed="lead-note")
    assert "Telegram-бота для анонсов" in out
    assert "Здравствуйте!" in out
    assert len(out) > len(base) // 2
    assert response_has_budget_discuss_note(out)


def test_ensure_budget_mismatch_note_appends_once() -> None:
    gap = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    base = "Соберу бота. Срок — 5 дней. Стоимость — от 1 500 ₽."
    out = ensure_budget_mismatch_note(base, gap, seed="once")
    assert response_has_budget_discuss_note(out)
    assert "1 500" in out
    assert "20 000" not in out
    assert "занижен" not in out.lower()
    out2 = ensure_budget_mismatch_note(out, gap, seed="once")
    assert out2 == out.rstrip()


def test_ensure_rewrites_scary_fair_orientir() -> None:
    gap = {
        "ceiling": 13_000,
        "fair_price": 144_000,
        "fill_price": 13_000,
        "max_ceiling": 30_000,
        "ratio": 11.0769,
    }
    base = (
        "Соберу Telegram-бота. Срок — 5 дней. Стоимость — от 13 000 ₽.\n\n"
        "В бюджете заказа (13 000 ₽) сделаю основной сценарий по ТЗ. "
        "Полный объём — ориентир от 144 000 ₽, если захотите расширить."
    )
    assert response_has_budget_discuss_note(base) is True
    out = ensure_budget_mismatch_note(base, gap, seed="rewrite")
    assert "144" not in out
    assert "30 000" in out
    assert response_has_budget_discuss_note(out)
    assert "ориентир от 30 000" in out


def test_budget_mismatch_issues_listed_not_fair_only() -> None:
    gap = {
        "ceiling": 1500,
        "fair_price": 20_000,
        "fill_price": 1500,
        "ratio": 13.3333,
    }
    bad = "Срок — 5 дней. Стоимость — от 1 500 ₽. Предлагаю обсудить детали."
    assert budget_mismatch_issues(bad, gap)
    zanizhen = (
        "Стоимость — от 20 000 ₽. Указанный бюджет выглядит заниженным. "
        "Предлагаю обсудить сумму под ваш результат."
    )
    issues = budget_mismatch_issues(zanizhen, gap)
    assert "budget_mismatch:zanizhen_strategy" in issues
    good = (
        "Стоимость — от 1 500 ₽. В бюджете заказа (1 500 ₽) сделаю "
        "основной сценарий по ТЗ. Расширение сверх MVP обсудим отдельно — "
        "после уточнения объёма."
    )
    assert not budget_mismatch_issues(good, gap)
    assert response_defers_price_or_days(good) is False


def test_scope_note_needs_two_signals() -> None:
    gap = {
        "ceiling": 2300,
        "fair_price": 45_000,
        "fill_price": 2300,
        "ratio": 19.5652,
    }
    single = "Стоимость — от 2 300 ₽, что включает основные сценарии парсинга."
    assert response_has_budget_discuss_note(single) is False
    assert "budget_mismatch:no_scope_note" in budget_mismatch_issues(single, gap)
    full = format_budget_mismatch_sentence(gap, seed="signals")
    assert response_has_budget_discuss_note(full) is True
    repaired = ensure_budget_mismatch_note(single, gap, seed="signals")
    assert response_has_budget_discuss_note(repaired) is True
    assert "budget_mismatch:no_scope_note" not in budget_mismatch_issues(repaired, gap)


def test_defer_skips_budget_mismatch_note() -> None:
    gap = {
        "ceiling": 1300,
        "fair_price": 14_400,
        "fill_price": 1300,
        "ratio": 11.0769,
    }
    base = (
        "Соберу интеграцию по ТЗ. По стоимости и срокам предлагаю обсудить "
        "после уточнения объёма."
    )
    assert response_defers_price_or_days(base) is True
    assert ensure_budget_mismatch_note(base, gap, seed="defer") == base.rstrip()
    assert "budget_mismatch:no_scope_note" not in budget_mismatch_issues(base, gap)


def test_align_replaces_asking_price_keeps_fair() -> None:
    # 3260072-like: form 20200, GPT wrote 15000 / fair 17960
    body = (
        "Сделаю MVP по ТЗ. Срок — 2 дня. Стоимость — от 15 000 ₽.\n\n"
        "В бюджете заказа (15 000 ₽) сделаю основной сценарий по ТЗ. "
        "Полный объём — ориентир от 17 960 ₽, если захотите расширить."
    )
    out = align_response_commercials(body, price=20_200, delivery_days=2)
    assert "Стоимость — от 20 200 ₽" in out
    assert "15 000" not in out
    assert "17 960" in out
    assert "В бюджете заказа (20 200 ₽)" in out
    assert "Срок — 2 дн." in out


def test_align_fixes_srok_and_appends_price() -> None:
    body = "Соберу API-интеграцию. Срок — 2 дня."
    out = align_response_commercials(body, price=20_200, delivery_days=7)
    assert "Срок — 7 дн." in out
    assert "Стоимость — 20 200 ₽." in out


def test_align_rewrites_mismatch_fill_only() -> None:
    body = (
        "Стоимость — от 1 300 ₽. Срок — 2 дн.\n\n"
        "В бюджете заказа (1 300 ₽) сделаю основной сценарий по ТЗ. "
        "Полный объём — ориентир от 14 400 ₽, если захотите расширить."
    )
    out = align_response_commercials(body, price=1_300, delivery_days=2)
    assert "В бюджете заказа (1 300 ₽)" in out
    assert "14 400" in out
    out2 = align_response_commercials(body, price=2_000, delivery_days=2)
    assert "Стоимость — от 2 000 ₽" in out2
    assert "В бюджете заказа (2 000 ₽)" in out2
    assert "14 400" in out2
