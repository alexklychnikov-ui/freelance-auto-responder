import asyncio
from src.config import get_settings
from src.telegram_bot.bot import TelegramReviewBot

MSG = (
    "✅ <b>Готов к приёму отклика</b>\n\n"
    "Пришли откорректированный текст <b>ответом на сообщение с черновиком</b>.\n\n"
    "Что сделаю:\n"
    "1. Заполню форму Kwork (текст, цена, оплата «целиком», срок 14 дн.)\n"
    "2. <b>НЕ нажму</b> «Предложить»\n"
    "3. Пришлю скриншот формы\n"
    "4. Сохраню в <code>data/prepared_responses/</code> на сервере\n\n"
    "Позже: /journal — строка в Excel-журнал"
)


async def main() -> None:
    s = get_settings()
    bot = TelegramReviewBot(s.telegram_bot_token, s.telegram_chat_id)
    await bot.notify(MSG)
    await bot.close()


asyncio.run(main())
