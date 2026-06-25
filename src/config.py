from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceConfig(BaseModel):
    id: str
    platform: str
    enabled: bool = False
    url: str | None = None
    channel: str | None = None
    scan_interval_minutes: int = 30
    bootstrap: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)
    last_message_id: int = 0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str
    openai_base_url: str = "https://api.proxyapi.ru/openai/v1"
    openai_model: str = "gpt-4o-mini"

    telegram_bot_token: str
    telegram_chat_id: str

    browser_adapter: str = "external"
    browsermcp_server: str | None = None
    browser_navigate_wait_seconds: float = 2.0

    response_journal: str
    database_path: str = "data/seen_projects.db"
    sources_config_path: str = "config/sources.yaml"
    lightrag_mcp: str = "user-lightrag"
    lightrag_base_url: str = ""
    lightrag_api_key: str = ""
    headroom_proxy_url: str = "http://127.0.0.1:8787"
    headroom_compress_context: bool = True
    headroom_context_min_chars: int = 2500
    github_username: str = "alexklychnikov-ui"
    github_token: str = ""
    github_stack_cache: str = "data/github_stack_cache.json"

    scan_interval_minutes: int = 30
    min_gpt_score: int = 7
    budget_ceiling_price_multiplier: float = 2.0
    max_daily_responses: int = 5
    require_telegram_approval: bool = True
    scan_bootstrap_skip_pipeline: bool = True
    scan_early_exit_known_count: int = 5

    response_examples_dir: str = ""
    dry_run_submit: bool = False
    prepare_only_no_submit: bool = True
    default_offer_days: int = 14
    prepared_responses_dir: str = "data/prepared_responses"
    pending_timeout_hours: int = 24

    kwork_login: str | None = None
    kwork_password: str | None = None
    kwork_auto_login: bool = True
    kwork_storage_state: str = ""

    @model_validator(mode="before")
    @classmethod
    def _env_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not data.get("openai_api_key") and data.get("PROXY_API_KEY"):
            data["openai_api_key"] = data["PROXY_API_KEY"]
        if not data.get("openai_base_url") and data.get("PROXY_BASE_URL"):
            data["openai_base_url"] = str(data["PROXY_BASE_URL"]).strip()
        if isinstance(data.get("openai_model"), str):
            data["openai_model"] = data["openai_model"].strip()
        return data

    @field_validator("openai_model")
    @classmethod
    def _strip_model(cls, v: str) -> str:
        return v.strip()

    def kwork_credentials(self) -> tuple[str, str] | None:
        login = (self.kwork_login or "").strip()
        password = self.kwork_password or ""
        if login and password:
            return login, password
        return None


def load_sources(config_path: str | Path | None = None) -> list[SourceConfig]:
    path = Path(config_path or "config/sources.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [SourceConfig.model_validate(item) for item in raw.get("sources", [])]


def get_enabled_sources(config_path: str | Path | None = None) -> list[SourceConfig]:
    return [s for s in load_sources(config_path) if s.enabled]


@lru_cache
def get_settings() -> Settings:
    return Settings()
