from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.models import PendingOffer

logger = logging.getLogger(__name__)


def _offer_key(offer: PendingOffer) -> str:
    return f"{offer.platform}_{offer.source_key}_{offer.project_id}"


class PendingStore:
    def __init__(self, base_dir: str | Path = "data/pending_offers") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, platform: str, source_key: str, project_id: str) -> Path:
        return self.base_dir / f"{platform}_{source_key}_{project_id}.json"

    def save(self, offer: PendingOffer) -> Path:
        path = self._path_for(offer.platform, offer.source_key, offer.project_id)
        path.write_text(
            offer.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info(
            "pending_saved key=%s status=%s",
            path.stem,
            offer.status,
        )
        return path

    def load(self, platform: str, source_key: str, project_id: str) -> PendingOffer | None:
        path = self._path_for(platform, source_key, project_id)
        if not path.exists():
            return None
        return PendingOffer.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, platform: str, source_key: str, project_id: str) -> None:
        path = self._path_for(platform, source_key, project_id)
        if path.exists():
            path.unlink()

    def list_all(self) -> list[PendingOffer]:
        offers: list[PendingOffer] = []
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                offers.append(
                    PendingOffer.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, ValueError):
                logger.warning("pending_corrupt file=%s", path.name)
        return offers

    def list_pending(self) -> list[PendingOffer]:
        return [o for o in self.list_all() if o.status == "pending"]

    def list_awaiting_submit(self) -> list[PendingOffer]:
        return [
            o
            for o in self.list_all()
            if o.status == "approved" and o.draft_message_id is not None
        ]

    def find_by_draft_message_id(self, message_id: int) -> PendingOffer | None:
        for offer in self.list_awaiting_submit():
            if offer.draft_message_id == message_id:
                return offer
        return None

    def expire_stale(self, timeout_hours: int) -> list[PendingOffer]:
        now = datetime.now(timezone.utc)
        expired: list[PendingOffer] = []
        for offer in self.list_pending():
            created = offer.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_hours = (now - created).total_seconds() / 3600
            if age_hours >= timeout_hours:
                offer.status = "expired"
                self.save(offer)
                expired.append(offer)
        return expired
