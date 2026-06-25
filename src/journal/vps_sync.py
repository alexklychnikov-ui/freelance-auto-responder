from __future__ import annotations

from dataclasses import dataclass

from src.config import Settings
from src.journal.kwork_status_sync import sync_journal_from_kwork_offers
from src.journal.writer import JournalWriter, format_offer_notes
from src.responses.prepared_store import PreparedResponseStore


@dataclass
class VpsJournalSyncResult:
    appended_prepared: int = 0
    updated_notes: int = 0
    offers_updated: int = 0
    offers_appended: int = 0
    offers_matched: int = 0
    offers_error: str | None = None


def sync_journal_on_vps(
    *,
    settings: Settings,
    writer: JournalWriter,
    prepared_store: PreparedResponseStore,
) -> VpsJournalSyncResult:
    result = VpsJournalSyncResult()
    existing_ids = writer.project_ids_in_journal()

    for item in prepared_store.list_all():
        if not item.journal_confirmed:
            continue

        notes = format_offer_notes(
            item.title,
            price=item.price,
            delivery_days=item.delivery_days,
        )
        in_journal = item.project_id in existing_ids

        if item.journal_exported and in_journal:
            if writer.update_notes_by_project_id(item.project_id, notes):
                result.updated_notes += 1
            continue
        if (not item.journal_exported) and in_journal:
            item.journal_exported = True
            prepared_store.save(item)
            if writer.update_notes_by_project_id(item.project_id, notes):
                result.updated_notes += 1
            continue

        writer.append_prepared(
            item.project,
            item.score,
            item.response_text,
            price=item.price,
            delivery_days=item.delivery_days,
        )
        item.journal_exported = True
        prepared_store.save(item)
        existing_ids.add(item.project_id)
        result.appended_prepared += 1

    offers = sync_journal_from_kwork_offers(
        settings.response_journal,
        settings=settings,
        project_types={
            item.project_id: item.score.suggested_project_type
            for item in prepared_store.list_all()
            if item.score.suggested_project_type
        },
    )
    result.offers_error = offers.error
    result.offers_updated = offers.updated
    result.offers_appended = offers.appended
    result.offers_matched = offers.matched
    return result
