from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.journal.vps_sync import sync_journal_on_vps
from src.journal.writer import JournalWriter
from src.responses.prepared_store import PreparedResponseStore
from src.telegram_bot.bot import TelegramReviewBot


async def main() -> None:
    settings = get_settings()
    writer = JournalWriter(settings.response_journal)
    prepared = PreparedResponseStore(settings.prepared_responses_dir)
    result = sync_journal_on_vps(
        settings=settings,
        writer=writer,
        prepared_store=prepared,
    )
    bot = TelegramReviewBot(settings.telegram_bot_token, settings.telegram_chat_id)
    try:
        caption = (
            f"📒 VPS journal sync done\n"
            f"Prepared +{result.appended_prepared}, notes {result.updated_notes}\n"
            f"Offers update {result.offers_updated}, append {result.offers_appended}"
        )
        if result.offers_error:
            caption += f"\n⚠️ {result.offers_error}"
        await bot.send_document(settings.response_journal, caption=caption[:1000])
        print("sent")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
