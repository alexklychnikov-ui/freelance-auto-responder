from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings, get_enabled_sources, get_settings, load_sources


@pytest.fixture
def env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.proxyapi.ru/openai/v1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.setenv("RESPONSE_JOURNAL", "data/test_journal.xlsx")
    get_settings.cache_clear()


def test_load_sources_yaml() -> None:
    config_path = Path("config/sources.yaml")
    sources = load_sources(config_path)
    kwork_sources = [s for s in sources if s.platform == "kwork"]
    assert len(kwork_sources) == 3
    by_id = {s.id: s for s in kwork_sources}
    assert by_id["kwork_dev_it"].url == "https://kwork.ru/projects?c=11"
    assert by_id["kwork_c5"].url == "https://kwork.ru/projects?c=5"
    assert by_id["kwork_c15"].url == "https://kwork.ru/projects?c=15"


def test_enabled_sources_only_kwork() -> None:
    enabled = get_enabled_sources("config/sources.yaml")
    assert len(enabled) == 3
    assert {s.id for s in enabled} == {"kwork_dev_it", "kwork_c5", "kwork_c15"}


def test_settings_from_env(env_vars: None) -> None:
    settings = get_settings()
    assert settings.openai_api_key == "test-key"
    assert settings.telegram_bot_token == "test-token"
    assert settings.min_gpt_score == 7
    assert settings.scan_bootstrap_skip_pipeline is True
    assert settings.browser_adapter == "cursor"


def test_settings_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("RESPONSE_JOURNAL", raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)
