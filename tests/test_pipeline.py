from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.models import GptScoreResult, PendingOffer, ProjectFull, ProjectPreview
from src.pipeline.orchestrator import PipelineOrchestrator
from src.store.repository import ProjectRepository


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.example.com/openai/v1",
        telegram_bot_token="token",
        telegram_chat_id="1",
        response_journal=str(tmp_path / "journal.xlsx"),
        database_path=str(tmp_path / "test.db"),
        scan_bootstrap_skip_pipeline=False,
        require_telegram_approval=False,
        min_gpt_score=7,
        _env_file=None,
    )


@pytest.fixture
def project_full() -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="999",
        url="https://kwork.ru/projects/999",
        title="AI bot",
        full_description="Need Python bot",
        desired_budget="5000",
        offers_count=3,
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


def _make_orchestrator(
    settings: Settings,
    *,
    previews: list[ProjectPreview],
    project_full: ProjectFull,
    score: GptScoreResult,
) -> PipelineOrchestrator:
    repo = ProjectRepository(settings.database_path)

    mock_adapter = MagicMock()
    mock_adapter.scan_new.return_value = previews
    mock_adapter.read_full.return_value = project_full
    mock_adapter.submit_response.return_value = MagicMock(
        success=True, project_id="999", message="ok"
    )

    mock_scorer = MagicMock()
    mock_scorer.score.return_value = score
    mock_scorer.close = MagicMock()

    mock_generator = MagicMock()
    mock_generator.generate.return_value = "Generated response text"
    mock_generator.close = MagicMock()

    mock_lightrag = MagicMock()
    mock_lightrag.get_full_context.return_value = "ctx"

    mock_tg = MagicMock()
    mock_tg.send_review_card = AsyncMock(return_value=1)
    mock_tg.send_draft_for_edit = AsyncMock(return_value=2)
    mock_tg.mark_review_skipped = AsyncMock()
    mock_tg.mark_review_approved = AsyncMock()
    mock_tg.send_photo = AsyncMock()
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
        prepared_store=PreparedResponseStore(
            Path(settings.database_path).parent / "prepared"
        ),
        adapter_factory=lambda _s: mock_adapter,
        browser=MagicMock(),
    )
    return orch


@pytest.mark.asyncio
async def test_pipeline_scan_score_submit(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="999",
        url=project_full.url,
        title=project_full.title,
    )
    orch = _make_orchestrator(
        settings, previews=[preview], project_full=project_full, score=score
    )
    totals = await orch.run_scan_cycle()

    assert totals["new"] == 1
    orch.scorer.score.assert_called_once()
    orch.response_generator.generate.assert_called_once()

    repo = ProjectRepository(settings.database_path)
    assert repo.is_known("kwork", "kwork_dev_it", "999")
    state = repo.get_scan_state("kwork_dev_it")
    assert state is not None


@pytest.mark.asyncio
async def test_pipeline_skips_low_score(
    settings: Settings, project_full: ProjectFull
) -> None:
    low_score = GptScoreResult(
        score=4,
        fit=False,
        reason="no",
        matched_skills=[],
        risks=[],
        suggested_project_type="X",
        competition_level="high",
        recommendation="пропустить",
    )
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="888",
        url="https://kwork.ru/projects/888",
        title="Low",
    )
    orch = _make_orchestrator(
        settings, previews=[preview], project_full=project_full, score=low_score
    )
    await orch.run_scan_cycle()
    orch.response_generator.generate.assert_not_called()


@pytest.mark.asyncio
async def test_handle_approved_requires_approval_flag(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    settings.require_telegram_approval = True
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="777",
        url=project_full.url,
        title="x",
    )
    orch = _make_orchestrator(
        settings, previews=[preview], project_full=project_full, score=score
    )

    pending = PendingOffer(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="777",
        url=project_full.url,
        title=project_full.title,
        project=project_full,
        score=score,
        created_at=datetime.now(timezone.utc),
        status="pending",
    )
    await orch.handle_approved("kwork", "kwork_dev_it", "777", pending)
    orch.response_generator.generate.assert_not_called()


@pytest.mark.asyncio
async def test_handle_approved_unknown_source_notifies(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    settings.require_telegram_approval = False
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="666",
        url=project_full.url,
        title="x",
    )
    orch = _make_orchestrator(
        settings, previews=[preview], project_full=project_full, score=score
    )

    offer = PendingOffer(
        platform="kwork",
        source_key="unknown_source",
        project_id="666",
        url=project_full.url,
        title=project_full.title,
        project=project_full,
        score=score,
        created_at=datetime.now(timezone.utc),
        status="approved",
        approved_at=datetime.now(timezone.utc),
        response_text="text",
    )
    await orch.handle_approved("kwork", "unknown_source", "666", offer)

    orch.review_service.tg_bot.notify.assert_called_once()
    assert "unknown_source" in orch.review_service.tg_bot.notify.call_args[0][0]
