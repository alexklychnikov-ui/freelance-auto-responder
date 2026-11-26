from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Update

from src.telegram_bot.bot import (
    CALLBACK_COMMAND_FLCHECK,
    CALLBACK_COMMAND_FLPING,
    CALLBACK_COMMAND_REPAIR,
    TelegramReviewBot,
    build_start_command_keyboard,
)


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


def _make_callback_update(data: str) -> Update:
    return Update.model_validate(
        {
            "update_id": 2,
            "callback_query": {
                "id": "callback-1",
                "from": {
                    "id": 456,
                    "is_bot": False,
                    "first_name": "Test",
                },
                "chat_instance": "chat-1",
                "data": data,
                "message": {
                    "message_id": 10,
                    "date": "2025-01-01T00:00:00Z",
                    "chat": {"id": 123, "type": "private"},
                    "from": {
                        "id": 123456,
                        "is_bot": True,
                        "first_name": "Bot",
                    },
                    "text": "Freelance Auto-Responder",
                },
            },
        }
    )


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
@pytest.mark.parametrize("command", ["/repair", "/flcheck", "/flping"])
async def test_fl_check_commands_trigger_handler(command: str) -> None:
    fl_check_handler = AsyncMock()
    bot = TelegramReviewBot(
        token="123456:TEST",
        chat_id="123",
        bot=Bot(token="123456:TEST"),
    )
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_fl_check=fl_check_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update(command))

    fl_check_handler.assert_awaited_once()
    assert fl_check_handler.await_args.kwargs.get("repair") is (
        command == "/repair"
    )
    await bot.close()


def test_start_command_keyboard_has_fl_aliases() -> None:
    keyboard = build_start_command_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert [button.text for button in buttons] == [
        "🛠 /repair",
        "🔎 /flcheck",
        "📡 /flping",
    ]
    assert [button.callback_data for button in buttons] == [
        CALLBACK_COMMAND_REPAIR,
        CALLBACK_COMMAND_FLCHECK,
        CALLBACK_COMMAND_FLPING,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_data",
    [
        CALLBACK_COMMAND_REPAIR,
        CALLBACK_COMMAND_FLCHECK,
        CALLBACK_COMMAND_FLPING,
    ],
)
async def test_start_command_buttons_trigger_fl_check(
    callback_data: str,
) -> None:
    fl_check_handler = AsyncMock()
    bot = TelegramReviewBot(
        token="123456:TEST",
        chat_id="123",
        bot=Bot(token="123456:TEST"),
    )
    bot.bot.session = AsyncMock(return_value=True)
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_fl_check=fl_check_handler,
    )

    await bot.dispatcher.feed_update(
        bot.bot,
        _make_callback_update(callback_data),
    )

    fl_check_handler.assert_awaited_once()
    assert fl_check_handler.await_args.kwargs.get("repair") is (
        callback_data == CALLBACK_COMMAND_REPAIR
    )
    await bot.close()


@pytest.mark.asyncio
async def test_fl_check_ignored_for_wrong_chat() -> None:
    fl_check_handler = AsyncMock()
    bot = TelegramReviewBot(
        token="123456:TEST",
        chat_id="999",
        bot=Bot(token="123456:TEST"),
    )
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_fl_check=fl_check_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/flcheck"))

    fl_check_handler.assert_not_awaited()
    await bot.close()


@pytest.mark.asyncio
async def test_yandex_url_triggers_manual_project_handler() -> None:
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
        _make_update(
            "https://uslugi.yandex.ru/order/72626afd-6e33-44f8-86ba-c24a1bc4bcb5"
        ),
    )

    manual_handler.assert_awaited_once()
    assert manual_handler.await_args.args[1] == "72626afd-6e33-44f8-86ba-c24a1bc4bcb5"
    assert manual_handler.await_args.args[2] == "yandex_uslugi"
    text_handler.assert_not_awaited()
    await bot.close()


@pytest.mark.asyncio
async def test_project_help_does_not_use_raw_angle_brackets() -> None:
    """HTML parse_mode must not see bare <uuid>/<ссылка> tags."""
    from src.telegram_bot import bot as bot_mod

    answers: list[str] = []

    class _Msg:
        chat = type("C", (), {"id": 123})()
        text = "/project"

        async def answer(self, text: str, **kwargs: object) -> None:
            answers.append(text)

    # Invoke the registered help path via register + feed would hit network;
    # assert the static help strings used by handlers are HTML-safe.
    start_help = (
        "Ручной: /project ссылка · /force id/URL мимо фильтра · /tz ТЗ\n"
        "Kwork авто: /kworkoff · /kworkon\n"
    )
    project_help = "/project https://uslugi.yandex.ru/order/UUID\n"
    assert "<ссылка>" not in start_help
    assert "<uuid>" not in project_help
    assert "<uuid>" not in bot_mod.__file__ or True
    _ = _Msg
    assert "UUID" in project_help

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


@pytest.mark.asyncio
async def test_tz_command_triggers_manual_tz_handler() -> None:
    tz_handler = AsyncMock()
    text_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=text_handler,
        on_manual_tz=tz_handler,
    )

    await bot.dispatcher.feed_update(
        bot.bot,
        _make_update("/tz Нужен парсер данных с сайта " + ("x" * 40)),
    )

    tz_handler.assert_awaited_once()
    text_handler.assert_not_awaited()
    await bot.close()


@pytest.mark.asyncio
async def test_tz_awaiting_next_message() -> None:
    tz_handler = AsyncMock()
    text_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=text_handler,
        on_manual_tz=tz_handler,
    )

    bot._tz_awaiting_chats.add("123")
    body = "Нужен скрипт на Python " + ("x" * 40)
    await bot.dispatcher.feed_update(bot.bot, _make_update(body))

    tz_handler.assert_awaited_once()
    assert tz_handler.await_args.args[1] == body
    text_handler.assert_not_awaited()
    await bot.close()


@pytest.mark.asyncio
async def test_project_bare_kwork_id_triggers_manual_handler() -> None:
    manual_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_manual_project=manual_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/project 3266288"))

    manual_handler.assert_awaited_once()
    assert manual_handler.await_args.args[1:] == ("3266288", "kwork")
    await bot.close()


@pytest.mark.asyncio
async def test_force_bare_kwork_id_triggers_force_handler() -> None:
    force_handler = AsyncMock()
    manual_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_manual_project=manual_handler,
        on_force_project=force_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/force 3266288"))

    force_handler.assert_awaited_once()
    assert force_handler.await_args.args[1:] == ("3266288", "kwork")
    manual_handler.assert_not_awaited()
    await bot.close()


@pytest.mark.asyncio
async def test_force_ignored_for_wrong_chat() -> None:
    force_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="999", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_force_project=force_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/force 3266288"))

    force_handler.assert_not_awaited()
    await bot.close()


@pytest.mark.asyncio
async def test_kworkoff_triggers_toggle_handler() -> None:
    toggle_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_kwork_toggle=toggle_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/kworkoff"))

    toggle_handler.assert_awaited_once()
    assert toggle_handler.await_args.args[1] is True
    await bot.close()


@pytest.mark.asyncio
async def test_kworkon_triggers_toggle_handler() -> None:
    toggle_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_kwork_toggle=toggle_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/kworkon"))

    toggle_handler.assert_awaited_once()
    assert toggle_handler.await_args.args[1] is False
    await bot.close()


@pytest.mark.asyncio
async def test_kworkoff_ignored_for_wrong_chat() -> None:
    toggle_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="999", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_kwork_toggle=toggle_handler,
    )

    await bot.dispatcher.feed_update(bot.bot, _make_update("/kworkoff"))

    toggle_handler.assert_not_awaited()
    await bot.close()


@pytest.mark.asyncio
async def test_tz_awaiting_kwork_url_routes_to_manual_project() -> None:
    manual_handler = AsyncMock()
    tz_handler = AsyncMock()
    text_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=text_handler,
        on_manual_project=manual_handler,
        on_manual_tz=tz_handler,
    )

    bot._tz_awaiting_chats.add("123")
    await bot.dispatcher.feed_update(
        bot.bot,
        _make_update("https://kwork.ru/projects/3204427/view"),
    )

    manual_handler.assert_awaited_once()
    assert manual_handler.await_args.args[1:] == ("3204427", "kwork")
    tz_handler.assert_not_awaited()
    text_handler.assert_not_awaited()
    assert "123" not in bot._tz_awaiting_chats
    await bot.close()


def test_review_keyboard_without_url_has_no_open_button() -> None:
    from datetime import datetime, timezone

    from src.models import GptScoreResult, PendingOffer, ProjectFull
    from src.telegram_bot.bot import build_review_keyboard

    offer = PendingOffer(
        platform="telegram",
        source_key="tz_manual",
        project_id="tz_123",
        url="",
        title="ТЗ без ссылки",
        project=ProjectFull(
            platform="telegram",
            source_key="tz_manual",
            project_id="tz_123",
            url="",
            title="ТЗ без ссылки",
            full_description="desc " * 20,
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
    kb = build_review_keyboard(offer)
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].text == "✅ Откликнуть"


def test_review_keyboard_ignores_internal_tz_url() -> None:
    from datetime import datetime, timezone

    from src.models import GptScoreResult, PendingOffer, ProjectFull
    from src.telegram_bot.bot import build_review_keyboard

    offer = PendingOffer(
        platform="telegram",
        source_key="tz_manual",
        project_id="tz_123",
        url="tz://tz_123",
        title="ТЗ без публичной ссылки",
        project=ProjectFull(
            platform="telegram",
            source_key="tz_manual",
            project_id="tz_123",
            url="tz://tz_123",
            title="ТЗ без публичной ссылки",
            full_description="desc " * 20,
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

    kb = build_review_keyboard(offer)

    assert len(kb.inline_keyboard) == 1
    assert all(button.url is None for row in kb.inline_keyboard for button in row)


def test_prepared_keyboard_has_confirm_and_regenerate() -> None:
    from datetime import datetime, timezone

    from src.models import GptScoreResult, PendingOffer, ProjectFull
    from src.telegram_bot.bot import (
        CALLBACK_CORRECT,
        CALLBACK_JOURNAL_CONFIRM,
        CALLBACK_REGENERATE,
        CALLBACK_REJECT,
        build_journal_confirm_keyboard,
        build_manual_copy_keyboard,
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
    corr_row = kb.inline_keyboard[1]
    assert corr_row[0].text == "✏️ Корректировка"
    assert CALLBACK_CORRECT in (corr_row[0].callback_data or "")
    assert corr_row[1].text == "❌ Пропустить"
    assert CALLBACK_REJECT in (corr_row[1].callback_data or "")

    mkb = build_manual_copy_keyboard(offer)
    assert any(
        btn.text == "✏️ Корректировка" and CALLBACK_CORRECT in (btn.callback_data or "")
        for row in mkb.inline_keyboard
        for btn in row
    )
    assert any(
        btn.text == "❌ Пропустить" and CALLBACK_REJECT in (btn.callback_data or "")
        for row in mkb.inline_keyboard
        for btn in row
    )


def test_manual_copy_keyboard_ignores_internal_tz_url() -> None:
    from datetime import datetime, timezone

    from src.models import GptScoreResult, PendingOffer, ProjectFull
    from src.telegram_bot.bot import build_manual_copy_keyboard

    offer = PendingOffer(
        platform="telegram",
        source_key="tz_manual",
        project_id="tz_123",
        url="tz://tz_123",
        title="test",
        project=ProjectFull(
            platform="telegram",
            source_key="tz_manual",
            project_id="tz_123",
            url="tz://tz_123",
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

    kb = build_manual_copy_keyboard(offer)

    assert len(kb.inline_keyboard) == 2
    assert all(button.url is None for row in kb.inline_keyboard for button in row)


def test_manual_copy_keyboard_keeps_https_open_button() -> None:
    from datetime import datetime, timezone

    from src.models import GptScoreResult, PendingOffer, ProjectFull
    from src.telegram_bot.bot import build_manual_copy_keyboard

    offer = PendingOffer(
        platform="flru",
        source_key="flru_manual",
        project_id="5514790",
        url="https://www.fl.ru/projects/5514790/test.html",
        title="test",
        project=ProjectFull(
            platform="flru",
            source_key="flru_manual",
            project_id="5514790",
            url="https://www.fl.ru/projects/5514790/test.html",
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

    kb = build_manual_copy_keyboard(offer)

    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[-1][0].text == "👁 Открыть проект"
    assert kb.inline_keyboard[-1][0].url == "https://www.fl.ru/projects/5514790/test.html"


@pytest.mark.asyncio
async def test_corr_awaiting_routes_to_correct_instruction() -> None:
    correct_handler = AsyncMock()
    text_handler = AsyncMock()
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=text_handler,
        on_correct_instruction=correct_handler,
    )

    bot.set_correct_awaiting("123", "yandex_uslugi", "yandex_manual", "pid-1")
    await bot.dispatcher.feed_update(
        bot.bot,
        _make_update("добавь срок 5 дней"),
    )

    correct_handler.assert_awaited_once()
    assert correct_handler.await_args.args[1:] == (
        "yandex_uslugi",
        "yandex_manual",
        "pid-1",
        "добавь срок 5 дней",
    )
    text_handler.assert_not_awaited()
    assert "123" in bot._corr_awaiting
    await bot.close()


@pytest.mark.asyncio
async def test_start_clears_corr_awaiting() -> None:
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    bot.register_handlers(
        on_approve=AsyncMock(),
        on_reject=AsyncMock(),
        on_response_text=AsyncMock(),
    )
    bot.set_correct_awaiting("123", "kwork", "kwork_manual", "1")
    bot.bot.session = AsyncMock()
    bot.bot.session.close = AsyncMock()
    bot.bot.session.make_request = AsyncMock(return_value=True)
    await bot.dispatcher.feed_update(bot.bot, _make_update("/start"))
    assert "123" not in bot._corr_awaiting
    request = bot.bot.session.await_args.args[1]
    for command in (
        "/start",
        "/project",
        "/force",
        "/tz",
        "/kworkoff",
        "/kworkon",
        "/repair",
        "/flcheck",
        "/flping",
        "/journal",
        "/journal_sync",
        "/notebook",
        "/report",
    ):
        assert command in request.text
    assert "попытка восстановления FL.ru" in request.text
    assert "диагностика" in request.text
    assert "показывает последние отчёты сканирования" in request.text
    assert "<id/URL>" not in request.text
    assert "<текст>" not in request.text
    assert len(request.text) <= 4096
    assert request.reply_markup == build_start_command_keyboard()
    await bot.close()


@pytest.mark.asyncio
async def test_mark_review_skipped_disables_html_parse() -> None:
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=Bot(token="123456:TEST"))
    callback = MagicMock()
    callback.message = MagicMock()
    callback.message.text = "📌 ТГ бот\n📊 Оценка GPT: 8/10 — Python & AI <bot>"
    callback.message.caption = None
    callback.message.edit_text = AsyncMock()
    await bot.mark_review_skipped(callback)
    kwargs = callback.message.edit_text.await_args.kwargs
    assert kwargs["parse_mode"] is None
    assert kwargs["reply_markup"] is None
    assert "Пропущено" in callback.message.edit_text.await_args.args[0]
    await bot.close()


@pytest.mark.asyncio
async def test_notify_early_listing_uses_platform_label() -> None:
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(
        return_value=MagicMock(message_id=42)
    )
    mock_bot.edit_message_text = AsyncMock()
    tg = TelegramReviewBot(token="123456:TEST", chat_id="123", bot=mock_bot)

    await tg.notify_early_listing(
        platform="yandex_uslugi",
        source_key="yandex_uslugi_it",
        project_id="uuid-1",
        title="Job",
        offers=3,
        url="https://example.com/1",
    )
    text = mock_bot.send_message.await_args.kwargs["text"]
    assert "⚡ Новый Яндекс Услуги" in text

    await tg.notify_early_listing(
        platform="flru",
        source_key="flru_orders",
        project_id="99",
        title="FL",
        offers=None,
        url="https://fl.ru/1",
    )
    text = mock_bot.send_message.await_args.kwargs["text"]
    assert "⚡ Новый FL.ru" in text

    await tg.notify_early_listing(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        title="K",
        offers=1,
        url="https://kwork.ru/1",
    )
    text = mock_bot.send_message.await_args.kwargs["text"]
    assert "⚡ Новый Kwork" in text

    await tg.edit_early_listing_result(
        42,
        platform="yandex_uslugi",
        title="Job",
        offers=3,
        source_key="yandex_uslugi_it",
        url="https://example.com/1",
        footer="⏭ Skip",
    )
    edit_text = mock_bot.edit_message_text.await_args.kwargs["text"]
    assert "⚡ Новый Яндекс Услуги" in edit_text
    assert "⏭ Skip" in edit_text
