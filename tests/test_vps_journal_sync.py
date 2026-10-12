from __future__ import annotations

from datetime import datetime, timezone

from openpyxl import load_workbook

from src.adapters.kwork import OfferFormSnapshot
from src.adapters.kwork_offers import KworkMyOfferStatus, KworkOfferComment
from src.journal.kwork_status_sync import (
    KworkStatusSyncResult,
    sync_journal_from_kwork_offers,
)
from src.journal.vps_sync import (
    load_last_sync_at,
    save_last_sync_at,
    sync_journal_on_vps,
)
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


def _prepared_telegram_tz(project_id: str, *, exported: bool = False) -> PreparedResponse:
    project = ProjectFull(
        platform="telegram",
        source_key="tz_manual",
        project_id=project_id,
        url="",
        title=f"TZ {project_id}",
        full_description="tz desc",
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
        platform="telegram",
        source_key="tz_manual",
        project_id=project_id,
        url="",
        title=project.title,
        project=project,
        score=score,
        response_text="AI TZ DRAFT TEXT",
        price="1000",
        delivery_days=7,
        prepared_at=datetime.now(timezone.utc),
        journal_confirmed=True,
        journal_exported=exported,
    )


def test_sync_skips_reappend_when_exported_id_unfindable(tmp_path, monkeypatch) -> None:
    """Confirmed+exported item with empty URL must not append again on second sync."""
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")
    item = _prepared_telegram_tz("tz_1785490763952", exported=False)
    store.save(item)

    monkeypatch.setattr(
        "src.browser.factory.get_browser_client",
        lambda settings: (_ for _ in ()).throw(ModuleNotFoundError("mcp")),
    )
    monkeypatch.setattr(
        "src.journal.vps_sync.sync_journal_from_kwork_offers",
        lambda *a, **k: KworkStatusSyncResult(),
    )
    settings = type("S", (), {"response_journal": str(journal_path)})()

    first = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)
    assert first.appended_prepared == 1
    wb = load_workbook(journal_path)
    rows_after_first = wb.active.max_row
    wb.close()

    second = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)
    assert second.appended_prepared == 0
    wb = load_workbook(journal_path)
    assert wb.active.max_row == rows_after_first
    wb.close()
    saved = store.load("telegram", "tz_manual", "tz_1785490763952")
    assert saved is not None and saved.journal_exported is True


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
    # prepared_at is "now" → still in since-window after this last_sync
    save_last_sync_at(journal_path, datetime(2020, 1, 1, tzinfo=timezone.utc))

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


def test_sync_fills_empty_i_from_comments_without_confirm(
    tmp_path, monkeypatch
) -> None:
    """Status-synced row with empty I gets platform text even if prepared is absent."""
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")

    writer.append_kwork_offer_status(
        project_id="3233067",
        title="Project 3233067",
        status="Отказ",
        result="Отказ",
        project_type="Telegram-бот",
    )
    assert writer.response_is_empty_by_project_id("3233067") is True

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
        lambda *a, **k: KworkStatusSyncResult(appended=1, matched=1),
    )
    monkeypatch.setattr(
        "src.journal.vps_sync.fetch_my_offer_comment_details",
        lambda browser, navigate=False: {
            "3233067": KworkOfferComment(
                project_id="3233067",
                comment="LIVE OFFER ON KWORK " + ("y" * 40),
                price="5500",
                delivery_days=5,
            )
        },
    )

    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    assert result.updated_notes >= 1
    assert result.appended_prepared == 0
    expected = format_response_payload(
        "LIVE OFFER ON KWORK " + ("y" * 40),
        price="5500",
        delivery_days=5,
    )
    wb = load_workbook(journal_path)
    cell = str(wb.active.cell(row=2, column=9).value or "")
    wb.close()
    assert cell == expected
    assert "LIVE OFFER ON KWORK" in cell
    assert "5500" in cell
    assert "5 дн." in cell
    assert writer.response_is_empty_by_project_id("3233067") is False


def test_sync_fills_empty_i_when_prepared_unconfirmed(
    tmp_path, monkeypatch
) -> None:
    """Unconfirmed prepared must not block empty-I fill from comments."""
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")
    item = _prepared("3233067")
    item.journal_confirmed = False
    store.save(item)

    writer.append_kwork_offer_status(
        project_id="3233067",
        title="Project 3233067",
        status="Отказ",
        result="Отказ",
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
            "3233067": KworkOfferComment(
                project_id="3233067",
                comment="PLATFORM TEXT FROM COMMENTS",
                price="8000",
                delivery_days=10,
            )
        },
    )

    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    assert result.updated_notes >= 1
    assert result.appended_prepared == 0
    wb = load_workbook(journal_path)
    cell = str(wb.active.cell(row=2, column=9).value or "")
    wb.close()
    assert "PLATFORM TEXT FROM COMMENTS" in cell
    assert "8000" in cell
    assert "AI DRAFT TEXT" not in cell
    saved = store.load("kwork", "kwork_dev_it", "3233067")
    assert saved is not None and saved.journal_confirmed is False


def test_empty_fill_path_does_not_overwrite_nonempty_i(
    tmp_path, monkeypatch
) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")

    writer.append_kwork_offer_status(
        project_id="3233067",
        title="Project 3233067",
        status="Отказ",
        result="Отказ",
    )
    keep = format_response_payload(
        "ALREADY FILLED RESPONSE", price="1000", delivery_days=3
    )
    assert writer.update_response_by_project_id("3233067", keep)

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
            "3233067": KworkOfferComment(
                project_id="3233067",
                comment="SHOULD NOT REPLACE EXISTING",
                price="99999",
                delivery_days=99,
            )
        },
    )

    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    # Empty-fill path must not touch I; no confirmed prepared either.
    assert result.updated_notes == 0
    wb = load_workbook(journal_path)
    cell = str(wb.active.cell(row=2, column=9).value or "")
    wb.close()
    assert cell == keep
    assert "SHOULD NOT REPLACE EXISTING" not in cell
    assert "99999" not in cell


def test_update_response_by_project_id_if_empty_unit(tmp_path) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    writer.append_kwork_offer_status(
        project_id="111",
        title="t",
        status="Отказ",
        result="Отказ",
    )
    first = format_response_payload("first", price="1", delivery_days=1)
    assert writer.update_response_by_project_id_if_empty("111", first) is True
    assert writer.update_response_by_project_id_if_empty(
        "111", format_response_payload("second", price="2", delivery_days=2)
    ) is False
    wb = load_workbook(journal_path)
    assert str(wb.active.cell(row=2, column=9).value or "") == first
    wb.close()


def test_exported_in_journal_skips_platform_fetch(tmp_path, monkeypatch) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")
    item = _prepared("3217871", exported=True)
    item.prepared_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save(item)
    writer.append_prepared(
        item.project,
        item.score,
        "KEEP ME",
        price="1000",
        delivery_days=7,
    )
    save_last_sync_at(
        journal_path, datetime(2026, 6, 1, tzinfo=timezone.utc)
    )

    snap_calls: list[str] = []

    def _track_snap(*args, **kwargs):
        snap_calls.append(args[1] if len(args) > 1 else kwargs.get("project_id", ""))
        raise AssertionError("_snap_for_project must not be called")

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
    monkeypatch.setattr("src.journal.vps_sync._snap_for_project", _track_snap)

    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    assert snap_calls == []
    assert result.appended_prepared == 0
    assert result.updated_notes == 0
    wb = load_workbook(journal_path)
    cell = str(wb.active.cell(row=2, column=9).value or "")
    wb.close()
    assert "KEEP ME" in cell
    assert load_last_sync_at(journal_path) is not None


def test_new_confirmed_since_last_sync_appends(tmp_path, monkeypatch) -> None:
    journal_path = tmp_path / "journal.xlsx"
    writer = JournalWriter(journal_path)
    JournalWriter.create_template_copy(journal_path)
    store = PreparedResponseStore(tmp_path / "prepared")
    save_last_sync_at(
        journal_path, datetime(2026, 6, 1, tzinfo=timezone.utc)
    )

    old = _prepared("3201111", exported=True)
    old.prepared_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save(old)
    writer.append_prepared(
        old.project, old.score, "OLD", price="1", delivery_days=1
    )

    fresh = _prepared("3202222", exported=False)
    fresh.prepared_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    store.save(fresh)

    snap_calls: list[str] = []

    def _fake_snap(browser, project_id, comments, *, platform, settings=None):
        snap_calls.append(project_id)
        return None

    monkeypatch.setattr(
        "src.browser.factory.get_browser_client",
        lambda settings: (_ for _ in ()).throw(ModuleNotFoundError("mcp")),
    )
    monkeypatch.setattr(
        "src.journal.vps_sync.sync_journal_from_kwork_offers",
        lambda *a, **k: KworkStatusSyncResult(),
    )
    monkeypatch.setattr("src.journal.vps_sync._snap_for_project", _fake_snap)

    settings = type("S", (), {"response_journal": str(journal_path)})()
    result = sync_journal_on_vps(settings=settings, writer=writer, prepared_store=store)

    assert result.appended_prepared == 1
    assert snap_calls == ["3202222"]
    assert "3201111" not in snap_calls
    saved = store.load("kwork", "kwork_dev_it", "3202222")
    assert saved is not None and saved.journal_exported is True
    assert "3202222" in writer.project_ids_in_journal()


def test_kwork_offers_append_only_skips_status_updates(tmp_path, monkeypatch) -> None:
    journal_path = tmp_path / "journal.xlsx"
    JournalWriter.create_template_copy(journal_path)
    writer = JournalWriter(journal_path)
    writer.append_kwork_offer_status(
        project_id="3204427",
        title="Existing",
        status="Отправлен",
        result="Отправлен",
    )

    offers = {
        "3204427": KworkMyOfferStatus(
            project_id="3204427",
            title="Existing updated title",
            informers=("Покупатель сделал 1 заказ",),
        ),
        "9990001": KworkMyOfferStatus(
            project_id="9990001",
            title="Brand new offer",
            informers=("Покупатель пока не сделал заказ",),
        ),
    }

    update_calls: list[str] = []
    repair_row_calls: list[str] = []
    repair_all_calls: list[int] = []

    real_update = JournalWriter.update_status_by_project_id
    real_repair_row = JournalWriter.repair_row_by_project_id
    real_repair_all = JournalWriter.repair_all_rows

    def _spy_update(self, project_id, *args, **kwargs):
        update_calls.append(project_id)
        return real_update(self, project_id, *args, **kwargs)

    def _spy_repair_row(self, project_id, *args, **kwargs):
        repair_row_calls.append(project_id)
        return real_repair_row(self, project_id, *args, **kwargs)

    def _spy_repair_all(self, *args, **kwargs):
        repair_all_calls.append(1)
        return real_repair_all(self, *args, **kwargs)

    monkeypatch.setattr(JournalWriter, "update_status_by_project_id", _spy_update)
    monkeypatch.setattr(JournalWriter, "repair_row_by_project_id", _spy_repair_row)
    monkeypatch.setattr(JournalWriter, "repair_all_rows", _spy_repair_all)

    result = sync_journal_from_kwork_offers(
        journal_path, offers=offers, update_existing=False
    )

    assert result.error is None
    assert result.matched == 1
    assert result.appended == 1
    assert result.updated == 0
    assert result.skipped == 1
    assert update_calls == []
    assert repair_row_calls == []
    assert repair_all_calls == []
    assert "9990001" in writer.project_ids_in_journal()

    wb = load_workbook(journal_path)
    assert wb.active.cell(row=2, column=6).value == "Отправлен"
    wb.close()
