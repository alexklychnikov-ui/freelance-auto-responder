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


@pytest.mark.asyncio
async def test_kwork_url_triggers_manual_project_handler() -> None:
    manual_handler = AsyncMock()
    text_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=text_handler,
        on_manual_project=manual_handler,
    )

    await bot.dispatcher.feed_update(
        bot.bot,
        _make_update("https://kwork.ru/projects/3204427/view"),
    )

    manual_handler.assert_awaited_once()
    assert manual_handler.await_args.args[1] == "3204427"
    text_handler.assert_not_awaited()
    await bot.close()


def test_prepared_keyboard_has_confirm_and_regenerate() -> None:
    from datetime import datetime, timezone

    from src.models import GptScoreResult, PendingOffer, ProjectFull
    from src.telegram_bot.bot import (
        CALLBACK_JOURNAL_CONFIRM,
        CALLBACK_REGENERATE,
        build_journal_confirm_keyboard,
    )

    offer = PendingOffer(
        platform="kwork",
        source_key="kwork_manual",
        project_id="3217293",
        url="https://kwork.ru/projects/3217293/view",
        title="test",
        project=ProjectFull(
            platform="kwork",
            source_key="kwork_manual",
            project_id="3217293",
            url="https://kwork.ru/projects/3217293/view",
            title="test",
            full_description="desc",
        ),
        score=GptScoreResult(
            score=8,
            fit=True,
            reason="ok",
            matched_skills=[],
            risks=[],
            suggested_project_type="Telegram-бот",
            competition_level="low",
            recommendation="откликаться",
        ),
        created_at=datetime.now(timezone.utc),
    )
    kb = build_journal_confirm_keyboard(offer)
    row = kb.inline_keyboard[0]
    assert len(row) == 2
    assert row[0].text == "✅ Подтвердить отклик"
    assert row[1].text == "🔄 Перегенерировать"
    assert CALLBACK_JOURNAL_CONFIRM in (row[0].callback_data or "")
    assert CALLBACK_REGENERATE in (row[1].callback_data or "")
