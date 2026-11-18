# -*- coding: utf-8 -*-
from pathlib import Path

from src.config import Settings
from src.platform_health import mark_platform_up, should_send_platform_down_alert


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal="j.xlsx",
        database_path=str(tmp_path / "seen.db"),
        _env_file=None,
    )


def test_platform_down_alert_throttled(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert should_send_platform_down_alert(
        settings, "yandex_uslugi", cooldown_sec=3600
    )
    assert not should_send_platform_down_alert(
        settings, "yandex_uslugi", cooldown_sec=3600
    )


def test_platform_up_clears_down(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert should_send_platform_down_alert(
        settings, "flru", cooldown_sec=3600
    )
    assert mark_platform_up(settings, "flru") is True
    assert mark_platform_up(settings, "flru") is False
