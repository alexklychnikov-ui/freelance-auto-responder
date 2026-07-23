from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.models import GptScoreResult, OfferTerms, PendingOffer, ProjectFull
from src.pipeline.orchestrator import PipelineOrchestrator
from src.store.repository import ProjectRepository

UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.example.com/openai/v1",
        telegram_bot_token="token",
        telegram_chat_id="1",
        response_journal=str(tmp_path / "journal.xlsx"),
        database_path=str(tmp_path / "test.db"),
        prepared_responses_dir=str(tmp_path / "prepared"),
        scan_bootstrap_skip_pipeline=False,
        require_telegram_approval=True,
        prepare_only_no_submit=True,
        yandex_max_daily_responses=7,
        yandex_storage_state=str(tmp_path / "yandex_storage.json"),
        sources_config_path="config/sources.yaml",
        _env_file=None,
    )


@pytest.fixture
def yandex_full() -> ProjectFull:
    return ProjectFull(
        platform="yandex_uslugi",
        source_key="yandex_uslugi_it",
        project_id=UUID,
        url=f"https://uslugi.yandex.ru/order/{UUID}",
        title="Telegram bot",
        full_description="Need aiogram bot with sheets sync.",
        desired_budget="15000",
    )


@pytest.fixture
def score() -> GptScoreResult:
    return GptScoreResult(
        score=9,
        fit=True,
        reason="match",
        matched_skills=["Python"],
        risks=[],
        suggested_project_type="Telegram-бот",
        competition_level="low",
        recommendation="откликаться",
    )


def _make_orch(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> tuple[PipelineOrchestrator, MagicMock]:
    repo = ProjectRepository(settings.database_path)
    mock_adapter = MagicMock()
    mock_adapter.scan_new.return_value = []
    mock_adapter.read_full.return_value = project_full
    mock_adapter.prepare_response = MagicMock(
        return_value=MagicMock(success=True, project_id=UUID, message="prepared")
    )
    mock_adapter.submit_response.return_value = MagicMock(
        success=False, project_id=UUID, message="manual_only"
    )
    mock_adapter.close = MagicMock()

    mock_estimator = MagicMock()
    mock_estimator.estimate.return_value = OfferTerms(
        price_rub=12000, delivery_days=10, plan_summary=""
    )
    mock_estimator.estimate_market_cost.return_value = 12000
    mock_estimator.close = MagicMock()

    mock_scorer = MagicMock()
    mock_scorer.score.return_value = score
    mock_scorer.close = MagicMock()

    mock_generator = MagicMock()
    mock_generator.generate.return_value = "Текст отклика для Яндекс"
    mock_generator.close = MagicMock()

    mock_lightrag = MagicMock()
    mock_lightrag.get_full_context.return_value = "ctx"

    mock_tg = MagicMock()
    mock_tg.send_review_card = AsyncMock(return_value=1)
    mock_tg.send_offer_link = AsyncMock(return_value=2)
    mock_tg.send_form_prepared_ready = AsyncMock(return_value=3)
    mock_tg.send_yandex_manual_copy = AsyncMock(return_value=4)
    mock_tg.send_manual_copy = AsyncMock(return_value=4)
    mock_tg.mark_review_skipped = AsyncMock()
    mock_tg.mark_review_approved = AsyncMock()
    mock_tg.notify = AsyncMock()
    mock_tg.close = AsyncMock()

    from src.journal.writer import JournalWriter
    from src.responses.prepared_store import PreparedResponseStore
    from src.telegram_bot.pending_store import PendingStore
    from src.telegram_bot.review_service import ReviewService

    store = PendingStore(base_dir=Path(settings.database_path).parent / "pending")
    review = ReviewService(settings, store, mock_tg, repo)

    orch = PipelineOrchestrator(
        settings=settings,
        repository=repo,
        review_service=review,
        scorer=mock_scorer,
        response_generator=mock_generator,
        lightrag=mock_lightrag,
        journal=JournalWriter(settings.response_journal),
        prepared_store=PreparedResponseStore(settings.prepared_responses_dir),
        offer_estimator=mock_estimator,
        adapter_factory=lambda _s, _b=None: mock_adapter,
        browser=MagicMock(),
    )
    return orch, mock_adapter


@pytest.mark.asyncio
async def test_yandex_approve_skips_prepare(
    settings: Settings, yandex_full: ProjectFull, score: GptScoreResult
) -> None:
    orch, mock_adapter = _make_orch(settings, yandex_full, score)
    offer = PendingOffer(
        platform="yandex_uslugi",
        source_key="yandex_uslugi_it",
        project_id=UUID,
        url=yandex_full.url,
        title=yandex_full.title,
        project=yandex_full,
        score=score,
        created_at=datetime.now(timezone.utc),
        status="approved",
        approved_at=datetime.now(timezone.utc),
        response_text="Готовый текст для ручного копирования.",
    )
    orch.review_service.store.save(offer)

    await orch.handle_approve_click(
        "yandex_uslugi", "yandex_uslugi_it", UUID, offer, None
    )

    mock_adapter.prepare_response.assert_not_called()
    orch.review_service.tg_bot.send_manual_copy.assert_awaited_once()
    orch.review_service.tg_bot.send_form_prepared_ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_yandex_regenerate_skips_prepare(
    settings: Settings, yandex_full: ProjectFull, score: GptScoreResult
) -> None:
    orch, mock_adapter = _make_orch(settings, yandex_full, score)
    offer = PendingOffer(
        platform="yandex_uslugi",
        source_key="yandex_uslugi_it",
        project_id=UUID,
        url=yandex_full.url,
        title=yandex_full.title,
        project=yandex_full,
        score=score,
        created_at=datetime.now(timezone.utc),
        status="prepared",
        response_text="old",
    )
    orch.review_service.store.save(offer)

    await orch.handle_regenerate_response(
        "yandex_uslugi", "yandex_uslugi_it", UUID, None
    )

    mock_adapter.prepare_response.assert_not_called()
    orch.review_service.tg_bot.send_manual_copy.assert_awaited_once()
