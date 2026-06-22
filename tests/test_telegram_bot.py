from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Update

from src.telegram_bot.bot import TelegramReviewBot


def _make_update(text: str) -> Update:
    message = {
        "message_id": 1,
        "date": "2025-01-01T00:00:00Z",
        "chat": {"id": 123, "type": "private"},
        "from": {"id": 456, "is_bot": False, "first_name": "Test"},
        "text": text,
    }
    if text.startswith("/"):
        command = text.split()[0]
        message["entities"] = [
            {
                "type": "bot_command",
                "offset": 0,
                "length": len(command),
            }
        ]
    return Update.model_validate({"update_id": 1, "message": message})


@pytest.mark.asyncio
async def test_regular_text_uses_response_flow() -> None:
    text_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=text_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("hello"))

    text_handler.assert_awaited_once()
    await bot.close()


@pytest.mark.asyncio
async def test_slash_command_not_handled_as_response_text() -> None:
    text_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=text_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/journal"))

    text_handler.assert_not_awaited()
    await bot.close()
