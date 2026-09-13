from __future__ import annotations

from pathlib import Path

from src.evidence.specificity import (
    GENERIC_ACTION_OPENERS,
    detect_generic_action_opener,
    has_generic_action_opener,
    has_required_anchors,
    missing_required_anchors,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "evidence"
BAD_RESPONSE = (FIXTURES / "3252339_bad_template_response.txt").read_text(encoding="utf-8")


def test_generic_openers_constant() -> None:
    assert GENERIC_ACTION_OPENERS == (
        "Реализую",
        "Создам",
        "Сделаю",
        "Разработаю",
        "Соберу",
    )


def test_detects_razrabotayu_after_hello() -> None:
    assert detect_generic_action_opener(BAD_RESPONSE) == "Разработаю"
    assert has_generic_action_opener(BAD_RESPONSE) is True


def test_detects_each_opener_after_hello() -> None:
    for opener in GENERIC_ACTION_OPENERS:
        text = f"Здравствуйте!\n{opener} парсер под ваше ТЗ."
        assert detect_generic_action_opener(text) == opener


def test_same_line_hello_then_opener() -> None:
    text = "Здравствуйте! Создам лендинг по вашему брифу."
    assert detect_generic_action_opener(text) == "Создам"


def test_detects_ya_prefix_opener() -> None:
    text = "Здравствуйте!\nЯ разработаю парсер под ваше ТЗ."
    assert detect_generic_action_opener(text) == "Разработаю"
    assert has_generic_action_opener(text) is True


def test_specific_opener_without_generic_verb() -> None:
    text = (
        "Здравствуйте!\n"
        "Посмотрел лот 184729 на торги-россии: поиск + выдача документов без ЛК.\n"
        "Сделаю MVP поиска по извещению и вложениям."
    )
    # first content line is not a generic opener verb
    assert detect_generic_action_opener(text) is None
    assert has_generic_action_opener(text) is False


def test_anchors_required_when_evidence_available() -> None:
    anchors = ["184729", "Извещение.pdf", "кадастровый"]
    missing = missing_required_anchors(
        BAD_RESPONSE,
        anchors,
        evidence_available=True,
    )
    assert "184729" in missing
    assert "Извещение.pdf" in missing
    assert has_required_anchors(BAD_RESPONSE, anchors, evidence_available=True) is False


def test_anchors_skipped_when_no_evidence() -> None:
    anchors = ["184729", "Извещение.pdf"]
    assert missing_required_anchors(
        BAD_RESPONSE,
        anchors,
        evidence_available=False,
    ) == []
    assert has_required_anchors(BAD_RESPONSE, anchors, evidence_available=False) is True


def test_anchors_pass_when_present() -> None:
    text = (
        "Здравствуйте!\n"
        "По лоту 184729 вижу извещение и Кадастровый паспорт — "
        "поиск + выдача документов без ЛК."
    )
    anchors = ["184729", "кадастровый"]
    assert missing_required_anchors(text, anchors, evidence_available=True) == []
    assert has_required_anchors(text, anchors, evidence_available=True) is True
