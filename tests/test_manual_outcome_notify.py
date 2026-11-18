# -*- coding: utf-8 -*-
from src.pipeline.orchestrator import _manual_outcome_tg_text


def test_manual_outcome_notified_silent():
    assert (
        _manual_outcome_tg_text(
            platform_label="Яндекс Услуги",
            project_id="abc",
            url="https://uslugi.yandex.ru/order/abc",
            outcome="notified",
        )
        is None
    )


def test_manual_outcome_extract_fail_message():
    text = _manual_outcome_tg_text(
        platform_label="Яндекс Услуги",
        project_id="283cc216-cf0c-4bf1-bd26-ab4939eb7558",
        url="https://uslugi.yandex.ru/order/283cc216-cf0c-4bf1-bd26-ab4939eb7558",
        outcome="extract_fail",
    )
    assert text is not None
    assert "не удалось прочитать карточку" in text
    assert "283cc216" in text
    assert "/tz" in text
