import asyncio

from src.config import get_settings
from src.pipeline.orchestrator import build_orchestrator


async def main() -> None:
    orch = build_orchestrator(get_settings())
    count = await orch.export_prepared_to_journal()
    print("exported", count)
    orch.close()


asyncio.run(main())
