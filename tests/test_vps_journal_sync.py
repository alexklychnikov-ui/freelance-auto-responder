from __future__ import annotations

from datetime import datetime, timezone

from src.journal.kwork_status_sync import KworkStatusSyncResult
from src.journal.vps_sync import sync_journal_on_vps
from src.journal.writer import JournalWriter
from src.models import GptScoreResult, ProjectFull
from src.responses.prepared_store import PreparedResponse, PreparedResponseStore


def _prepared(project_id: str) -> PreparedResponse:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id=project_id,
        url=f"https://kwork.ru/projects/{project_id}",
        title=f"Project {project_id}",
        full_description="desc",
    )
    score = GptScoreResult(
        score=8,
        fit=True,
        reason="ok",
        matched_skills=["Python"],
        risks=[],
        suggested_project_type="Telegram-бот",
        competition_level="low",
        recommendation="откликаться",
    )
    return PreparedResponse(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id=project_id,
        url=project.url,
        title=project.title,
        project=project,
        score=score,
        response_text="text",
        price="1000",
        delivery_days=7,
        prepared_at=datetime.now(timezone.utc),
        journal_confirmed=True,
    )


def test_sync_journal_on_vps_appends_and_marks_exported(tmp_path, monkeypatch) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")
    item = _prepared("3204427")
    store.save(item)

    def _fake_offers(*args, **kwargs):
        return KworkStatusSyncResult(updated=1, matched=1, appended=2)

    monkeypatch.setattr(
        "src.journal.vps_sync.sync_journal_from_kwork_offers",
        _fake_offers,
    )
    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    assert result.appended_prepared == 1
    assert result.offers_updated == 1
    assert result.offers_appended == 2
    saved = store.load("kwork", "kwork_dev_it", "3204427")
    assert saved is not None and saved.journal_exported is True
