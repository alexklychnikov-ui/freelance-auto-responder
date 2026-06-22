"""Resend prepared offer text to Telegram (copy-paste package)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.models import PendingOffer
from src.responses.prepared_store import PreparedResponseStore
from src.telegram_bot.bot import TelegramReviewBot


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: resend_prepared_tg.py <project_id>")
        sys.exit(1)
    project_id = sys.argv[1]
    settings = get_settings()
    store = PreparedResponseStore(settings.prepared_responses_dir)
    items = [i for i in store.list_all() if i.project_id == project_id]
    if not items:
        print("not found")
        sys.exit(1)
    item = items[0]
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
        await bot.send_prepared_offer_details(
            offer,
            response_text=item.response_text,
            price=item.price,
            delivery_days=item.delivery_days,
            deadline_manual=True,
        )
        print("sent")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
