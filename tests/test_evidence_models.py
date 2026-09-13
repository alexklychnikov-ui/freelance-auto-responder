from __future__ import annotations

from datetime import datetime, timezone

from src.evidence.models import (
    EvidenceBundle,
    EvidenceFact,
    EvidenceInsight,
    EvidenceSource,
)
from src.models import GptScoreResult, PendingOffer, ProjectFull
from src.responses.prepared_store import PreparedResponse


def _sample_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="1",
        status="partial",
        required=True,
        project_hash="abc123",
        sources=[
            EvidenceSource(
                id="src1",
                kind="url",
                role="data_source",
                input_ref="https://example.com/lot",
                final_url="https://example.com/lot",
                title="Lot page",
                fetch_method="http",
                status="verified",
                http_status=200,
                content_type="text/html",
                retrieved_at=datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
                content_hash="deadbeef",
            )
        ],
        facts=[
            EvidenceFact(
                id="f1",
                source_id="src1",
                claim="Lot number present",
                quote="Лот № 3252339",
                anchors=["3252339"],
                relevance=0.9,
                verification="exact_quote",
                eligible_for_response=True,
            )
        ],
        insights=[
            EvidenceInsight(
                fact_ids=["f1"],
                finding="Need lot-specific parser",
                implementation_consequence="Scrape lot id from public page",
                kind="scope",
            )
        ],
        warnings=["secondary mirror unavailable"],
        collected_at=datetime(2026, 9, 13, 10, 1, tzinfo=timezone.utc),
    )


def _score() -> GptScoreResult:
    return GptScoreResult(
        score=8,
        fit=True,
        reason="ok",
        matched_skills=[],
        risks=[],
        suggested_project_type="Parser",
        competition_level="low",
        recommendation="откликаться",
    )


def _project() -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="42",
        url="https://kwork.ru/projects/42",
        title="Test",
        full_description="desc",
    )


def test_evidence_bundle_roundtrip() -> None:
    bundle = _sample_bundle()
    restored = EvidenceBundle.model_validate(bundle.model_dump(mode="json"))
    assert restored == bundle
    assert restored.sources[0].id == "src1"
    assert restored.facts[0].anchors == ["3252339"]
    assert restored.insights[0].kind == "scope"


def test_pending_offer_old_json_without_evidence() -> None:
    payload = {
        "platform": "kwork",
        "source_key": "kwork_dev_it",
        "project_id": "42",
        "url": "https://kwork.ru/projects/42",
        "title": "Test",
        "project": _project().model_dump(mode="json"),
        "score": _score().model_dump(mode="json"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    offer = PendingOffer.model_validate(payload)
    assert offer.evidence is None


def test_prepared_response_from_dict_without_evidence() -> None:
    project = _project()
    score = _score()
    data = {
        "platform": project.platform,
        "source_key": project.source_key,
        "project_id": project.project_id,
        "url": project.url,
        "title": project.title,
        "project": project.model_dump(mode="json"),
        "score": score.model_dump(mode="json"),
        "response_text": "Здравствуйте!",
        "price": "5000",
        "delivery_days": 7,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    item = PreparedResponse.from_dict(data)
    assert item.evidence is None
    assert item.to_dict()["evidence"] is None


def test_prepared_response_with_evidence_roundtrip() -> None:
    project = _project()
    score = _score()
    bundle = _sample_bundle()
    item = PreparedResponse(
        platform=project.platform,
        source_key=project.source_key,
        project_id=project.project_id,
        url=project.url,
        title=project.title,
        project=project,
        score=score,
        response_text="Здравствуйте! Лот 3252339",
        price="5000",
        evidence=bundle,
    )
    restored = PreparedResponse.from_dict(item.to_dict())
    assert restored.evidence is not None
    assert restored.evidence.status == "partial"
    assert restored.evidence.facts[0].quote == "Лот № 3252339"


def test_prepared_store_roundtrip_with_evidence(tmp_path) -> None:
    from src.responses.prepared_store import PreparedResponseStore

    project = _project()
    score = _score()
    bundle = _sample_bundle()
    store = PreparedResponseStore(tmp_path / "prepared")
    item = PreparedResponse(
        platform=project.platform,
        source_key=project.source_key,
        project_id=project.project_id,
        url=project.url,
        title=project.title,
        project=project,
        score=score,
        response_text="Здравствуйте! Лот 3252339",
        price="5000",
        evidence=bundle,
    )
    store.save(item)
    loaded = store.load(project.platform, project.source_key, project.project_id)
    assert loaded is not None
    assert loaded.evidence is not None
    assert loaded.evidence.status == "partial"
    assert len(loaded.evidence.sources) == 1
    assert loaded.evidence.facts[0].anchors == ["3252339"]


def test_evidence_summary_html_no_quotes() -> None:
    from src.evidence.summary import evidence_summary_html

    summary = evidence_summary_html(_sample_bundle())
    assert "Evidence: 1 sources, 1 facts" in summary
    assert "status=partial" in summary
    assert "Лот № 3252339" not in summary
    assert "<html" not in summary.lower()
    assert "secondary mirror unavailable" in summary
