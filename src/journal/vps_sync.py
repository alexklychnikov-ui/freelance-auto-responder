from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.adapters.kwork import OfferFormSnapshot, _read_offer_text_from_new_offer_form
from src.adapters.kwork_offers import KworkOfferComment, fetch_my_offer_comment_details
from src.config import Settings
from src.journal.kwork_status_sync import sync_journal_from_kwork_offers
from src.journal.writer import JournalWriter, format_response_payload
from src.responses.prepared_store import PreparedResponseStore

if TYPE_CHECKING:
    from src.browser.base import BrowserClient

logger = logging.getLogger(__name__)


@dataclass
class VpsJournalSyncResult:
    appended_prepared: int = 0
    updated_notes: int = 0
    offers_updated: int = 0
    offers_appended: int = 0
    offers_matched: int = 0
    offers_error: str | None = None


def _platform_response_fields(
    snap: OfferFormSnapshot | None,
    *,
    fallback_text: str,
    fallback_price: str | None,
    fallback_days: int | None,
) -> tuple[str, str | None, int | None, bool]:
    """Return (text, price, days, used_platform)."""
    if snap is None or not snap.ok:
        return fallback_text, fallback_price, fallback_days, False
    text = (snap.description or "").strip()
    if not text:
        return fallback_text, fallback_price, fallback_days, False
    price = snap.price or fallback_price
    days = snap.delivery_days if snap.delivery_days is not None else fallback_days
    return text, price, days, True


def _snap_from_comments(
    comments: dict[str, KworkOfferComment], project_id: str
) -> OfferFormSnapshot | None:
    hit = comments.get(project_id)
    if hit is None or not hit.comment.strip():
        return None
    return OfferFormSnapshot(
        description=hit.comment.strip(),
        price=hit.price,
        delivery_days=hit.delivery_days,
        ok=True,
    )


def _snap_for_project(
    browser: BrowserClient | None,
    project_id: str,
    comments: dict[str, KworkOfferComment],
    *,
    platform: str,
    settings: Settings | None = None,
) -> OfferFormSnapshot | None:
    if platform == "yandex_uslugi":
        if settings is None:
            return None
        from src.adapters.yandex_uslugi import (
            read_submitted_offer_text as read_yandex_submitted,
        )

        storage = (settings.yandex_storage_state or "").strip() or None
        ybrowser = None
        try:
            from src.browser.factory import close_browser_client, get_browser_client

            ybrowser = get_browser_client(settings, storage_state_path=storage)
            ysnap = read_yandex_submitted(ybrowser, project_id)
            if not ysnap.ok:
                return OfferFormSnapshot(
                    description="", ok=False, error=ysnap.error or "yandex_read_failed"
                )
            return OfferFormSnapshot(
                description=ysnap.description,
                price=ysnap.price,
                ok=True,
            )
        except Exception as exc:
            logger.warning(
                "yandex_submitted_read_failed project_id=%s err=%s", project_id, exc
            )
            return OfferFormSnapshot(
                description="", ok=False, error=f"read_exception: {exc}"
            )
        finally:
            if ybrowser is not None:
                try:
                    from src.browser.factory import close_browser_client

                    close_browser_client(ybrowser)
                except Exception:
                    pass

    if platform == "flru":
        if settings is None:
            return None
        from src.adapters.flru import (
            read_submitted_offer_text as read_flru_submitted,
        )

        storage = (settings.flru_storage_state or "").strip() or None
        fbrowser = None
        try:
            from src.browser.factory import close_browser_client, get_browser_client

            fbrowser = get_browser_client(settings, storage_state_path=storage)
            fsnap = read_flru_submitted(fbrowser, project_id)
            if not fsnap.ok:
                return OfferFormSnapshot(
                    description="", ok=False, error=fsnap.error or "flru_read_failed"
                )
            return OfferFormSnapshot(
                description=fsnap.description,
                price=fsnap.price,
                delivery_days=fsnap.delivery_days,
                ok=True,
            )
        except Exception as exc:
            logger.warning(
                "flru_submitted_read_failed project_id=%s err=%s", project_id, exc
            )
            return OfferFormSnapshot(
                description="", ok=False, error=f"read_exception: {exc}"
            )
        finally:
            if fbrowser is not None:
                try:
                    from src.browser.factory import close_browser_client

                    close_browser_client(fbrowser)
                except Exception:
                    pass

    snap = _snap_from_comments(comments, project_id)
    if snap is not None:
        return snap
    if browser is None or platform != "kwork":
        return None
    try:
        return _read_offer_text_from_new_offer_form(browser, project_id)
    except Exception as exc:
        logger.warning(
            "kwork_offer_form_fallback_failed project_id=%s err=%s", project_id, exc
        )
        return OfferFormSnapshot(
            description="", ok=False, error=f"read_exception: {exc}"
        )


def sync_journal_on_vps(
    *,
    settings: Settings,
    writer: JournalWriter,
    prepared_store: PreparedResponseStore,
) -> VpsJournalSyncResult:
    result = VpsJournalSyncResult()
    writer.normalize_layout()
    existing_ids = writer.project_ids_in_journal()

    browser: BrowserClient | None = None
    try:
        from src.browser.factory import close_browser_client, get_browser_client

        browser = get_browser_client(settings)
    except ModuleNotFoundError as exc:
        logger.warning("journal_sync_browser_unavailable: %s", exc)
        browser = None
    except Exception as exc:
        logger.warning("journal_sync_browser_open_failed: %s", exc)
        browser = None

    try:
        offers = sync_journal_from_kwork_offers(
            settings.response_journal,
            settings=settings,
            browser=browser,
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
        existing_ids = writer.project_ids_in_journal()

        comments: dict[str, KworkOfferComment] = {}
        if browser is not None:
            try:
                # Status sync already opened /offers — reuse page when possible.
                comments = fetch_my_offer_comment_details(browser, navigate=False)
            except Exception as exc:
                logger.warning("journal_sync_comments_failed: %s", exc)
                comments = {}

        # Status-synced rows leave column I empty; backfill from /offers comments
        # without touching non-empty responses or requiring journal_confirmed.
        existing_ids = writer.project_ids_in_journal()
        for project_id, comment in comments.items():
            if project_id not in existing_ids:
                continue
            text = (comment.comment or "").strip()
            if not text:
                continue
            payload = format_response_payload(
                text,
                price=comment.price,
                delivery_days=comment.delivery_days,
            )
            if writer.update_response_by_project_id_if_empty(project_id, payload):
                result.updated_notes += 1

        for item in prepared_store.list_all():
            if not item.journal_confirmed:
                continue

            in_journal = item.project_id in existing_ids
            snap = (
                _snap_for_project(
                    browser,
                    item.project_id,
                    comments,
                    platform=item.platform,
                    settings=settings,
                )
                if item.platform in ("kwork", "yandex_uslugi", "flru")
                else None
            )
            text, price, days, used_platform = _platform_response_fields(
                snap,
                fallback_text=item.response_text,
                fallback_price=item.price,
                fallback_days=item.delivery_days,
            )

            if in_journal:
                if used_platform:
                    payload = format_response_payload(
                        text, price=price, delivery_days=days
                    )
                    if writer.update_response_by_project_id(item.project_id, payload):
                        result.updated_notes += 1
                elif snap is not None and not snap.ok:
                    logger.warning(
                        "journal_keep_existing_response project_id=%s err=%s",
                        item.project_id,
                        snap.error,
                    )
                if not item.journal_exported:
                    item.journal_exported = True
                    prepared_store.save(item)
                continue

            if item.journal_exported:
                logger.warning(
                    "journal_skip_reappend_exported project_id=%s",
                    item.project_id,
                )
                continue

            from src.pipeline.manual_copy import journal_status_for_confirm

            journal_status, journal_result = journal_status_for_confirm(item.platform)
            writer.append_prepared(
                item.project,
                item.score,
                text,
                price=price,
                delivery_days=days,
                status=journal_status,
                result=journal_result,
            )
            if used_platform and text != (item.response_text or "").strip():
                item.response_text = text
                if price:
                    item.price = price
                if days is not None:
                    item.delivery_days = days
            item.journal_exported = True
            prepared_store.save(item)
            existing_ids.add(item.project_id)
            result.appended_prepared += 1
    finally:
        if browser is not None:
            try:
                from src.browser.factory import close_browser_client

                close_browser_client(browser)
            except Exception:
                pass

    return result
