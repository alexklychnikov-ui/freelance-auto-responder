import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_settings
from src.telegram_bot.bot import TelegramReviewBot


async def main() -> None:
    s = get_settings()
    bot = TelegramReviewBot(s.telegram_bot_token, s.telegram_chat_id)
    try:
        await bot.notify(
            "✅ На «Текст отклика» теперь кнопки:\n"
            "✅ Подтвердить отклик · 🔄 Перегенерировать\n"
            "Деплой готов — следующий отклик уже с ними."
        )
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
