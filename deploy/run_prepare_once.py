import asyncio
import json
from pathlib import Path

from src.config import get_settings
from src.pipeline.orchestrator import build_orchestrator
from src.models import PendingOffer

path = Path("data/pending_offers/kwork_kwork_dev_it_3202099.json")
data = json.loads(path.read_text(encoding="utf-8"))
offer = PendingOffer.model_validate(data)
if not offer.response_text:
    prepared = Path("data/prepared_responses/kwork_kwork_dev_it_3202099.json")
    if prepared.exists():
        offer.response_text = json.loads(prepared.read_text(encoding="utf-8")).get(
            "response_text", ""
        )

async def main():
    orch = build_orchestrator(get_settings())
    await orch.handle_user_response_text(offer, offer.response_text or "")
    orch.close()

asyncio.run(main())
