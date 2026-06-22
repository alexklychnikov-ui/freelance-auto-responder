"""Generate response text for a project (stdout)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_settings, get_enabled_sources
from src.models import PendingOffer, ProjectFull, GptScoreResult
from src.pipeline.orchestrator import build_orchestrator
from src.responses.prepared_store import PreparedResponseStore
from datetime import datetime, timezone


async def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else "3202784"
    settings = get_settings()
    store = PreparedResponseStore(settings.prepared_responses_dir)
    item = next((i for i in store.list_all() if i.project_id == project_id), None)

    orch = build_orchestrator(settings)
    try:
        if item:
            offer = PendingOffer(
                platform=item.platform,
                source_key=item.source_key,
                project_id=item.project_id,
                url=item.url,
                title=item.title,
                project=item.project,
                score=item.score,
                created_at=datetime.now(timezone.utc),
                status="approved",
            )
        else:
            offer = PendingOffer(
                platform="kwork",
                source_key="kwork_dev_it",
                project_id=project_id,
                url=f"https://kwork.ru/projects/{project_id}/view",
                title="",
                project=ProjectFull(
                    platform="kwork",
                    source_key="kwork_dev_it",
                    project_id=project_id,
                    url=f"https://kwork.ru/projects/{project_id}/view",
                    title="",
                    full_description="",
                ),
                score=GptScoreResult(
                    score=8,
                    fit=True,
                    reason="",
                    matched_skills=["Python", "парсинг"],
                    risks=[],
                    suggested_project_type="Парсинг",
                    competition_level="medium",
                    recommendation="откликаться",
                ),
                created_at=datetime.now(timezone.utc),
                status="approved",
            )

        await orch._refresh_offer_project(offer)
        if not (offer.project.full_description or "").strip():
            offer.project = offer.project.model_copy(
                update={
                    "title": offer.project.title or "Нужно сделать парсер",
                    "full_description": (
                        "Нужно сделать парсер, который будет собирать ссылки "
                        "на публикации в LinkedIn."
                    ),
                }
            )
        text = await orch._generate_response_text(offer)
        print(text)
    finally:
        orch.close()


if __name__ == "__main__":
    asyncio.run(main())
