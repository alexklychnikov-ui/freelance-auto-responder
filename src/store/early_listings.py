"""Persist early-listing cards until operator clicks Откликнуться / Пропустить."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class EarlyListing(BaseModel):
    platform: str
    source_key: str
    project_id: str
    url: str
    title: str
    offers_count: int | None = None
    telegram_message_id: int | None = None
    status: str = "awaiting"
    budget_warning: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EarlyListingStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, platform: str, source_key: str, project_id: str) -> Path:
        safe = f"{platform}_{source_key}_{project_id}".replace("/", "_")
        return self.directory / f"{safe}.json"

    def save(self, listing: EarlyListing) -> None:
        path = self._path(listing.platform, listing.source_key, listing.project_id)
        path.write_text(
            listing.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def load(
        self, platform: str, source_key: str, project_id: str
    ) -> EarlyListing | None:
        path = self._path(platform, source_key, project_id)
        if not path.exists():
            return None
        return EarlyListing.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, platform: str, source_key: str, project_id: str) -> None:
        path = self._path(platform, source_key, project_id)
        path.unlink(missing_ok=True)
