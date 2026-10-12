from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.models import GptScoreResult, PendingOffer, ProjectFull
from src.store.repository import ProjectRepository
from src.telegram_bot.pending_store import PendingStore
from src.telegram_bot.review_service import ReviewService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.example.com/openai/v1",
        telegram_bot_token="token",
        telegram_chat_id="42",
        response_journal=str(tmp_path / "journal.xlsx"),
        database_path=str(tmp_path / "test.db"),
        _env_file=None,
    )


def _offer(*, status: str = "pending", response_text: str | None = None) -> PendingOffer:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3262969",
        url="https://kwork.ru/projects/3262969",
        title="Test approve",
        full_description="Need a Python bot",
    )
    score = GptScoreResult(
        score=8,
        fit=True,
        reason="ok",
        matched_skills=["Python"],
        risks=[],
        suggested_project_type="Bot",
        competition_level="low",
        recommendation="откликаться",
    )
    return PendingOffer(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3262969",
        url=project.url,
        title=project.title,
        project=project,
        score=score,
        created_at=datetime.now(timezone.utc),
        status=status,  # type: ignore[arg-type]
        response_text=response_text,
        approved_at=datetime.now(timezone.utc) if status == "approved" else None,
    )


def _callback(chat_id: str = "42") -> MagicMock:
    cb = MagicMock()
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.chat.id = int(chat_id) if chat_id.isdigit() else chat_id
    return cb


@pytest.mark.asyncio
async def test_approve_pending_answers_once_then_pipeline(
    settings: Settings, tmp_path: Path
) -> None:
    store = PendingStore(tmp_path / "pending")
    offer = _offer(status="pending")
    store.save(offer)
    repo = ProjectRepository(settings.database_path)
    mock_tg = MagicMock()
    mock_tg.chat_id = settings.telegram_chat_id
    mock_tg.notify = AsyncMock()
    mock_tg.register_handlers = MagicMock()
    mock_tg.clear_correct_awaiting = MagicMock()
    mock_tg.set_correct_awaiting = MagicMock()

    on_approve = AsyncMock()
    review = ReviewService(settings, store, mock_tg, repo, on_approve=on_approve)
    cb = _callback()

    await review._handle_approve("kwork", "kwork_dev_it", "3262969", cb)

    cb.answer.assert_awaited_once_with("Готовлю отклик для формы…")
    on_approve.assert_awaited_once()
    saved = store.load("kwork", "kwork_dev_it", "3262969")
    assert saved is not None
    assert saved.status == "approved"


@pytest.mark.asyncio
async def test_approve_stuck_approved_empty_text_resumes(
    settings: Settings, tmp_path: Path
) -> None:
    store = PendingStore(tmp_path / "pending")
    offer = _offer(status="approved", response_text=None)
    store.save(offer)
    repo = ProjectRepository(settings.database_path)
    mock_tg = MagicMock()
    mock_tg.chat_id = settings.telegram_chat_id
    mock_tg.notify = AsyncMock()
    mock_tg.register_handlers = MagicMock()
    mock_tg.clear_correct_awaiting = MagicMock()
    mock_tg.set_correct_awaiting = MagicMock()

    on_approve = AsyncMock()
    review = ReviewService(settings, store, mock_tg, repo, on_approve=on_approve)
    cb = _callback()

    await review._handle_approve("kwork", "kwork_dev_it", "3262969", cb)

    cb.answer.assert_awaited_once_with("Продолжаю подготовку…")
    on_approve.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_rejected_status_alerts_without_pipeline(
    settings: Settings, tmp_path: Path
) -> None:
    store = PendingStore(tmp_path / "pending")
    offer = _offer(status="rejected")
    store.save(offer)
    repo = ProjectRepository(settings.database_path)
    mock_tg = MagicMock()
    mock_tg.chat_id = settings.telegram_chat_id
    mock_tg.notify = AsyncMock()
    mock_tg.register_handlers = MagicMock()
    mock_tg.clear_correct_awaiting = MagicMock()
    mock_tg.set_correct_awaiting = MagicMock()

    on_approve = AsyncMock()
    review = ReviewService(settings, store, mock_tg, repo, on_approve=on_approve)
    cb = _callback()

    await review._handle_approve("kwork", "kwork_dev_it", "3262969", cb)

    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    on_approve.assert_not_awaited()
