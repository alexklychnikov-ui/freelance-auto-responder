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
    assert len(sources) == 3
    kwork = next(s for s in sources if s.id == "kwork_dev_it")
    assert kwork.enabled is True
    assert kwork.platform == "kwork"
    assert "kwork.ru/projects" in (kwork.url or "")


def test_enabled_sources_only_kwork() -> None:
    enabled = get_enabled_sources("config/sources.yaml")
    assert len(enabled) == 1
    assert enabled[0].id == "kwork_dev_it"


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
