"""Re-run Kwork prepare (server-side draft) and notify in Telegram."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.models import PendingOffer
from src.pipeline.orchestrator import build_orchestrator
from src.responses.prepared_store import PreparedResponseStore


async def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else "3202784"
    settings = get_settings()
    store = PreparedResponseStore(settings.prepared_responses_dir)
    item = next((i for i in store.list_all() if i.project_id == project_id), None)
    if item is None:
        print("not found")
        sys.exit(1)

    offer = PendingOffer(
        platform=item.platform,
        source_key=item.source_key,
        project_id=item.project_id,
        url=item.url,
        title=item.title,
        project=item.project,
        score=item.score,
        created_at=item.prepared_at,
        status="approved",
        response_text=item.response_text,
    )
    orch = build_orchestrator(settings)
    try:
        await orch._prepare_offer_on_site(offer)
    finally:
        orch.close()
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
