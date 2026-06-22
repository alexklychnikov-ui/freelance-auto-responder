from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.models import GptScoreResult, PendingOffer, ProjectFull
from src.telegram_bot.pending_store import PendingStore


@pytest.fixture
def store(tmp_path: Path) -> PendingStore:
    return PendingStore(tmp_path / "pending_offers")


@pytest.fixture
def offer() -> PendingOffer:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="42",
        url="https://kwork.ru/projects/42",
        title="Test",
        full_description="desc",
    )
    score = GptScoreResult(
        score=8,
        fit=True,
        reason="ok",
        matched_skills=[],
        risks=[],
        suggested_project_type="Bot",
        competition_level="low",
        recommendation="откликаться",
    )
    return PendingOffer(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="42",
        url=project.url,
        title=project.title,
        project=project,
        score=score,
        created_at=datetime.now(timezone.utc),
        status="pending",
    )


def test_pending_store_roundtrip(store: PendingStore, offer: PendingOffer) -> None:
    store.save(offer)
    loaded = store.load("kwork", "kwork_dev_it", "42")
    assert loaded is not None
    assert loaded.title == "Test"
    assert loaded.status == "pending"


def test_pending_store_list_pending(store: PendingStore, offer: PendingOffer) -> None:
    store.save(offer)
    offer.status = "rejected"
    store.save(offer)
    assert store.list_pending() == []

    offer.status = "pending"
    store.save(offer)
    assert len(store.list_pending()) == 1
