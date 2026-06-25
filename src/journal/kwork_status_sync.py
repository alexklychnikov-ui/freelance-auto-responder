from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.adapters.kwork_offers import (
    KworkMyOfferStatus,
    fetch_my_offer_statuses,
    journal_status_for_offer,
)
from src.config import Settings, get_settings
from src.journal.writer import JournalWriter

if TYPE_CHECKING:
    from src.browser.base import BrowserClient


@dataclass
class KworkStatusSyncResult:
    updated: int = 0
    matched: int = 0
    appended: int = 0
    skipped: int = 0
    error: str | None = None


def sync_journal_from_kwork_offers(
    journal_path: str | Path,
    *,
    settings: Settings | None = None,
    offers: dict[str, KworkMyOfferStatus] | None = None,
    project_types: dict[str, str] | None = None,
) -> KworkStatusSyncResult:
    settings = settings or get_settings()
    writer = JournalWriter(journal_path)
    journal_ids = writer.project_ids_in_journal()

    if offers is None:
        # Ленивый импорт: sync_journal должен работать без обязательного mcp-пакета,
        # если офферы не нужны или если браузерная часть отключена.
        try:
            from src.browser.factory import close_browser_client, get_browser_client
        except ModuleNotFoundError as exc:
            return KworkStatusSyncResult(
                error=f"browser_dep_missing: {exc.name}"
            )

        browser: BrowserClient = get_browser_client(settings)
        try:
            offers = fetch_my_offer_statuses(browser)
        except Exception as exc:
            return KworkStatusSyncResult(error=str(exc))
        finally:
            close_browser_client(browser)

    result = KworkStatusSyncResult()
    types = project_types or {}
    for project_id, offer in offers.items():
        status, communication = journal_status_for_offer(offer)
        title = offer.title or f"Kwork project {project_id}"
        ptype = types.get(project_id)
        if project_id in journal_ids:
            result.matched += 1
            if writer.update_status_by_project_id(
                project_id,
                status=status,
                result=communication,
            ):
                result.updated += 1
            writer.repair_row_by_project_id(
                project_id,
                title=title,
                project_type=ptype,
            )
            continue

        writer.append_kwork_offer_status(
            project_id=project_id,
            title=title,
            status=status,
            result=communication,
            project_type=ptype,
        )
        journal_ids.add(project_id)
        result.appended += 1

    writer.repair_all_rows(
        titles={pid: (offer.title or "") for pid, offer in offers.items()},
        project_types=types,
    )
    return result
