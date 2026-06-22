import asyncio
from src.config import get_settings
from src.telegram_bot.bot import TelegramReviewBot

MSG = (
    "🤖 Бот переведён в режим daemon — кнопки теперь работают.\n\n"
    "❌ <b>Пропустить</b> — карточка помечается «Пропущено», кнопки убираются.\n\n"
    "✅ <b>Откликнуть</b> — GPT сгенерирует черновик по правилам LightRAG.\n"
    "Отредактируй и отправь <b>ответом на сообщение с черновиком</b> — "
    "бот заполнит форму на Kwork (DRY_RUN если включён).\n\n"
    "Можешь снова нажать кнопку на карточке W5300 или дождаться следующего скана."
)


async def main() -> None:
    s = get_settings()
    bot = TelegramReviewBot(s.telegram_bot_token, s.telegram_chat_id)
    await bot.notify(MSG)
    await bot.close()


asyncio.run(main())
