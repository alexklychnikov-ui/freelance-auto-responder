from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import GptScoreResult, PendingOffer, ProjectFull
from src.telegram_bot.bot import TelegramReviewBot, _project_view_url


def test_project_view_url() -> None:
    assert (
        _project_view_url("https://kwork.ru/projects/3202677")
        == "https://kwork.ru/projects/3202677/view"
    )
    assert (
        _project_view_url("https://kwork.ru/projects/3202677/view")
        == "https://kwork.ru/projects/3202677/view"
    )


@pytest.mark.asyncio
async def test_send_offer_link_minimal_text() -> None:
    offer = PendingOffer(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3202677",
        url="https://kwork.ru/projects/3202677",
        title="Расчет зарплаты по данным из Excel, выгрузка ведомостей",
        project=ProjectFull(
            platform="kwork",
            source_key="kwork_dev_it",
            project_id="3202677",
            url="https://kwork.ru/projects/3202677",
            title="Расчет зарплаты по данным из Excel, выгрузка ведомостей",
            full_description="",
        ),
        score=GptScoreResult(
            score=8,
            fit=True,
            reason="ok",
            matched_skills=["Python"],
            risks=[],
            suggested_project_type="Автоматизация",
            competition_level="low",
            recommendation="откликаться",
        ),
        created_at=datetime.now(timezone.utc),
        status="approved",
    )
    bot = TelegramReviewBot(token="123456:TEST", chat_id="123")
    bot._bot = MagicMock()
    bot._bot.send_message = AsyncMock(
        return_value=MagicMock(message_id=42)
    )

    msg_id = await bot.send_offer_link(offer)

    assert msg_id == 42
    text = bot._bot.send_message.call_args.kwargs["text"]
    assert "✍️" in text
    assert "Расчет зарплаты" in text
    assert "https://kwork.ru/projects/3202677/view" in text
    assert "ответом на это сообщение" not in text.lower()
