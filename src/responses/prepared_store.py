from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.models import GptScoreResult, ProjectFull

logger = logging.getLogger(__name__)


class PreparedResponse:
    def __init__(
        self,
        *,
        platform: str,
        source_key: str,
        project_id: str,
        url: str,
        title: str,
        project: ProjectFull,
        score: GptScoreResult,
        response_text: str,
        price: str,
        delivery_days: int = 14,
        payment_method: str = "Целиком, когда заказ выполнен",
        prepared_at: datetime | None = None,
        journal_exported: bool = False,
        screenshot_path: str | None = None,
    ) -> None:
        self.platform = platform
        self.source_key = source_key
        self.project_id = project_id
        self.url = url
        self.title = title
        self.project = project
        self.score = score
        self.response_text = response_text
        self.price = price
        self.delivery_days = delivery_days
        self.payment_method = payment_method
        self.prepared_at = prepared_at or datetime.now(timezone.utc)
        self.journal_exported = journal_exported
        self.screenshot_path = screenshot_path

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "source_key": self.source_key,
            "project_id": self.project_id,
            "url": self.url,
            "title": self.title,
            "project": self.project.model_dump(mode="json"),
            "score": self.score.model_dump(mode="json"),
            "response_text": self.response_text,
            "price": self.price,
            "delivery_days": self.delivery_days,
            "payment_method": self.payment_method,
            "prepared_at": self.prepared_at.isoformat(),
            "journal_exported": self.journal_exported,
            "screenshot_path": self.screenshot_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PreparedResponse:
        return cls(
            platform=data["platform"],
            source_key=data["source_key"],
            project_id=data["project_id"],
            url=data["url"],
            title=data["title"],
            project=ProjectFull.model_validate(data["project"]),
            score=GptScoreResult.model_validate(data["score"]),
            response_text=data["response_text"],
            price=data["price"],
            delivery_days=int(data.get("delivery_days", 14)),
            payment_method=data.get("payment_method", "Целиком, когда заказ выполнен"),
            prepared_at=datetime.fromisoformat(data["prepared_at"]),
            journal_exported=bool(data.get("journal_exported", False)),
            screenshot_path=data.get("screenshot_path"),
        )


class PreparedResponseStore:
    def __init__(self, base_dir: str | Path = "data/prepared_responses") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, platform: str, source_key: str, project_id: str) -> Path:
        return self.base_dir / f"{platform}_{source_key}_{project_id}.json"

    def save(self, item: PreparedResponse) -> Path:
        path = self._path(item.platform, item.source_key, item.project_id)
        path.write_text(
            json.dumps(item.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("prepared_saved %s", path.name)
        return path

    def load(self, platform: str, source_key: str, project_id: str) -> PreparedResponse | None:
        path = self._path(platform, source_key, project_id)
        if not path.exists():
            return None
        return PreparedResponse.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_all(self) -> list[PreparedResponse]:
        items: list[PreparedResponse] = []
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                items.append(
                    PreparedResponse.from_dict(json.loads(path.read_text(encoding="utf-8")))
                )
            except (json.JSONDecodeError, ValueError, KeyError):
                logger.warning("prepared_corrupt file=%s", path.name)
        return items

    def list_not_exported(self) -> list[PreparedResponse]:
        return [i for i in self.list_all() if not i.journal_exported]
