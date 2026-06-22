import asyncio
from src.config import get_settings
from src.telegram_bot.bot import TelegramReviewBot

MSG = (
    "⚠️ <b>Причина ошибки</b>: на VPS нет сессии Kwork (гость).\n"
    "Форма отклика видна только после логина.\n"
    "Автологин упёрся в CAPTCHA.\n\n"
    "<b>Что сделано:</b>\n"
    "• Текст отклика теперь сохраняется на сервер даже без формы\n"
    "• /journal — Excel\n\n"
    "<b>Для заполнения формы на VPS:</b>\n"
    "1. Один раз залогинься на Kwork с сервера (VNC + браузер)\n"
    "   или пришли cookies — настроим import\n"
    "2. Либо заполняй форму вручную по сохранённому тексту\n\n"
    "Можешь <b>повторно прислать</b> текст ответом на черновик — "
    "сохраню в prepared_responses."
)


async def main() -> None:
    s = get_settings()
    bot = TelegramReviewBot(s.telegram_bot_token, s.telegram_chat_id)
    await bot.notify(MSG)
    await bot.close()


asyncio.run(main())
