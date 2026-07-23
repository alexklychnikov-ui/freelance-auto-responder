from __future__ import annotations

import pytest

from src.pipeline.tz_project import TZ_MIN_CHARS, build_tz_project


def test_build_tz_project_uses_first_line_as_title() -> None:
    text = "Нужен Telegram-бот\nОписание задачи подробно " + ("x" * 40)
    project = build_tz_project(text)
    assert project.platform == "telegram"
    assert project.source_key == "tz_manual"
    assert project.title == "Нужен Telegram-бот"
    assert project.full_description == text
    assert project.project_id.startswith("tz_")
    assert project.url == ""


def test_build_tz_project_short_title_uses_body_prefix() -> None:
    text = "бот " + ("подробное описание " * 5)
    project = build_tz_project(text)
    assert len(project.title) <= 120


@pytest.mark.parametrize("text", ["", "коротко", "x" * (TZ_MIN_CHARS - 1)])
def test_tz_min_chars_constant(text: str) -> None:
    assert len(text) < TZ_MIN_CHARS
