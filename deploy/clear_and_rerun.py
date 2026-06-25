"""Clear cached response text and re-run prepare for a project."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.models import PendingOffer
from src.pipeline.orchestrator import build_orchestrator
from src.responses.prepared_store import PreparedResponseStore


def clear_response(project_id: str) -> bool:
    settings = get_settings()
    store = PreparedResponseStore(settings.prepared_responses_dir)
    item = next((i for i in store.list_all() if i.project_id == project_id), None)
    if item is None:
        matches = list(Path(settings.prepared_responses_dir).glob(f"*_{project_id}.json"))
        if not matches:
            return False
        for path in matches:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["response_text"] = ""
            data["journal_confirmed"] = False
            data["journal_exported"] = False
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print("cleared", path.name)
        return True
    item.response_text = ""
    item.journal_confirmed = False
    item.journal_exported = False
    store.save(item)
    print("cleared", project_id)
    return True


async def rerun(project_id: str) -> None:
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
        response_text="",
    )
    orch = build_orchestrator(settings)
    try:
        await orch._prepare_offer_on_site(offer)
    finally:
        orch.close()
    print("done", project_id)


async def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not project_id:
        print("Usage: clear_and_rerun.py <project_id>")
        sys.exit(1)
    if not clear_response(project_id):
        print("prepared not found", project_id)
        sys.exit(1)
    await rerun(project_id)


if __name__ == "__main__":
    asyncio.run(main())
