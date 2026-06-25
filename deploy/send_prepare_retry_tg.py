"""Send Telegram message with «Заполнить форму снова» for a project."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.models import PendingOffer
from src.responses.prepared_store import PreparedResponseStore
from src.telegram_bot.bot import TelegramReviewBot
from src.telegram_bot.pending_store import PendingStore


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: send_prepare_retry_tg.py <project_id> [error]")
        sys.exit(1)
    project_id = sys.argv[1]
    error = sys.argv[2] if len(sys.argv) > 2 else "Повтори автозаполнение формы на Kwork."
    settings = get_settings()
    pending = PendingStore()
    offer = next(
        (o for o in pending.list_all() if o.project_id == project_id),
        None,
    )
    if offer is None:
        prepared = PreparedResponseStore(settings.prepared_responses_dir)
        item = next((i for i in prepared.list_all() if i.project_id == project_id), None)
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
        )
    bot = TelegramReviewBot(settings.telegram_bot_token, settings.telegram_chat_id)
    try:
        await bot.send_prepare_retry(offer, error=error)
        print("sent")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
