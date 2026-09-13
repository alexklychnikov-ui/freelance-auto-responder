from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.evidence.collector import project_content_hash
from src.evidence.models import EvidenceBundle, EvidenceFact
from src.models import GptScoreResult, OfferTerms, PendingOffer, ProjectFull
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
        kwork_inbox_mirror_enabled=False,
        kwork_inbox_seen_db=str(tmp_path / "kwork_inbox_seen.db"),
        evidence_research_enabled=False,
        _env_file=None,
    )


@pytest.fixture
def project_full() -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3252339",
        url="https://kwork.ru/projects/3252339",
        title="Разработать сайт",
        full_description=(
            "Нужен аналог https://торги-россии.рф/ данные с https://torgi.gov.ru"
        ),
        desired_budget="5000",
        offers_count=1,
    )


@pytest.fixture
def score() -> GptScoreResult:
    return GptScoreResult(
        score=9,
        fit=True,
        reason="match",
        matched_skills=["Python"],
        risks=[],
        suggested_project_type="Сайт",
        competition_level="low",
        recommendation="откликаться",
    )


def _make_orch(
    settings: Settings,
    project_full: ProjectFull,
    score: GptScoreResult,
    *,
    evidence_service: MagicMock | None = None,
) -> PipelineOrchestrator:
    from src.journal.writer import JournalWriter
    from src.responses.prepared_store import PreparedResponseStore
    from src.telegram_bot.pending_store import PendingStore
    from src.telegram_bot.review_service import ReviewService

    repo = ProjectRepository(settings.database_path)
    mock_adapter = MagicMock()
    mock_adapter.read_full.return_value = project_full

    mock_estimator = MagicMock()
    mock_estimator.estimate.return_value = OfferTerms(
        price_rub=5000, delivery_days=14, plan_summary=""
    )
    mock_estimator.estimate_market_cost.return_value = 5000
    mock_estimator.close = MagicMock()

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
    mock_tg.send_offer_link = AsyncMock(return_value=2)
    mock_tg.notify = AsyncMock()
    mock_tg.close = AsyncMock()

    store = PendingStore(base_dir=Path(settings.database_path).parent / "pending")
    review = ReviewService(settings, store, mock_tg, repo)

    return PipelineOrchestrator(
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
        offer_estimator=mock_estimator,
        evidence_service=evidence_service,
        adapter_factory=lambda _s, _b=None: mock_adapter,
        browser=MagicMock(),
    )


def _offer(project_full: ProjectFull, score: GptScoreResult) -> PendingOffer:
    return PendingOffer(
        platform=project_full.platform,
        source_key=project_full.source_key,
        project_id=project_full.project_id,
        url=project_full.url,
        title=project_full.title,
        project=project_full,
        score=score,
        created_at=datetime.now(timezone.utc),
        status="approved",
    )


@pytest.mark.asyncio
async def test_generate_skips_evidence_when_disabled(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    mock_ev = MagicMock()
    mock_ev.collect = MagicMock(
        side_effect=AssertionError("collect must not run when disabled")
    )
    orch = _make_orch(settings, project_full, score, evidence_service=mock_ev)
    offer = _offer(project_full, score)
    orch.review_service.store.save(offer)

    text = await orch._generate_response_text(offer)
    assert text
    mock_ev.collect.assert_not_called()
    orch.response_generator.generate.assert_called_once()
    call_kwargs = orch.response_generator.generate.call_args.kwargs
    assert call_kwargs.get("evidence") is not None
    assert call_kwargs["evidence"].status == "not_required"
    assert offer.evidence is not None
    assert offer.evidence.status == "not_required"
    assert offer.evidence.required is False
    assert offer.evidence.sources == []
    assert offer.evidence.facts == []


@pytest.mark.asyncio
async def test_ensure_evidence_disabled_clears_stale_and_prepared(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    """When flag is off, prior facts must not leak into prepared JSON."""
    settings.evidence_research_enabled = False
    stale = EvidenceBundle(
        status="complete",
        required=True,
        project_hash=project_content_hash(project_full),
        facts=[
            EvidenceFact(
                id="stale",
                source_id="s1",
                claim="старый факт",
                quote="184729",
                eligible_for_response=True,
            )
        ],
    )
    mock_ev = MagicMock()
    mock_ev.collect = MagicMock(
        side_effect=AssertionError("collect must not run when disabled")
    )
    orch = _make_orch(settings, project_full, score, evidence_service=mock_ev)
    offer = _offer(project_full, score)
    offer.evidence = stale
    orch.review_service.store.save(offer)

    text = await orch._generate_response_text(offer)
    assert text
    mock_ev.collect.assert_not_called()
    assert offer.evidence is not None
    assert offer.evidence.status == "not_required"
    assert offer.evidence.required is False
    assert offer.evidence.facts == []
    assert offer.evidence.sources == []

    loaded = orch.review_service.store.load(
        offer.platform, offer.source_key, offer.project_id
    )
    assert loaded is not None
    assert loaded.evidence is not None
    assert loaded.evidence.status == "not_required"
    assert loaded.evidence.facts == []

    await orch._save_prepared_response(offer, "Prepared disabled", "5000", 14)
    prepared = orch.prepared_store.load(
        offer.platform, offer.source_key, offer.project_id
    )
    assert prepared is not None
    assert prepared.evidence is not None
    assert prepared.evidence.status == "not_required"
    assert prepared.evidence.required is False
    assert prepared.evidence.facts == []
    assert prepared.evidence.sources == []


@pytest.mark.asyncio
async def test_ensure_evidence_collects_and_stores_when_enabled(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    settings.evidence_research_enabled = True
    bundle = EvidenceBundle(
        status="complete",
        required=True,
        project_hash=project_content_hash(project_full),
        facts=[
            EvidenceFact(
                id="f1",
                source_id="s1",
                claim="лот",
                quote="184729",
                eligible_for_response=True,
            )
        ],
    )
    mock_ev = MagicMock()
    mock_ev.collect = MagicMock(return_value=bundle)

    orch = _make_orch(settings, project_full, score, evidence_service=mock_ev)
    offer = _offer(project_full, score)
    orch.review_service.store.save(offer)

    text = await orch._generate_response_text(offer)
    assert text
    mock_ev.collect.assert_called_once()
    assert offer.evidence is not None
    assert offer.evidence.status == "complete"
    assert len(offer.evidence.facts) == 1

    loaded = orch.review_service.store.load(
        offer.platform, offer.source_key, offer.project_id
    )
    assert loaded is not None
    assert loaded.evidence is not None
    assert loaded.evidence.project_hash == project_content_hash(project_full)

    call_kwargs = orch.response_generator.generate.call_args.kwargs
    assert call_kwargs["evidence"] is bundle


@pytest.mark.asyncio
async def test_ensure_evidence_reuses_cache_on_regenerate(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    settings.evidence_research_enabled = True
    cached = EvidenceBundle(
        status="partial",
        required=True,
        project_hash=project_content_hash(project_full),
        warnings=["cached"],
    )
    mock_ev = MagicMock()
    mock_ev.collect = MagicMock(
        side_effect=AssertionError("must reuse cached evidence")
    )

    orch = _make_orch(settings, project_full, score, evidence_service=mock_ev)
    offer = _offer(project_full, score)
    offer.evidence = cached
    offer.response_text = None
    orch.review_service.store.save(offer)

    bundle = await orch.ensure_evidence(offer)
    assert bundle is cached
    mock_ev.collect.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_evidence_fail_open(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    settings.evidence_research_enabled = True
    mock_ev = MagicMock()
    mock_ev.collect = MagicMock(side_effect=RuntimeError("boom"))

    orch = _make_orch(settings, project_full, score, evidence_service=mock_ev)
    offer = _offer(project_full, score)
    orch.review_service.store.save(offer)

    text = await orch._generate_response_text(offer)
    assert text
    assert offer.evidence is not None
    assert offer.evidence.status == "failed"
    assert any("evidence_collect_error" in w for w in offer.evidence.warnings)
    orch.response_generator.generate.assert_called_once()


@pytest.mark.asyncio
async def test_save_prepared_response_includes_evidence(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    settings.evidence_research_enabled = True
    bundle = EvidenceBundle(
        status="complete",
        required=True,
        project_hash=project_content_hash(project_full),
        facts=[
            EvidenceFact(
                id="f1",
                source_id="s1",
                claim="лот",
                quote="184729",
                eligible_for_response=True,
            )
        ],
    )
    mock_ev = MagicMock()
    mock_ev.collect = MagicMock(return_value=bundle)

    orch = _make_orch(settings, project_full, score, evidence_service=mock_ev)
    offer = _offer(project_full, score)
    orch.review_service.store.save(offer)

    await orch._generate_response_text(offer)
    await orch._save_prepared_response(offer, "Prepared with evidence", "5000", 14)

    loaded = orch.prepared_store.load(
        offer.platform, offer.source_key, offer.project_id
    )
    assert loaded is not None
    assert loaded.evidence is not None
    assert loaded.evidence.status == "complete"
    assert len(loaded.evidence.facts) == 1
    assert loaded.evidence.facts[0].claim == "лот"


@pytest.mark.asyncio
async def test_generate_notifies_evidence_summary(
    settings: Settings, project_full: ProjectFull, score: GptScoreResult
) -> None:
    settings.evidence_research_enabled = True
    bundle = EvidenceBundle(
        status="partial",
        required=True,
        project_hash=project_content_hash(project_full),
        warnings=["mirror down"],
    )
    mock_ev = MagicMock()
    mock_ev.collect = MagicMock(return_value=bundle)

    orch = _make_orch(settings, project_full, score, evidence_service=mock_ev)
    offer = _offer(project_full, score)
    orch.review_service.store.save(offer)

    notify = AsyncMock()
    await orch._generate_response_text(offer, notify=notify)

    summary_calls = [
        c.args[0]
        for c in notify.await_args_list
        if c.args and "Evidence:" in str(c.args[0])
    ]
    assert summary_calls
    assert "sources" in summary_calls[-1] or "facts" in summary_calls[-1]
    assert "mirror down" in summary_calls[-1]
