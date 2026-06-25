from __future__ import annotations

from src.adapters.kwork import _stages_dom_ok


def test_stages_dom_ok_accepts_matching_rows() -> None:
    stages = [("Этап 1", 55000), ("Этап 2", 45000)]
    read = {
        "prices": ["55000", "45000"],
        "rows": [{"title": "Этап 1"}, {"title": "Этап 2"}],
        "vueStages": [],
    }
    assert _stages_dom_ok(stages, read) is True


def test_stages_dom_ok_rejects_missing_prices() -> None:
    stages = [("Этап 1", 55000), ("Этап 2", 45000)]
    read = {
        "prices": ["55000"],
        "rows": [{"title": "Этап 1"}, {"title": "Этап 2"}],
        "vueStages": [],
    }
    assert _stages_dom_ok(stages, read) is False


def test_stages_dom_ok_rejects_undefined_titles() -> None:
    stages = [("Этап 1", 5000), ("Этап 2", 5000)]
    read = {
        "prices": ["5000", "5000"],
        "rows": [{"title": "Этап 1"}, {"title": "undefined"}],
        "vueStages": [],
    }
    assert _stages_dom_ok(stages, read) is False


def test_stages_dom_ok_rejects_wrong_title() -> None:
    stages = [("Этап 1", 55000), ("Этап 2", 45000)]
    read = {
        "prices": ["55000", "45000"],
        "rows": [{"title": "Старый этап"}, {"title": "Этап 2"}],
        "vueStages": [],
    }
    assert _stages_dom_ok(stages, read) is False


def test_stage_title_matches_partial() -> None:
    from src.adapters.kwork import _stage_title_matches

    assert _stage_title_matches(
        "Анализ ТЗ и реализация основной части",
        "Анализ ТЗ и реализация основной части",
    )
    assert not _stage_title_matches("Этап 1", "undefined")


def test_stages_dom_ok_accepts_vue_titles() -> None:
    stages = [("Анализ", 7000), ("Сдача", 3000)]
    read = {
        "prices": ["7000", "3000"],
        "rows": [{"title": ""}, {"title": ""}],
        "vueStages": [
            {"title": "Анализ", "price": 7000},
            {"title": "Сдача", "price": 3000},
        ],
    }
    assert _stages_dom_ok(stages, read) is True
