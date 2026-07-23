"""One-shot TG notify from VPS."""
from __future__ import annotations

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
            "✅ Поправил отклики (деплой на VPS):\n"
            "1) «Ирина» — GPT копировал имя из примера в промпте. "
            "Теперь имя только из buyer; выдуманное «Имя, здравствуйте!» срезается.\n"
            "2) Текст режется на абзацы (открытие / решение / срок+цена+CTA).\n"
            "3) В сообщении «Текст отклика» теперь заголовок + ссылка на проект.\n\n"
            "Перегенерируй старый отклик кнопкой 🔄 — уйдёт уже в новом формате."
        )
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
