# -*- coding: utf-8 -*-
from pathlib import Path

from src.config import Settings
from src.platform_health import mark_platform_up, note_platform_failure


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        database_path=str(tmp_path / "seen.db"),
        _env_file=None,
    )


def test_first_fail_no_send(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr("src.platform_health.time.time", lambda: 1_000.0)
    assert (
        note_platform_failure(
            settings, "yandex_uslugi", confirm_sec=120, cooldown_sec=3600
        )
        is False
    )


def test_second_fail_after_confirm_sends(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "src.platform_health.time.time", lambda: clock["now"]
    )
    assert (
        note_platform_failure(
            settings, "flru", confirm_sec=120, cooldown_sec=3600
        )
        is False
    )
    clock["now"] = 1_000.0 + 119
    assert (
        note_platform_failure(
            settings, "flru", confirm_sec=120, cooldown_sec=3600
        )
        is False
    )
    clock["now"] = 1_000.0 + 120
    assert (
        note_platform_failure(
            settings, "flru", confirm_sec=120, cooldown_sec=3600
        )
        is True
    )
    assert (
        note_platform_failure(
            settings, "flru", confirm_sec=120, cooldown_sec=3600
        )
        is False
    )


def test_recover_from_suspect_without_alert(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr("src.platform_health.time.time", lambda: 1_000.0)
    assert note_platform_failure(settings, "kwork", confirm_sec=120) is False
    assert mark_platform_up(settings, "kwork") is False
    assert mark_platform_up(settings, "kwork") is False


def test_recover_after_alert_true_once(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "src.platform_health.time.time", lambda: clock["now"]
    )
    assert note_platform_failure(settings, "flru", confirm_sec=60) is False
    clock["now"] = 1_100.0
    assert note_platform_failure(settings, "flru", confirm_sec=60) is True
    assert mark_platform_up(settings, "flru") is True
    assert mark_platform_up(settings, "flru") is False
