from __future__ import annotations

from datetime import datetime, timezone

from openpyxl import load_workbook

from src.adapters.kwork import OfferFormSnapshot
from src.adapters.kwork_offers import KworkOfferComment
from src.journal.kwork_status_sync import KworkStatusSyncResult
from src.journal.vps_sync import sync_journal_on_vps
from src.journal.writer import JournalWriter, format_response_payload
from src.models import GptScoreResult, ProjectFull
from src.responses.prepared_store import PreparedResponse, PreparedResponseStore


def _prepared(project_id: str, *, exported: bool = False) -> PreparedResponse:
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
        response_text="AI DRAFT TEXT",
        price="1000",
        delivery_days=7,
        prepared_at=datetime.now(timezone.utc),
        journal_confirmed=True,
        journal_exported=exported,
    )


def test_sync_journal_on_vps_appends_and_marks_exported(tmp_path, monkeypatch) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")
    item = _prepared("3204427")
    store.save(item)

    def _fake_get_browser(settings):
        raise ModuleNotFoundError("mcp")

    monkeypatch.setattr(
        "src.browser.factory.get_browser_client",
        _fake_get_browser,
    )

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


def test_sync_does_not_overwrite_existing_i_with_ai_when_kwork_fails(
    tmp_path, monkeypatch
) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    item = _prepared("3217871", exported=True)
    store = PreparedResponseStore(tmp_path / "prepared")
    store.save(item)

    original = format_response_payload(
        "PLATFORM TEXT ALREADY IN JOURNAL", price="9000", delivery_days=10
    )
    writer.append_prepared(
        item.project,
        item.score,
        "PLATFORM TEXT ALREADY IN JOURNAL",
        price="9000",
        delivery_days=10,
    )
    writer.update_response_by_project_id("3217871", original)

    monkeypatch.setattr(
        "src.browser.factory.get_browser_client",
        lambda settings: object(),
    )
    monkeypatch.setattr(
        "src.browser.factory.close_browser_client",
        lambda client: None,
    )
    monkeypatch.setattr(
        "src.journal.vps_sync.sync_journal_from_kwork_offers",
        lambda *a, **k: KworkStatusSyncResult(),
    )
    monkeypatch.setattr(
        "src.journal.vps_sync.fetch_my_offer_comment_details",
        lambda browser, navigate=False: {},
    )
    monkeypatch.setattr(
        "src.journal.vps_sync._read_offer_text_from_new_offer_form",
        lambda browser, pid: OfferFormSnapshot(
            description="", ok=False, error="form_missing"
        ),
    )

    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    assert result.updated_notes == 0
    wb = load_workbook(journal_path)
    cell = str(wb.active.cell(row=2, column=9).value or "")
    assert "PLATFORM TEXT ALREADY IN JOURNAL" in cell
    assert "AI DRAFT TEXT" not in cell


def test_sync_updates_i_from_statedata_comments(tmp_path, monkeypatch) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    item = _prepared("3217871", exported=True)
    store = PreparedResponseStore(tmp_path / "prepared")
    store.save(item)
    writer.append_prepared(
        item.project,
        item.score,
        "AI DRAFT TEXT",
        price="1000",
        delivery_days=7,
    )

    monkeypatch.setattr(
        "src.browser.factory.get_browser_client",
        lambda settings: object(),
    )
    monkeypatch.setattr(
        "src.browser.factory.close_browser_client",
        lambda client: None,
    )
    monkeypatch.setattr(
        "src.journal.vps_sync.sync_journal_from_kwork_offers",
        lambda *a, **k: KworkStatusSyncResult(),
    )
    monkeypatch.setattr(
        "src.journal.vps_sync.fetch_my_offer_comment_details",
        lambda browser, navigate=False: {
            "3217871": KworkOfferComment(
                project_id="3217871",
                comment="REAL KWORK OFFER TEXT " + ("x" * 40),
                price="12000",
                delivery_days=14,
            )
        },
    )

    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    assert result.updated_notes >= 1
    wb = load_workbook(journal_path)
    cell = str(wb.active.cell(row=2, column=9).value or "")
    assert "REAL KWORK OFFER TEXT" in cell
    assert "AI DRAFT TEXT" not in cell
    assert "12000" in cell
