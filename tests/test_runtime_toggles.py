from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.runtime_toggles import (
    get_toggles,
    is_kwork_paused,
    set_kwork_paused,
    toggles_file_path,
)


def test_missing_file_defaults_not_paused(tmp_path: Path) -> None:
    path = tmp_path / "platform_toggles.json"
    assert not path.exists()
    assert is_kwork_paused(path) is False
    assert get_toggles(path) == {"kwork_paused": False}


def test_set_and_get_kwork_paused(tmp_path: Path) -> None:
    path = tmp_path / "platform_toggles.json"
    assert set_kwork_paused(path, paused=True) is True
    assert is_kwork_paused(path) is True
    assert path.exists()
    assert set_kwork_paused(path, paused=False) is False
    assert is_kwork_paused(path) is False


def test_settings_derives_path_from_database_dir(tmp_path: Path) -> None:
    db = tmp_path / "seen.db"
    settings = Settings(
        openai_api_key="k",
        telegram_bot_token="t",
        telegram_chat_id="1",
        response_journal=str(tmp_path / "j.xlsx"),
        database_path=str(db),
    )
    expected = tmp_path / "platform_toggles.json"
    assert toggles_file_path(settings) == expected
    assert is_kwork_paused(settings) is False
    assert set_kwork_paused(settings, paused=True) is True
    assert is_kwork_paused(settings) is True
    assert expected.exists()
