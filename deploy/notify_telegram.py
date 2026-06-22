"""Send a one-off Telegram notification (operator ping)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.telegram_bot.bot import TelegramReviewBot


async def main() -> None:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        text = "✅ Freelance Auto-Responder: ревью и тесты завершены."
    settings = get_settings()
    bot = TelegramReviewBot(settings.telegram_bot_token, settings.telegram_chat_id)
    try:
        await bot.notify(text)
        print("sent")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
