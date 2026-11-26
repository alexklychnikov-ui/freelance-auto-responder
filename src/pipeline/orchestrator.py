from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx
from aiogram.types import CallbackQuery

from src.adapters.kwork_pricing import (
    align_response_commercials,
    budget_gap,
    ensure_budget_mismatch_note,
    format_rub_amount,
    parse_budget_ceiling_rub,
    pick_listed_offer_price,
    prefer_response_price_when_no_budget,
    price_exceeds_budget_ceiling,
    project_has_listed_budget,
)
from src.adapters.offer_scope import (
    catalog_item_count_from_evidence,
    listed_price_mix_for_scope,
    suggest_delivery_days_for_scope,
)
from src.adapters.kwork import (
    KworkAdapter,
    OfferFormSnapshot,
    _is_weak_description,
    kwork_offer_form_url,
    merge_preview_into_full,
    read_submitted_offer_text,
)
from src.adapters.kwork_auth import (
    KworkAuthError,
    KworkCredentials,
    ensure_logged_in,
    is_logged_in,
)
from src.adapters.flru import FlruAdapter, FlruAuthError, FlruProjectClosed
from src.adapters.flru_urls import flru_project_url, extract_flru_project_id
from src.adapters.yandex_urls import yandex_order_url
from src.adapters.yandex_uslugi import YandexAuthError, YandexUslugiAdapter
from src.analyzer.examples_loader import load_response_examples
from src.analyzer.gpt_offer_estimator import GptOfferEstimator
from src.analyzer.gpt_response_generator import GptResponseGenerator
from src.analyzer.response_text import (
    append_missing_checklist_answers,
    ensure_response_paragraphs,
    finalize_response_text,
)
from src.analyzer.gpt_scorer import GptScorer
from src.analyzer.lightrag_client import LightRagClient
from src.analyzer.project_brief import build_project_brief
from src.analyzer.project_tier import resolve_acceptance_tier
from src.analyzer.response_history import load_recent_response_context
from src.browser.factory import close_browser_client, get_browser_client
from src.browser.playwright_adapter import persist_strip_ddos_guard_cookies
from src.config import Settings, SourceConfig, get_enabled_sources, get_settings
from src.journal.writer import JournalWriter
from src.journal.vps_sync import sync_journal_on_vps
from src.pipeline.manual_copy import is_manual_copy_platform, journal_status_for_confirm
from src.pipeline.tz_project import (
    TZ_MANUAL_SOURCE_KEY,
    TZ_MIN_CHARS,
    build_tz_project,
)
from src.limits.daily import count_today_platform_prepared, is_daily_limit_reached
from src.adapters.kwork_urls import kwork_project_view_url
from src.evidence.collector import EvidenceService, project_content_hash
from src.evidence.discovery import candidates_from_urls, extract_url_candidates
from src.evidence.models import EvidenceBundle, EvidenceFact, EvidenceInsight, EvidenceSource
from src.evidence.summary import evidence_summary_html
from src.evidence.usage import evidence_usage_issues
from src.models import PendingOffer, ProjectFull, ProjectPreview
from src.responses.prepared_store import PreparedResponse, PreparedResponseStore
from src.adapters.tg_user_watch import TgUserAuthError
from src.pipeline.flru_inbox_poller import poll_flru_inbox
from src.pipeline.kwork_inbox_poller import poll_kwork_inbox
from src.pipeline.tg_user_watch_poller import poll_tg_user_watch
from src.pipeline.yandex_inbox_poller import poll_yandex_inbox
from src.platform_health import mark_platform_up, note_platform_failure
from src.runtime_toggles import is_kwork_paused, set_kwork_paused
from src.store.early_listings import EarlyListing, EarlyListingStore
from src.store.early_ping_seen import EarlyPingSeenStore
from src.store.kwork_inbox_seen import KworkInboxSeenStore
from src.store.repository import ProjectRepository
from src.store.scan_reports import ScanCycleStats, ScanReportStore
from src.telegram_bot.bot import TelegramReviewBot
from src.telegram_bot.pending_store import PendingStore
from src.telegram_bot.review_service import ReviewService
from src.telegram_bot.scan_report import format_scan_reports_message

logger = logging.getLogger(__name__)


def _is_closed_project_error(exc: BaseException) -> bool:
    if isinstance(exc, FlruProjectClosed):
        return True
    msg = str(exc).lower()
    return "flru_project_closed" in msg or "project_closed" in msg


def _classify_flru_check_error(exc: BaseException) -> str:
    message = str(exc).lower()
    if any(
        marker in message
        for marker in (
            "timeout",
            "timed out",
            "err_timed_out",
            "deadline exceeded",
        )
    ):
        return "таймаут"
    if any(
        marker in message
        for marker in (
            "not_logged_in",
            "login",
            "storage_state",
            "cookie",
            "куки",
            "сесс",
        )
    ):
        return "куки/сессия"
    if any(
        marker in message
        for marker in (
            "failed to fetch",
            "ddos",
            "__ddg",
            "waf",
            "403",
            "429",
            "bad_origin",
            "connection",
            "network",
        )
    ):
        return "WAF/сеть"
    return "ошибка"


def _manual_outcome_tg_text(
    *, platform_label: str, project_id: str, url: str, outcome: str
) -> str | None:
    """Human message after /project|/force when no review card was sent."""
    if outcome == "notified":
        return None
    pid = html.escape(project_id)
    link = html.escape(url, quote=True) if url else ""
    tail = f"\n{link}" if link else ""
    if outcome == "extract_fail":
        return (
            f"⚠️ {platform_label} <code>{pid}</code>: не удалось прочитать карточку "
            f"(пустое/служебное описание). Пришли ТЗ текстом через /tz или ссылку ещё раз."
            f"{tail}"
        )
    if outcome == "stack_reject":
        return (
            f"⏭ {platform_label} <code>{pid}</code>: стек не подходит "
            f"(для обхода фильтра — /force).{tail}"
        )
    if outcome == "budget_reject":
        return f"⏭ {platform_label} <code>{pid}</code>: бюджет выше потолка.{tail}"
    if outcome == "timeout":
        return f"⚠️ {platform_label} <code>{pid}</code>: таймаут обработки.{tail}"
    if outcome == "closed":
        return f"⏭ {platform_label} <code>{pid}</code>: проект закрыт.{tail}"
    if outcome == "already_done":
        return f"ℹ️ {platform_label} <code>{pid}</code>: уже обработан.{tail}"
    if outcome == "error":
        return f"⚠️ {platform_label} <code>{pid}</code>: ошибка обработки.{tail}"
    return (
        f"⚠️ {platform_label} <code>{pid}</code>: обработка завершилась "
        f"({html.escape(outcome)}), карточки отклика нет.{tail}"
    )


def _normalize_compare_title(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _titles_differ_substantially(preview_title: str, page_title: str) -> bool:
    a = _normalize_compare_title(preview_title)
    b = _normalize_compare_title(page_title)
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() < 0.35

_PROCESS_QUEUE_SKIP_STATUSES = frozenset(
    {
        "scored",
        "skipped",
        "prepared",
        "submitted",
        "pending",
        "notified",
    }
)

_PREPARE_FORM_ONLY_RETRY = (
    "prepare_milestone_click_failed",
    "prepare_stages_not_visible",
    "prepare_payment_block_missing",
    "prepare_milestone_not_selected",
    "prepare_stages_failed",
    "prepare_verify_failed",
    "prepare_total_mismatch",
)


class PipelineOrchestrator:
    def __init__(
        self,
        settings: Settings,
        repository: ProjectRepository,
        review_service: ReviewService,
        scorer: GptScorer,
        response_generator: GptResponseGenerator,
        lightrag: LightRagClient,
        journal: JournalWriter,
        prepared_store: PreparedResponseStore,
        offer_estimator: GptOfferEstimator | None = None,
        evidence_service: EvidenceService | None = None,
        *,
        adapter_factory: Any | None = None,
        browser: Any | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.review_service = review_service
        self.scorer = scorer
        self.response_generator = response_generator
        self.lightrag = lightrag
        self.journal = journal
        self.prepared_store = prepared_store
        self.offer_estimator = offer_estimator or GptOfferEstimator(settings)
        self.evidence_service = evidence_service or EvidenceService()
        self._browser = browser
        self._adapter_factory = adapter_factory or self._default_adapter
        self._journal_sync_lock = asyncio.Lock()
        self._flru_scan_lock = asyncio.Lock()
        self.scan_reports = ScanReportStore(settings.database_path)
        self.kwork_inbox_seen = KworkInboxSeenStore(
            settings.kwork_inbox_seen_db or "data/kwork_inbox_seen.db"
        )
        self.flru_inbox_seen = KworkInboxSeenStore(
            settings.flru_inbox_seen_db or "data/flru_inbox_seen.db"
        )
        self.yandex_inbox_seen = KworkInboxSeenStore(
            settings.yandex_inbox_seen_db or "data/yandex_inbox_seen.db"
        )
        self.tg_user_watch_seen = KworkInboxSeenStore(
            settings.tg_user_watch_seen_db or "data/tg_user_watch_seen.db"
        )
        self.early_ping_seen = EarlyPingSeenStore(
            settings.early_ping_seen_db or "data/early_ping_seen.db"
        )
        self.early_listings = EarlyListingStore(
            settings.early_listings_dir or "data/early_listings"
        )
        self._process_queue: asyncio.Queue[tuple[SourceConfig, Any]] = asyncio.Queue()
        self._process_sem = asyncio.Semaphore(
            max(1, int(settings.process_concurrency))
        )
        self._process_queued_keys: set[tuple[str, str, str]] = set()
        self.review_service.set_approve_handler(self.handle_approve_click)
        self.review_service.set_early_go_handler(self.handle_early_go)
        self.review_service.set_early_skip_handler(self.handle_early_skip)
        self.review_service.set_journal_confirm_handler(self.handle_journal_confirm)
        self.review_service.set_prepare_retry_handler(self.handle_prepare_retry)
        self.review_service.set_regenerate_handler(self.handle_regenerate_response)
        self.review_service.set_submit_text_handler(self.handle_user_response_text)
        self.review_service.set_correct_instruction_handler(
            self._handle_correct_instruction
        )
        self.review_service.set_export_journal_handler(self.export_prepared_to_journal)
        self.review_service.set_scan_report_handler(self.send_scan_report)
        self.review_service.set_fl_check_handler(self.check_flru_health)
        self.review_service.set_notebook_sync_handler(self.request_notebook_sync)
        self.review_service.set_manual_project_handler(self.handle_manual_project)
        self.review_service.set_force_project_handler(self.handle_force_project)
        self.review_service.set_kwork_toggle_handler(self.handle_kwork_pause)
        self.review_service.set_manual_tz_handler(self.handle_manual_tz)

    def close(self) -> None:
        if self._browser is not None:
            close_browser_client(self._browser)
            self._browser = None
        self.offer_estimator.close()

    def _get_browser(self):
        if self._browser is None:
            self._browser = get_browser_client(self.settings)
        return self._browser

    def _default_adapter(self, source: SourceConfig, browser: Any | None = None):
        if source.platform == "yandex_uslugi":
            return YandexUslugiAdapter(
                source_key=source.id,
                listing_url=source.url or "",
                settings=self.settings,
                browser=browser,
            )
        if source.platform == "flru":
            return FlruAdapter(
                source_key=source.id,
                listing_url=source.url or "",
                settings=self.settings,
                filters=source.filters,
                browser=browser,
            )
        browser = browser or self._get_browser()
        if source.platform == "kwork":
            creds = None
            if pair := self.settings.kwork_credentials():
                creds = KworkCredentials(login=pair[0], password=pair[1])
            return KworkAdapter(
                source_key=source.id,
                listing_url=source.url or "",
                browser=browser,
                dry_run_submit=self.settings.dry_run_submit,
                kwork_credentials=creds,
                auto_login=self.settings.kwork_auto_login,
            )
        raise ValueError(f"Unsupported platform: {source.platform}")

    def _is_bootstrap(self, source: SourceConfig) -> bool:
        state = self.repository.get_scan_state(source.id)
        return state is None and source.bootstrap

    def _first_kwork_source(self) -> SourceConfig | None:
        for source in get_enabled_sources(self.settings.sources_config_path):
            if source.platform == "kwork":
                return source
        return None

    def _first_yandex_source(self) -> SourceConfig | None:
        for source in get_enabled_sources(self.settings.sources_config_path):
            if source.platform == "yandex_uslugi":
                return source
        return None

    def _first_flru_source(self) -> SourceConfig | None:
        for source in get_enabled_sources(self.settings.sources_config_path):
            if source.platform == "flru":
                return source
        return None

    def _resolve_source_config(self, source_key: str) -> SourceConfig | None:
        if source_key == self.MANUAL_SOURCE_KEY:
            return self._first_kwork_source()
        if source_key == self.YANDEX_MANUAL_SOURCE_KEY:
            return self._first_yandex_source()
        if source_key == self.FLRU_MANUAL_SOURCE_KEY:
            return self._first_flru_source()
        return next(
            (
                s
                for s in get_enabled_sources(self.settings.sources_config_path)
                if s.id == source_key
            ),
            None,
        )

    MANUAL_SOURCE_KEY = "kwork_manual"
    YANDEX_MANUAL_SOURCE_KEY = "yandex_manual"
    FLRU_MANUAL_SOURCE_KEY = "flru_manual"

    async def process_manual_kwork_project(self, project_id: str) -> dict[str, str]:
        kwork_source = self._first_kwork_source()
        if kwork_source is None:
            raise RuntimeError("Нет включённого источника kwork в config/sources.yaml")

        preview = ProjectPreview(
            platform="kwork",
            source_key=self.MANUAL_SOURCE_KEY,
            project_id=project_id,
            url=kwork_project_view_url(project_id),
            title="",
        )
        if not self.repository.is_known(
            preview.platform, preview.source_key, preview.project_id
        ):
            self.repository.insert_new(
                platform=preview.platform,
                source_key=preview.source_key,
                project_id=preview.project_id,
                url=preview.url,
                status="new",
            )

        try:
            outcome = await self._process_new_project(
                kwork_source, preview, manual=True
            )
        except Exception as exc:
            logger.exception("manual_kwork_project_failed project_id=%s", project_id)
            await self.review_service.tg_bot.notify(
                f"⚠️ Не удалось обработать проект {project_id}: {exc}"
            )
            return {"project_id": project_id, "outcome": "error"}

        await self._notify_manual_outcome(
            platform_label="Kwork",
            project_id=project_id,
            url=preview.url or "",
            outcome=str(outcome),
        )
        return {"project_id": project_id, "outcome": outcome}

    async def poll_flru_inbox(self) -> dict[str, int]:
        if not self.settings.flru_inbox_mirror_enabled:
            return {"bootstrapped": 0, "checked": 0, "fetched": 0, "notified": 0, "errors": 0}
        try:
            return await poll_flru_inbox(
                settings=self.settings,
                store=self.flru_inbox_seen,
                notify=self.review_service.tg_bot.notify,
            )
        except FlruAuthError as exc:
            logger.error("flru_inbox_auth_failed: %s", exc)
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }
        except Exception:
            logger.exception("flru_inbox_poll_failed")
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }

    async def poll_kwork_inbox(self) -> dict[str, int]:
        if not self.settings.kwork_inbox_mirror_enabled:
            return {"bootstrapped": 0, "checked": 0, "fetched": 0, "notified": 0, "errors": 0}
        if is_kwork_paused(self.settings):
            logger.info("kwork_inbox_poll skipped: kwork paused")
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 0,
            }
        creds = None
        if pair := self.settings.kwork_credentials():
            creds = KworkCredentials(login=pair[0], password=pair[1])
        try:
            return await poll_kwork_inbox(
                settings=self.settings,
                store=self.kwork_inbox_seen,
                notify=self.review_service.tg_bot.notify,
                credentials=creds,
                auto_login=self.settings.kwork_auto_login,
            )
        except KworkAuthError as exc:
            logger.error("kwork_inbox_auth_failed: %s", exc)
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }
        except Exception:
            logger.exception("kwork_inbox_poll_failed")
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }

    async def poll_yandex_inbox(self) -> dict[str, int]:
        if not self.settings.yandex_inbox_mirror_enabled:
            return {"bootstrapped": 0, "checked": 0, "fetched": 0, "notified": 0, "errors": 0}
        try:
            return await poll_yandex_inbox(
                settings=self.settings,
                store=self.yandex_inbox_seen,
                notify=self.review_service.tg_bot.notify,
            )
        except YandexAuthError as exc:
            logger.error("yandex_inbox_auth_failed: %s", exc)
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }
        except Exception:
            logger.exception("yandex_inbox_poll_failed")
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }

    async def poll_tg_user_watch(self) -> dict[str, int]:
        if not self.settings.tg_user_watch_enabled:
            return {"bootstrapped": 0, "checked": 0, "fetched": 0, "notified": 0, "errors": 0}
        try:
            return await poll_tg_user_watch(
                settings=self.settings,
                store=self.tg_user_watch_seen,
                notify=self.review_service.tg_bot.notify,
            )
        except TgUserAuthError as exc:
            logger.error("tg_user_watch_auth_failed: %s", exc)
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }
        except Exception:
            logger.exception("tg_user_watch_poll_failed")
            return {
                "bootstrapped": 0,
                "checked": 0,
                "fetched": 0,
                "notified": 0,
                "errors": 1,
            }

    async def run_scan_cycle(self) -> dict[str, int]:
        totals = {"seen": 0, "new": 0, "skipped": 0, "scored": 0, "notified": 0}
        cycle_stats = ScanCycleStats()
        by_platform: dict[str, ScanCycleStats] = {}
        self.review_service.expire_stale_pending()

        if self.settings.kwork_inbox_mirror_enabled:
            inbox_stats = await self.poll_kwork_inbox()
            logger.info("kwork_inbox_poll %s", inbox_stats)

        if self.settings.flru_inbox_mirror_enabled:
            flru_inbox_stats = await self.poll_flru_inbox()
            logger.info("flru_inbox_poll %s", flru_inbox_stats)

        if self.settings.yandex_inbox_mirror_enabled:
            yandex_inbox_stats = await self.poll_yandex_inbox()
            logger.info("yandex_inbox_poll %s", yandex_inbox_stats)

        # tg_user_watch: only scheduler.inbox_loop (avoid dual-poll duplicate pings)

        for source in get_enabled_sources(self.settings.sources_config_path):
            if self.settings.early_scan_enabled_for(source.platform):
                continue
            if source.platform == "kwork" and is_kwork_paused(self.settings):
                logger.info("scan_cycle skip kwork source=%s: paused", source.id)
                continue
            source_stats = ScanCycleStats()
            try:
                previews = await self._scan_source(
                    source,
                    allow_guest_fallback=True,
                )
            except KworkAuthError as exc:
                logger.error("Kwork auth failed for %s: %s", source.id, exc)
                await self.review_service.tg_bot.notify(
                    f"⚠️ Kwork login failed ({source.id}): {exc}"
                )
                continue
            except YandexAuthError as exc:
                # Soft fail: no crash loop.
                # not_logged_in / transient net (ERR_TIMED_OUT after retries) → log only.
                msg = str(exc)
                transient_net = any(
                    m in msg
                    for m in (
                        "ERR_TIMED_OUT",
                        "Timeout",
                        "net::ERR_",
                        "ERR_CONNECTION",
                        "ERR_HTTP2",
                        "ERR_NETWORK",
                        "ERR_NAME_NOT_RESOLVED",
                        "ERR_ADDRESS_UNREACHABLE",
                    )
                )
                if "not_logged_in" in msg or transient_net:
                    logger.warning(
                        "Yandex scan soft-fail for %s (no TG): %s",
                        source.id,
                        msg[:300],
                    )
                else:
                    logger.error("Yandex auth/scan failed for %s: %s", source.id, msg)
                    await self.review_service.tg_bot.notify(
                        f"⚠️ Яндекс Услуги ({source.id}): {html.escape(msg[:400])}"
                    )
                continue
            except FlruAuthError as exc:
                msg = str(exc)
                soft = (
                    "not_logged_in" in msg
                    or "Extra data" in msg
                    or "JSONDecodeError" in msg
                    or "storage_state" in msg
                    or any(
                        m in msg
                        for m in (
                            "ERR_TIMED_OUT",
                            "Timeout",
                            "net::ERR_",
                            "ERR_CONNECTION",
                            "ERR_HTTP2",
                        )
                    )
                )
                if soft:
                    logger.warning(
                        "FL.ru scan soft-fail for %s (no TG): %s",
                        source.id,
                        msg[:300],
                    )
                else:
                    logger.error("FL.ru auth/scan failed for %s: %s", source.id, msg)
                    await self.review_service.tg_bot.notify(
                        f"⚠️ FL.ru ({source.id}): {html.escape(msg[:400])}"
                    )
                continue
            bootstrap = self._is_bootstrap(source)

            known_streak = 0
            new_in_source = 0

            for preview in previews:
                totals["seen"] += 1
                source_stats.seen += 1
                known = self.repository.is_known(
                    preview.platform, preview.source_key, preview.project_id
                )
                stuck_new = known and self.repository.is_unprocessed_new(
                    preview.platform, preview.source_key, preview.project_id
                )
                if known and not stuck_new:
                    totals["skipped"] += 1
                    known_streak += 1
                    # Early-exit only on long listings (Kwork). Short cab pages
                    # (Yandex/FL.ru ~10–30) mix known+new — cutting after N known
                    # misses new cards at the bottom.
                    if (
                        len(previews)
                        > max(40, self.settings.scan_early_exit_known_count * 4)
                        and known_streak >= self.settings.scan_early_exit_known_count
                    ):
                        break
                    continue

                known_streak = 0

                if not known:
                    if bootstrap and self.settings.scan_bootstrap_skip_pipeline:
                        self.repository.bootstrap_skip(
                            platform=preview.platform,
                            source_key=preview.source_key,
                            project_id=preview.project_id,
                            title=preview.title,
                            url=preview.url,
                            published_at=(
                                preview.published_at.isoformat()
                                if preview.published_at
                                else None
                            ),
                        )
                        totals["skipped"] += 1
                        continue

                    self.repository.insert_new(
                        platform=preview.platform,
                        source_key=preview.source_key,
                        project_id=preview.project_id,
                        title=preview.title,
                        url=preview.url,
                        published_at=(
                            preview.published_at.isoformat()
                            if preview.published_at
                            else None
                        ),
                    )
                    totals["new"] += 1
                    new_in_source += 1

                try:
                    outcome = await self._process_new_project(source, preview)
                except Exception as exc:
                    if _is_closed_project_error(exc):
                        logger.info(
                            "project_closed_skip platform=%s source=%s project_id=%s err=%s",
                            preview.platform,
                            preview.source_key,
                            preview.project_id,
                            exc,
                        )
                        self.repository.update_status(
                            preview.platform,
                            preview.source_key,
                            preview.project_id,
                            "skipped",
                        )
                        totals["skipped"] += 1
                        source_stats.checked += 1
                        continue
                    logger.exception(
                        "process_project_failed platform=%s source=%s project_id=%s "
                        "(left status=new for retry)",
                        preview.platform,
                        preview.source_key,
                        preview.project_id,
                    )
                    source_stats.checked += 1
                    continue

                source_stats.checked += 1
                if outcome == "extract_fail":
                    totals["skipped"] += 1
                else:
                    totals["scored"] += 1
                if outcome == "stack_reject":
                    source_stats.rejected_stack += 1
                elif outcome == "budget_reject":
                    source_stats.rejected_budget += 1
                elif outcome == "extract_fail":
                    source_stats.rejected_stack += 1
                elif outcome == "notified":
                    source_stats.notified += 1
                    totals["notified"] += 1

            max_id = max(
                (int(p.project_id) for p in previews if p.project_id.isdigit()),
                default=0,
            )
            self.repository.set_scan_state(
                source_key=source.id,
                platform=source.platform,
                last_known_project_id=str(max_id) if max_id else None,
                last_new_project_at=(
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                    if new_in_source
                    else None
                ),
            )

            logger.info(
                "[scan] platform=%s source=%s | seen=%d | new=%d | skipped=%d | "
                "checked=%d | stack=%d | budget=%d | notified=%d",
                source.platform,
                source.id,
                source_stats.seen,
                new_in_source,
                source_stats.seen - new_in_source,
                source_stats.checked,
                source_stats.rejected_stack,
                source_stats.rejected_budget,
                source_stats.notified,
            )
            plat = source.platform
            if plat not in by_platform:
                by_platform[plat] = ScanCycleStats()
            by_platform[plat].merge(source_stats)
            cycle_stats.merge(source_stats)

        self.scan_reports.save(cycle_stats, by_platform=by_platform)
        return totals

    def enqueue_process(self, source: SourceConfig, preview: Any) -> bool:
        if (
            getattr(preview, "platform", None) == "kwork"
            or getattr(source, "platform", None) == "kwork"
        ) and is_kwork_paused(self.settings):
            logger.info(
                "process_queue skip enqueue kwork paused project_id=%s",
                getattr(preview, "project_id", "?"),
            )
            return False
        key = (preview.platform, preview.source_key, str(preview.project_id))
        if key in self._process_queued_keys:
            return False
        self._process_queued_keys.add(key)
        self._process_queue.put_nowait((source, preview))
        return True

    async def run_process_worker(self, shutdown: asyncio.Event) -> None:
        while not shutdown.is_set():
            try:
                source, preview = await asyncio.wait_for(
                    self._process_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            key = (preview.platform, preview.source_key, str(preview.project_id))
            try:
                async with self._process_sem:
                    await self._run_queued_process(source, preview)
            finally:
                self._process_queued_keys.discard(key)
                self._process_queue.task_done()

    async def _run_queued_process(
        self, source: SourceConfig, preview: Any
    ) -> None:
        if (
            getattr(preview, "platform", None) == "kwork"
            or getattr(source, "platform", None) == "kwork"
        ) and is_kwork_paused(self.settings):
            logger.info(
                "process_queue skip run kwork paused project_id=%s",
                getattr(preview, "project_id", "?"),
            )
            return
        status = self.repository.get_status(
            preview.platform, preview.source_key, preview.project_id
        )
        if status in _PROCESS_QUEUE_SKIP_STATUSES:
            await self._finalize_early_listing_after_process(
                preview.platform,
                preview.source_key,
                str(preview.project_id),
                outcome="already_done",
            )
            return
        outcome = "error"
        try:
            outcome = await asyncio.wait_for(
                self._process_new_project(source, preview),
                timeout=180.0,
            )
        except asyncio.TimeoutError:
            logger.error(
                "process_queue_timeout platform=%s source=%s project_id=%s",
                preview.platform,
                preview.source_key,
                preview.project_id,
            )
            self.repository.update_status(
                preview.platform,
                preview.source_key,
                preview.project_id,
                "skipped",
            )
            outcome = "timeout"
        except Exception as exc:
            if _is_closed_project_error(exc):
                logger.info(
                    "process_queue_closed_skip platform=%s source=%s project_id=%s",
                    preview.platform,
                    preview.source_key,
                    preview.project_id,
                )
                self.repository.update_status(
                    preview.platform,
                    preview.source_key,
                    preview.project_id,
                    "skipped",
                )
                outcome = "closed"
            else:
                logger.exception(
                    "process_queue_failed platform=%s source=%s project_id=%s",
                    preview.platform,
                    preview.source_key,
                    preview.project_id,
                )
                outcome = "error"
        await self._finalize_early_listing_after_process(
            preview.platform,
            preview.source_key,
            str(preview.project_id),
            outcome=str(outcome),
        )

    async def _finalize_early_listing_after_process(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        *,
        outcome: str,
    ) -> None:
        listing = self.early_listings.load(platform, source_key, project_id)
        if listing is None or listing.status not in {"awaiting", "accepted"}:
            return
        status = self.repository.get_status(platform, source_key, project_id)
        score_val, _fit = self.repository.get_score_fit(
            platform, source_key, project_id
        )

        if outcome == "notified" or status == "notified":
            score_txt = ""
            if score_val is not None:
                score_txt = f" (score {int(score_val)})"
            if listing.budget_warning:
                footer = (
                    f"✅ Подходит{score_txt} — ⚠️ бюджет {listing.budget_warning}"
                )
            else:
                footer = "✅ Подходит — карточка со score ниже"
            listing.status = "notified"
        elif outcome == "stack_reject":
            score_txt = ""
            if score_val is not None:
                score_txt = f" (score {int(score_val)}/10)"
            footer = f"⏭ Стек не подходит{score_txt}"
            listing.status = "rejected"
        elif outcome == "budget_reject":
            footer = "⏭ Бюджет: оценка выше потолка"
            listing.status = "rejected"
        elif outcome == "extract_fail":
            footer = "⏭ Не удалось извлечь карточку"
            listing.status = "rejected"
        elif status in {"skipped", "rejected"}:
            score_txt = ""
            if score_val is not None:
                score_txt = f" (score {int(score_val)}/10)"
            footer = f"⏭ Пропущено{score_txt}"
            listing.status = "rejected"
        elif outcome == "timeout":
            footer = "⚠️ Таймаут скоринга — пропущено"
            listing.status = "rejected"
        elif outcome == "closed":
            footer = "⏭ Проект закрыт"
            listing.status = "rejected"
        elif outcome == "already_done":
            footer = f"ℹ️ Уже обработано ({status or '—'})"
            listing.status = "done"
        else:
            footer = f"⚠️ Ошибка обработки ({outcome})"
            listing.status = "error"

        self.early_listings.save(listing)
        if listing.telegram_message_id is None:
            return
        try:
            await self.review_service.tg_bot.edit_early_listing_result(
                listing.telegram_message_id,
                platform=listing.platform,
                title=listing.title,
                offers=listing.offers_count,
                source_key=listing.source_key,
                url=listing.url,
                footer=footer,
            )
        except Exception:
            logger.exception(
                "early_listing_edit_failed project_id=%s msg_id=%s",
                project_id,
                listing.telegram_message_id,
            )

    async def _alert_platform_down(
        self, platform: str, source_id: str, message: str
    ) -> None:
        label = {
            "kwork": "Kwork",
            "yandex_uslugi": "Яндекс Услуги",
            "flru": "FL.ru",
        }.get(platform, platform)
        if not note_platform_failure(self.settings, platform):
            logger.warning(
                "platform_down_alert_throttled platform=%s source=%s msg=%s",
                platform,
                source_id,
                message[:200],
            )
            return
        await self.review_service.tg_bot.notify(
            f"🚨 <b>{html.escape(label)} отвалилась</b>\n"
            f"Источник: <code>{html.escape(source_id)}</code>\n"
            f"{html.escape(message[:400])}\n"
            f"Повторная проверка ~2 мин; алерт не чаще чем раз в 30 мин. "
            f"/report — статус."
        )

    async def run_early_scan_cycle(self) -> dict[str, int]:
        totals = {
            "seen": 0,
            "new": 0,
            "pinged": 0,
            "enqueued": 0,
            "skipped": 0,
            "errors": 0,
        }
        by_platform: dict[str, ScanCycleStats] = {}
        tg = self.review_service.tg_bot
        kwork_paused = is_kwork_paused(self.settings)

        def _plat_stats(platform: str) -> ScanCycleStats:
            stats = by_platform.get(platform)
            if stats is None:
                stats = ScanCycleStats()
                by_platform[platform] = stats
            return stats

        for source in get_enabled_sources(self.settings.sources_config_path):
            if not self.settings.early_scan_enabled_for(source.platform):
                continue
            if source.platform == "kwork" and kwork_paused:
                logger.info(
                    "early_scan skip kwork source=%s: paused", source.id
                )
                continue
            plat_stats = _plat_stats(source.platform)
            try:
                previews = await self._scan_source(
                    source,
                    allow_guest_fallback=False,
                )
            except KworkAuthError as exc:
                msg = str(exc)
                logger.error("Kwork auth failed for %s: %s", source.id, msg)
                plat_stats.errors += 1
                plat_stats.error = msg[:300]
                totals["errors"] += 1
                await self._alert_platform_down(source.platform, source.id, msg)
                continue
            except YandexAuthError as exc:
                msg = str(exc)
                logger.warning(
                    "Yandex early_scan failed for %s: %s", source.id, msg[:300]
                )
                plat_stats.errors += 1
                plat_stats.error = msg[:300]
                totals["errors"] += 1
                await self._alert_platform_down(source.platform, source.id, msg)
                continue
            except FlruAuthError as exc:
                msg = str(exc)
                logger.warning(
                    "FL.ru early_scan failed for %s: %s", source.id, msg[:300]
                )
                plat_stats.errors += 1
                plat_stats.error = msg[:300]
                totals["errors"] += 1
                await self._alert_platform_down(source.platform, source.id, msg)
                continue

            if mark_platform_up(self.settings, source.platform):
                label = {
                    "kwork": "Kwork",
                    "yandex_uslugi": "Яндекс Услуги",
                    "flru": "FL.ru",
                }.get(source.platform, source.platform)
                await tg.notify(f"✅ {label} снова в строю (скан ок)")

            bootstrap = self._is_bootstrap(source)
            known_streak = 0
            new_in_source = 0
            source_seen = 0
            source_pinged = 0
            early_ping_overflow = 0
            ping_cap = max(0, int(self.settings.early_ping_max_per_source))

            for preview in previews:
                totals["seen"] += 1
                source_seen += 1
                plat_stats.seen += 1
                known = self.repository.is_known(
                    preview.platform, preview.source_key, preview.project_id
                )
                stuck_new = known and self.repository.is_unprocessed_new(
                    preview.platform, preview.source_key, preview.project_id
                )
                if known and not stuck_new:
                    totals["skipped"] += 1
                    known_streak += 1
                    if (
                        len(previews)
                        > max(40, self.settings.scan_early_exit_known_count * 4)
                        and known_streak
                        >= self.settings.scan_early_exit_known_count
                    ):
                        break
                    continue

                known_streak = 0
                bootstrap_skipped = False

                if not known:
                    if bootstrap and self.settings.scan_bootstrap_skip_pipeline:
                        self.repository.bootstrap_skip(
                            platform=preview.platform,
                            source_key=preview.source_key,
                            project_id=preview.project_id,
                            title=preview.title,
                            url=preview.url,
                            published_at=(
                                preview.published_at.isoformat()
                                if preview.published_at
                                else None
                            ),
                        )
                        totals["skipped"] += 1
                        bootstrap_skipped = True
                    else:
                        self.repository.insert_new(
                            platform=preview.platform,
                            source_key=preview.source_key,
                            project_id=preview.project_id,
                            title=preview.title,
                            url=preview.url,
                            published_at=(
                                preview.published_at.isoformat()
                                if preview.published_at
                                else None
                            ),
                        )
                        totals["new"] += 1
                        new_in_source += 1

                if not bootstrap_skipped:
                    if self.early_ping_seen.mark_if_new(
                        preview.platform, preview.project_id
                    ):
                        if source_pinged >= ping_cap:
                            early_ping_overflow += 1
                            continue
                        offers = getattr(preview, "offers_count", None)
                        if offers is None:
                            offers = getattr(preview, "responses_count", None)
                        msg_id = await tg.notify_early_listing(
                            platform=preview.platform,
                            source_key=preview.source_key,
                            project_id=preview.project_id,
                            title=preview.title or "",
                            offers=offers if isinstance(offers, int) else None,
                            url=preview.url or "",
                            requires_paid_response=getattr(
                                preview, "requires_paid_response", None
                            ),
                        )
                        self.early_listings.save(
                            EarlyListing(
                                platform=preview.platform,
                                source_key=preview.source_key,
                                project_id=str(preview.project_id),
                                url=preview.url or "",
                                title=preview.title or "",
                                offers_count=(
                                    offers if isinstance(offers, int) else None
                                ),
                                telegram_message_id=msg_id,
                                status="awaiting",
                            )
                        )
                        totals["pinged"] += 1
                        source_pinged += 1
                        plat_stats.notified += 1

            if early_ping_overflow > 0:
                overflow_label = {
                    "kwork": "Kwork",
                    "yandex_uslugi": "Яндекс Услуги",
                    "flru": "FL.ru",
                }.get(source.platform, source.platform)
                await tg.notify(
                    f"⚡ {overflow_label}: ещё {early_ping_overflow} новых "
                    f"без early-ping (лимит {ping_cap}/цикл)"
                )

            digit_ids = [
                int(p.project_id) for p in previews if p.project_id.isdigit()
            ]
            if digit_ids:
                last_known = str(max(digit_ids))
            elif previews:
                last_known = str(previews[-1].project_id)
            else:
                last_known = None
            self.repository.set_scan_state(
                source_key=source.id,
                platform=source.platform,
                last_known_project_id=last_known,
                last_new_project_at=(
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                    if new_in_source
                    else None
                ),
            )

            logger.info(
                "early_scan platform=%s source=%s seen=%d new=%d pinged=%d "
                "enqueued=%d skipped=%d",
                source.platform,
                source.id,
                source_seen,
                new_in_source,
                source_pinged,
                totals["enqueued"],
                totals["skipped"],
            )

        cycle_stats = ScanCycleStats(
            seen=totals["seen"],
            checked=totals["seen"],
            notified=totals["pinged"],
            errors=totals["errors"],
        )
        if by_platform:
            self.scan_reports.save(cycle_stats, by_platform=by_platform)

        return totals

    async def run_kwork_fast_scan_cycle(self) -> dict[str, int]:
        return await self.run_early_scan_cycle()

    async def handle_early_go(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        callback: CallbackQuery,
    ) -> None:
        listing = self.early_listings.load(platform, source_key, project_id)
        if listing is None:
            await callback.answer("Карточка не найдена / уже обработана", show_alert=True)
            return
        if listing.status != "awaiting":
            await callback.answer(f"Уже: {listing.status}", show_alert=True)
            return

        source = self._resolve_source_config(source_key)
        if source is None or source.platform != platform:
            await callback.answer("Источник не найден", show_alert=True)
            return

        await callback.answer("Ок, считаю score…")
        listing.status = "accepted"
        self.early_listings.save(listing)
        try:
            if callback.message is not None:
                await callback.message.edit_reply_markup(reply_markup=None)
                base = callback.message.text or callback.message.html_text or ""
                await callback.message.edit_text(
                    f"{base}\n\n⏳ Считаю стек / риски…",
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
        except Exception:
            logger.exception("early_go_edit_failed project_id=%s", project_id)

        preview = ProjectPreview(
            platform=listing.platform,
            source_key=listing.source_key,
            project_id=listing.project_id,
            url=listing.url,
            title=listing.title,
            responses_count=listing.offers_count,
        )
        if self.enqueue_process(source, preview):
            logger.info(
                "early_go_enqueued platform=%s source=%s project_id=%s",
                platform,
                source_key,
                project_id,
            )
        else:
            await self.review_service.tg_bot.notify(
                f"ℹ️ Уже в очереди: {listing.title}"
            )

    async def handle_early_skip(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        callback: CallbackQuery,
    ) -> None:
        listing = self.early_listings.load(platform, source_key, project_id)
        title = listing.title if listing is not None else project_id
        if listing is not None and listing.status != "awaiting":
            await callback.answer(f"Уже: {listing.status}", show_alert=True)
            return

        await callback.answer("Пропущено")
        if listing is not None:
            listing.status = "skipped"
            self.early_listings.save(listing)
        self.repository.update_status(platform, source_key, project_id, "skipped")
        try:
            if callback.message is not None:
                await callback.message.edit_reply_markup(reply_markup=None)
                base = callback.message.text or ""
                await callback.message.edit_text(
                    f"{base}\n\n⏭ Пропущено",
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
        except Exception:
            logger.exception("early_skip_edit_failed project_id=%s", project_id)
        logger.info(
            "early_skip platform=%s source=%s project_id=%s title=%s",
            platform,
            source_key,
            project_id,
            title,
        )

    async def _scan_source(
        self,
        source: SourceConfig,
        *,
        allow_guest_fallback: bool,
    ) -> list[Any]:
        if source.platform != "flru":
            return await asyncio.to_thread(self._scan_listings, source)
        async with self._flru_scan_lock:
            return await asyncio.to_thread(
                self._scan_listings,
                source,
                allow_guest_fallback=allow_guest_fallback,
            )

    def _scan_listings(
        self,
        source: SourceConfig,
        *,
        allow_guest_fallback: bool = True,
    ) -> list[Any]:
        self._last_flru_scan_via = None
        if source.platform in ("yandex_uslugi", "flru"):
            adapter = self._adapter_factory(source, None)
            try:
                if source.platform == "flru":
                    previews = adapter.scan_new(
                        allow_guest_fallback=allow_guest_fallback
                    )
                    self._last_flru_scan_via = getattr(
                        adapter, "last_scan_via", None
                    )
                    return previews
                return adapter.scan_new()
            finally:
                close = getattr(adapter, "close", None)
                if callable(close):
                    close()
        browser = get_browser_client(self.settings)
        try:
            adapter = self._adapter_factory(source, browser)
            return adapter.scan_new()
        finally:
            close_browser_client(browser)

    def _read_full_listing(self, source: SourceConfig, project_id: str) -> ProjectFull:
        if source.platform in ("yandex_uslugi", "flru"):
            adapter = self._adapter_factory(source, None)
            try:
                return adapter.read_full(project_id)
            finally:
                close = getattr(adapter, "close", None)
                if callable(close):
                    close()
        browser = get_browser_client(self.settings)
        try:
            adapter = self._adapter_factory(source, browser)
            return adapter.read_full(project_id)
        finally:
            close_browser_client(browser)

    async def _process_new_project(
        self, source: SourceConfig, preview: Any, *, manual: bool = False
    ) -> str:
        full = await asyncio.to_thread(self._read_full_listing, source, preview.project_id)
        page_title = (full.title or "").strip()
        page_desc = (full.full_description or "").strip()
        page_extract_empty = not page_title and len(page_desc) < 20

        full = merge_preview_into_full(full, preview)
        full = full.model_copy(
            update={
                "source_key": preview.source_key,
                "url": preview.url or full.url,
            }
        )

        title = (full.title or "").strip()
        desc = (full.full_description or "").strip()
        preview_title = str(getattr(preview, "title", "") or "").strip()
        if preview_title and title and _titles_differ_substantially(preview_title, title):
            logger.warning(
                "title_mismatch preview=%r page=%r project_id=%s",
                preview_title,
                title,
                full.project_id,
            )
        if page_extract_empty or not title or (len(desc) < 20 and len(title) < 10):
            logger.warning(
                "extract_fail project_id=%s title=%r desc_len=%s page_empty=%s",
                full.project_id,
                title,
                len(desc),
                page_extract_empty,
            )
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
            )
            return "extract_fail"
        if _is_weak_description(title, desc) or _is_weak_description(page_title, page_desc):
            logger.warning(
                "extract_fail_weak_desc project_id=%s title=%r desc_len=%s page_desc_len=%s",
                full.project_id,
                title,
                len(desc),
                len(page_desc),
            )
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
            )
            return "extract_fail"

        return await self._process_project_full(full, manual=manual)

    async def _process_project_full(
        self, full: ProjectFull, *, manual: bool = False
    ) -> str:
        title = (full.title or "").strip()
        desc = (full.full_description or "").strip()
        if (
            full.platform == "flru"
            and full.requires_paid_response is True
        ):
            from src.adapters.flru import flru_paid_response_tg_mark

            msg = flru_paid_response_tg_mark(title=title or full.project_id, url=full.url)
            logger.info(
                "flru_paid_response_skip project_id=%s manual=%s",
                full.project_id,
                manual,
            )
            try:
                await self.review_service.tg_bot.notify(msg)
            except Exception:
                logger.warning(
                    "flru_paid_response_notify_failed project_id=%s",
                    full.project_id,
                    exc_info=True,
                )
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
            )
            return "paid_response_required"
        if not title or len(desc) < 20:
            logger.warning(
                "extract_fail project_id=%s title=%r desc_len=%s",
                full.project_id,
                title,
                len(desc),
            )
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
            )
            return "extract_fail"
        if _is_weak_description(title, desc):
            logger.warning(
                "extract_fail_weak_desc project_id=%s title=%r desc_len=%s",
                full.project_id,
                title,
                len(desc),
            )
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
            )
            return "extract_fail"

        context = await asyncio.to_thread(self.lightrag.get_scoring_context, full)
        examples = await asyncio.to_thread(
            load_response_examples, self.settings.response_examples_dir
        )
        score = await asyncio.to_thread(
            self.scorer.score, full, context, examples=examples
        )
        acceptance_tier = (
            "standard" if manual else resolve_acceptance_tier(full, score, self.settings)
        )
        self.repository.update_status(
            full.platform,
            full.source_key,
            full.project_id,
            "scored",
            fit=score.fit,
            score=float(score.score),
        )

        if acceptance_tier is None:
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
                fit=score.fit,
                score=float(score.score),
            )
            return "stack_reject"

        budget_ceiling_warning: str | None = None
        if not manual and acceptance_tier == "standard":
            gap = await self._budget_ceiling_gap(full, context)
            if gap is not None:
                estimated, ceiling = gap
                mult = self.settings.budget_ceiling_price_multiplier
                budget_ceiling_warning = (
                    f"⚠️ Бюджет: оценка {format_rub_amount(estimated)} ₽ > "
                    f"потолок {format_rub_amount(ceiling)} ₽ ×{mult} — решай сам"
                )
                logger.info(
                    "budget_ceiling_warn project_id=%s estimated=%s ceiling=%s mult=%s",
                    full.project_id,
                    estimated,
                    ceiling,
                    mult,
                )
                await self.review_service.tg_bot.notify(budget_ceiling_warning)
                early = self.early_listings.load(
                    full.platform, full.source_key, full.project_id
                )
                if early is not None:
                    early.budget_warning = (
                        f"оценка {estimated // 1000}k vs потолок {ceiling // 1000}k"
                    )
                    self.early_listings.save(early)

        if self.settings.require_telegram_approval:
            await self.review_service.request_review(
                full,
                score,
                acceptance_tier=acceptance_tier,
                budget_ceiling_warning=budget_ceiling_warning,
            )
            return "notified"

        offer = PendingOffer(
            platform=full.platform,
            source_key=full.source_key,
            project_id=full.project_id,
            url=full.url,
            title=full.title,
            project=full,
            score=score,
            acceptance_tier=acceptance_tier,
            created_at=datetime.now(timezone.utc),
            status="approved",
            approved_at=datetime.now(timezone.utc),
            budget_ceiling_warning=budget_ceiling_warning,
        )
        if is_manual_copy_platform(full.platform):
            await self._ensure_response_text(offer)
            await self._send_manual_copy(offer)
        elif self.settings.prepare_only_no_submit:
            await self._prepare_offer_on_site(offer)
        else:
            await self._ensure_response_text(offer)
            await self.handle_approved(
                full.platform, full.source_key, full.project_id, offer
            )
        return "notified"

    async def _budget_ceiling_gap(
        self, full: ProjectFull, lightrag_context: str
    ) -> tuple[int, int] | None:
        if parse_budget_ceiling_rub(full) is None:
            return None
        estimated = await asyncio.to_thread(
            self.offer_estimator.estimate_market_cost, full, lightrag_context
        )
        if price_exceeds_budget_ceiling(
            estimated,
            full,
            multiplier=self.settings.budget_ceiling_price_multiplier,
        ):
            ceiling = parse_budget_ceiling_rub(full)
            if ceiling is None:
                return None
            return estimated, ceiling
        return None

    async def _refresh_offer_project(self, offer: PendingOffer) -> ProjectFull:
        if offer.source_key == TZ_MANUAL_SOURCE_KEY:
            return offer.project
        brief = build_project_brief(offer.project)
        budgets_missing = not (offer.project.desired_budget or "").strip() and not (
            offer.project.max_budget or ""
        ).strip()
        # Long description alone is not enough — missing budgets must re-scrape
        # because flat «Цена N ₽» cards can otherwise keep None/None.
        if (
            not budgets_missing
            and len(brief) >= 80
            and len(offer.project.full_description or "") >= 40
        ):
            return offer.project
        source = self._resolve_source_config(offer.source_key)
        if source is None:
            return offer.project
        try:
            full = await asyncio.to_thread(
                self._read_full_listing, source, offer.project_id
            )
            full = full.model_copy(update={"source_key": offer.source_key})
            offer.project = full
            offer.title = full.title
            offer.url = full.url
            self.review_service.store.save(offer)
            logger.info(
                "project_refreshed project_id=%s desc_len=%s",
                offer.project_id,
                len(full.full_description or ""),
            )
            return full
        except Exception:
            logger.exception("project_refresh_failed project_id=%s", offer.project_id)
            return offer.project

    async def ensure_evidence(
        self,
        offer: PendingOffer,
        *,
        notify: Any | None = None,
    ) -> EvidenceBundle:
        """Collect or reuse EvidenceBundle before response generation. Fail-open."""
        current_hash = project_content_hash(offer.project)
        if not self.settings.evidence_research_enabled:
            # Auto-collect when TZ already exposes URLs/attachment markers
            # (fail-open path below). No candidates → keep not_required.
            try:
                from src.evidence.discovery import discover

                auto_candidates = discover(
                    offer.project,
                    max_urls=int(self.settings.evidence_max_urls),
                )
            except Exception:
                logger.warning(
                    "evidence_discover_for_auto_failed", exc_info=True
                )
                auto_candidates = []
            if not auto_candidates:
                bundle = EvidenceBundle(
                    status="not_required",
                    required=False,
                    project_hash=current_hash,
                )
                prior = offer.evidence
                offer.evidence = bundle
                # Clear stale prior evidence from pending so prepared saves stay clean.
                if prior is not None and (
                    prior.status != "not_required"
                    or prior.required
                    or prior.facts
                    or prior.sources
                    or prior.insights
                    or prior.warnings
                ):
                    self.review_service.store.save(offer)
                return bundle

        if (
            offer.evidence is not None
            and offer.evidence.project_hash == current_hash
        ):
            # Flag-off auto-collect: don't stick on a prior not_required stub.
            if not (
                not self.settings.evidence_research_enabled
                and offer.evidence.status == "not_required"
            ):
                return offer.evidence

        async def _notify(msg: str) -> None:
            if notify is None:
                return
            try:
                await notify(msg)
            except Exception:
                logger.warning("evidence_progress_notify_failed", exc_info=True)

        await _notify("🔎 Проверяю ссылки…")
        t0 = time.perf_counter()
        try:
            bundle = await asyncio.to_thread(
                self.evidence_service.collect,
                offer.project,
                settings=self.settings,
                timeout=float(self.settings.evidence_timeout_seconds),
                max_urls=int(self.settings.evidence_max_urls),
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.exception(
                "evidence_collect_failed project_id=%s latency_ms=%s",
                offer.project_id,
                latency_ms,
            )
            bundle = EvidenceBundle(
                status="failed",
                required=True,
                project_hash=current_hash,
                warnings=[f"evidence_collect_error: {type(exc).__name__}"],
            )
        else:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            status_counts = Counter(s.status for s in bundle.sources)
            err_codes = sorted(
                {s.error_code for s in bundle.sources if s.error_code}
            )
            logger.info(
                "evidence_ensure project_id=%s sources=%s statuses=%s "
                "facts=%s latency_ms=%s errors=%s status=%s",
                offer.project_id,
                len(bundle.sources),
                dict(status_counts),
                len(bundle.facts),
                latency_ms,
                err_codes,
                bundle.status,
            )

        offer.evidence = bundle
        self.review_service.store.save(offer)
        return bundle

    async def _generate_response_text(
        self,
        offer: PendingOffer,
        *,
        notify: Any | None = None,
    ) -> str:
        await self._refresh_offer_project(offer)
        evidence = await self.ensure_evidence(offer, notify=notify)
        context = await asyncio.to_thread(self.lightrag.get_full_context)
        examples = await asyncio.to_thread(
            load_response_examples, self.settings.response_examples_dir
        )
        recent = await asyncio.to_thread(
            load_recent_response_context, self.prepared_store
        )
        fair_price = 0
        try:
            fair_price = int(
                await asyncio.to_thread(
                    self.offer_estimator.estimate_market_cost,
                    offer.project,
                    context,
                )
            )
        except Exception:
            logger.exception(
                "fair_price_estimate_failed project_id=%s", offer.project_id
            )
        volume = catalog_item_count_from_evidence(evidence)
        mix = listed_price_mix_for_scope(offer.project, volume=volume)
        gap = (
            budget_gap(fair_price, offer.project, mix=mix)
            if fair_price > 0
            else None
        )
        listed = pick_listed_offer_price(offer.project, fair=fair_price, mix=mix)
        price_hint: int | str | None = (
            listed if listed > 0 else fair_price if fair_price > 0 else None
        )
        days_hint = suggest_delivery_days_for_scope(
            offer.project, volume=volume
        )
        gen = self.response_generator
        gen_with = getattr(gen, "generate_with_progress", None)
        if (
            notify is not None
            and callable(gen_with)
            and asyncio.iscoroutinefunction(gen_with)
        ):
            text = await gen_with(
                offer.project,
                context,
                notify=notify,
                examples=examples,
                recent_responses=recent,
                price_hint=price_hint,
                days_hint=days_hint,
                budget_mismatch=gap,
                evidence=evidence,
                project_type=offer.score.suggested_project_type,
            )
        else:
            text = await asyncio.to_thread(
                gen.generate,
                offer.project,
                context,
                examples=examples,
                recent_responses=recent,
                price_hint=price_hint,
                days_hint=days_hint,
                budget_mismatch=gap,
                evidence=evidence,
                project_type=offer.score.suggested_project_type,
            )
        text = finalize_response_text(text.strip(), offer.project)
        text = ensure_budget_mismatch_note(
            text, gap, seed=str(offer.project.project_id or "")
        )
        if listed > 0:
            text = align_response_commercials(
                text, price=listed, delivery_days=days_hint
            )
        if notify is not None and evidence.status != "not_required":
            try:
                await notify(evidence_summary_html(evidence))
            except Exception:
                logger.warning(
                    "evidence_summary_notify_failed project_id=%s",
                    offer.project_id,
                    exc_info=True,
                )
        return text

    async def _ensure_response_text(
        self,
        offer: PendingOffer,
        *,
        notify: Any | None = None,
    ) -> str:
        if (offer.response_text or "").strip():
            text = finalize_response_text(offer.response_text.strip(), offer.project)
            if text != offer.response_text:
                offer.response_text = text
                self.review_service.store.save(offer)
            return text
        text = await self._generate_response_text(offer, notify=notify)
        offer.response_text = text
        self.review_service.store.save(offer)
        return text

    async def handle_approve_click(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        offer: PendingOffer,
        callback: Any | None,
    ) -> None:
        if callback is not None:
            await self.review_service.tg_bot.mark_review_approved(callback)

        async def _progress(msg: str) -> None:
            await self.review_service.tg_bot.notify(msg)

        try:
            if not (offer.response_text or "").strip():
                await self.review_service.tg_bot.notify(
                    f"⏳ Генерирую отклик: {html.escape(offer.title)}"
                )
            await self._ensure_response_text(offer, notify=_progress)
        except httpx.HTTPStatusError as exc:
            offer.status = "pending"
            offer.approved_at = None
            self.review_service.store.save(offer)
            self.repository.update_status(
                platform, source_key, project_id, "pending"
            )
            logger.exception("response_generation_failed project_id=%s", project_id)
            if exc.response.status_code == 429:
                await self.review_service.tg_bot.notify(
                    f"⚠️ OpenAI rate limit: {html.escape(offer.title)}\n"
                    "Нажми «Откликнуться» снова через 1–2 мин."
                )
            else:
                await self.review_service.tg_bot.notify(
                    f"❌ Не удалось сгенерировать текст для формы: "
                    f"{html.escape(offer.title)}\nHTTP {exc.response.status_code}"
                )
            return
        except Exception:
            offer.status = "pending"
            offer.approved_at = None
            self.review_service.store.save(offer)
            self.repository.update_status(
                platform, source_key, project_id, "pending"
            )
            logger.exception("response_generation_failed project_id=%s", project_id)
            await self.review_service.tg_bot.notify(
                f"❌ Не удалось сгенерировать текст для формы: "
                f"{html.escape(offer.title)}"
            )
            return

        link_msg_id = await self.review_service.tg_bot.send_offer_link(offer)
        offer.draft_message_id = link_msg_id
        self.review_service.store.save(offer)

        if is_manual_copy_platform(platform):
            await self._send_manual_copy(offer)
            return

        if self.settings.prepare_only_no_submit:
            await self._prepare_offer_on_site(offer)
        else:
            await self.review_service.tg_bot.notify(
                f"🔗 {kwork_offer_form_url(offer.project_id)}\n"
                "Текст отклика — на форме Kwork."
            )

    async def handle_prepare_retry(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        callback: Any | None = None,
    ) -> None:
        offer = self.review_service.store.load(platform, source_key, project_id)
        if offer is None:
            await self.review_service.tg_bot.notify(
                f"⚠️ Заявка {project_id} не найдена в pending_offers"
            )
            return
        if offer.status in ("pending", "prepared"):
            offer.status = "approved"
            self.review_service.store.save(offer)
        prepared = self.prepared_store.load(platform, source_key, project_id)
        if prepared is not None and (prepared.response_text or "").strip():
            offer.response_text = prepared.response_text
            self.review_service.store.save(offer)
        if is_manual_copy_platform(platform):
            await self._send_manual_copy(offer)
            return
        await self._prepare_offer_on_site(offer)

    async def handle_regenerate_response(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        callback: Any | None = None,
    ) -> None:
        offer = self.review_service.store.load(platform, source_key, project_id)
        if offer is None:
            await self.review_service.tg_bot.notify(
                f"⚠️ Заявка {project_id} не найдена в pending_offers"
            )
            return

        offer.status = "approved"
        offer.response_text = None
        offer.approved_at = datetime.now(timezone.utc)
        self.review_service.store.save(offer)

        async def _progress(msg: str) -> None:
            await self.review_service.tg_bot.notify(msg)

        await self.review_service.tg_bot.notify(
            f"🔄 Перегенерирую отклик: {html.escape(offer.title)}"
        )
        try:
            await self._ensure_response_text(offer, notify=_progress)
        except Exception as exc:
            logger.exception("regenerate_failed project_id=%s", project_id)
            await self.review_service.tg_bot.notify(
                f"❌ Не удалось перегенерировать: {html.escape(str(exc)[:500])}"
            )
            return

        if is_manual_copy_platform(platform):
            await self._send_manual_copy(offer)
            return

        if self.settings.prepare_only_no_submit:
            await self._prepare_offer_on_site(offer)
        else:
            await self.review_service.tg_bot.notify(
                f"🔗 {kwork_offer_form_url(offer.project_id)}\n"
                "Новый текст готов — открой форму на Kwork."
            )

    async def _handle_correct_instruction(
        self,
        message: Any,
        platform: str,
        source_key: str,
        project_id: str,
        text: str,
    ) -> None:
        _ = message
        await self.handle_correct_response(platform, source_key, project_id, text)

    @staticmethod
    def _merge_correct_evidence(
        prior: EvidenceBundle | None,
        new: EvidenceBundle,
        *,
        project_hash: str,
    ) -> EvidenceBundle:
        """Union sources/facts/insights/warnings by id; new wins on conflict."""
        if prior is None or (
            prior.status == "not_required"
            and not prior.sources
            and not prior.facts
        ):
            return new.model_copy(update={"project_hash": project_hash})

        sources_by_id: dict[str, EvidenceSource] = {s.id: s for s in prior.sources}
        for src in new.sources:
            sources_by_id[src.id] = src
        sources = list(sources_by_id.values())

        facts_by_id: dict[str, EvidenceFact] = {f.id: f for f in prior.facts}
        for fact in new.facts:
            facts_by_id[fact.id] = fact
        facts = list(facts_by_id.values())

        insights_by_key: dict[str, EvidenceInsight] = {}
        for insight in [*prior.insights, *new.insights]:
            key = f"{insight.kind}|{insight.finding}|{','.join(insight.fact_ids)}"
            insights_by_key[key] = insight
        insights = list(insights_by_key.values())

        warnings: list[str] = []
        seen_warn: set[str] = set()
        for w in [*prior.warnings, *new.warnings]:
            if w in seen_warn:
                continue
            seen_warn.add(w)
            warnings.append(w)

        required = bool(prior.required or new.required or sources)
        verified = [s for s in sources if s.status == "verified"]
        failed = [s for s in sources if s.status in ("unavailable", "rejected")]
        if not required or not sources:
            status = "not_required"
        elif verified and not failed:
            status = "complete"
        elif verified and failed:
            status = "partial"
        elif failed and not verified:
            status = "failed"
        else:
            status = new.status if new.status != "not_required" else "partial"

        return EvidenceBundle(
            status=status,  # type: ignore[arg-type]
            required=required,
            project_hash=project_hash,
            sources=sources,
            facts=facts,
            insights=insights,
            warnings=warnings,
            collected_at=new.collected_at or prior.collected_at,
        )

    async def handle_correct_response(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        instruction: str,
    ) -> None:
        offer = self.review_service.store.load(platform, source_key, project_id)
        if offer is None:
            self.review_service.clear_correct_awaiting()
            await self.review_service.tg_bot.notify(
                f"⚠️ Заявка {project_id} не найдена в pending_offers"
            )
            return

        current = (offer.response_text or "").strip()
        if not current:
            prepared = self.prepared_store.load(platform, source_key, project_id)
            if prepared is not None:
                current = (prepared.response_text or "").strip()
                if current:
                    offer.response_text = current
                    self.review_service.store.save(offer)
        if not current:
            await self.review_service.tg_bot.notify(
                "⚠️ Сначала сгенерируй отклик (Откликнуть / Перегенерировать)"
            )
            return

        instruction = instruction.strip()
        if not instruction:
            await self.review_service.tg_bot.notify(
                "⚠️ Пустая инструкция — пришли текст правки"
            )
            return

        await self.review_service.tg_bot.notify(
            f"✏️ Правирую отклик: {html.escape(offer.title)}"
        )

        evidence_for_revise = None
        url_cands = extract_url_candidates(
            instruction, max_urls=int(self.settings.evidence_max_urls)
        )
        if url_cands and self.settings.evidence_research_enabled:
            await self.review_service.tg_bot.notify("🔎 Проверяю ссылки…")
            extra = candidates_from_urls(
                [c.input_ref for c in url_cands],
                role="data_source",
            )
            try:
                bundle = await asyncio.to_thread(
                    self.evidence_service.collect,
                    offer.project,
                    settings=self.settings,
                    timeout=float(self.settings.evidence_timeout_seconds),
                    max_urls=int(self.settings.evidence_max_urls),
                    extra_candidates=extra,
                )
            except Exception as exc:
                logger.exception(
                    "correct_evidence_collect_failed project_id=%s", project_id
                )
                await self.review_service.tg_bot.notify(
                    f"⚠️ Не удалось проверить ссылки: {html.escape(str(exc)[:300])}"
                )
                bundle = None
            if bundle is not None:
                current_hash = project_content_hash(offer.project)
                offer.evidence = self._merge_correct_evidence(
                    offer.evidence, bundle, project_hash=current_hash
                )
                self.review_service.store.save(offer)
                evidence_for_revise = offer.evidence
                if bundle.status != "not_required":
                    try:
                        await self.review_service.tg_bot.notify(
                            evidence_summary_html(bundle)
                        )
                    except Exception:
                        logger.warning(
                            "correct_evidence_summary_notify_failed project_id=%s",
                            project_id,
                            exc_info=True,
                        )

        try:
            revised = await asyncio.to_thread(
                self.response_generator.revise,
                offer.project,
                current,
                instruction,
                evidence=evidence_for_revise,
            )
        except Exception as exc:
            logger.exception("correct_revise_failed project_id=%s", project_id)
            await self.review_service.tg_bot.notify(
                f"❌ Не удалось скорректировать: {html.escape(str(exc)[:500])}"
            )
            return

        if evidence_for_revise is not None:
            usage_issues = evidence_usage_issues(revised or "", evidence_for_revise)
            if usage_issues:
                retry_instruction = (
                    f"{instruction}\nmust_fix: {'; '.join(usage_issues)}"
                )
                try:
                    revised = await asyncio.to_thread(
                        self.response_generator.revise,
                        offer.project,
                        current,
                        retry_instruction,
                        evidence=evidence_for_revise,
                    )
                except Exception:
                    logger.warning(
                        "correct_revise_usage_retry_failed project_id=%s",
                        project_id,
                        exc_info=True,
                    )

        revised_raw = (revised or "").strip()
        if not revised_raw:
            await self.review_service.tg_bot.notify(
                "❌ Модель вернула пустой текст — исходный отклик не изменён"
            )
            return

        offer.response_text = finalize_response_text(revised_raw, offer.project)
        self.review_service.store.save(offer)

        if is_manual_copy_platform(platform):
            await self._send_manual_copy(offer, skip_checklist_enrich=True)
            return

        if self.settings.prepare_only_no_submit:
            await self._prepare_offer_on_site(offer, skip_checklist_enrich=True)
            return

        await self.review_service.tg_bot.notify(
            f"🔗 {kwork_offer_form_url(offer.project_id)}\n"
            "Текст обновлён — открой форму на Kwork."
        )

    async def handle_user_response_text(
        self, offer: PendingOffer, text: str
    ) -> None:
        if offer.status not in ("approved",):
            await self.review_service.tg_bot.notify(
                f"⚠️ Проект {offer.project_id} уже в статусе {offer.status}"
            )
            return

        offer.response_text = finalize_response_text(text.strip(), offer.project)
        self.review_service.store.save(offer)

        if is_manual_copy_platform(offer.platform):
            await self._send_manual_copy(offer)
            return

        if self.settings.prepare_only_no_submit:
            await self._prepare_offer_on_site(offer)
            return

        await self.handle_approved(
            offer.platform,
            offer.source_key,
            offer.project_id,
            offer,
        )

    def _scope_volume(self, offer: PendingOffer) -> int | None:
        return catalog_item_count_from_evidence(getattr(offer, "evidence", None))

    def _scoped_delivery_days(
        self, offer: PendingOffer, terms_days: int
    ) -> int:
        scope_days = suggest_delivery_days_for_scope(
            offer.project, volume=self._scope_volume(offer)
        )
        days = int(terms_days or 0)
        if scope_days is not None:
            days = max(days, int(scope_days)) if days > 0 else int(scope_days)
        tier = offer.acceptance_tier or resolve_acceptance_tier(
            offer.project, offer.score, self.settings
        )
        if tier in ("quick_win", "experience_win"):
            days = min(days, self.settings.quick_win_max_delivery_days)
        return days

    @staticmethod
    def _align_offer_commercials(
        text: str,
        *,
        price: int,
        delivery_days: int,
        gap: dict | None,
        seed: str | None = None,
    ) -> str:
        body = ensure_budget_mismatch_note(text, gap, seed=seed)
        body = align_response_commercials(
            body, price=int(price), delivery_days=int(delivery_days)
        )
        # Align may append Стоимость/Срок — re-break into readable paragraphs.
        return ensure_response_paragraphs(body)

    def _resolve_listed_fill(
        self,
        project: ProjectFull,
        *,
        fair_price: int = 0,
        offer_price: int = 0,
        volume: int | None = None,
        response_text: str | None = None,
    ) -> tuple[int, dict | None, int]:
        gap_fair = int(fair_price or 0) or int(offer_price or 0)
        mix = listed_price_mix_for_scope(project, volume=volume)
        gap = (
            budget_gap(gap_fair, project, mix=mix) if gap_fair > 0 else None
        )
        listed = pick_listed_offer_price(
            project, fair=int(fair_price or 0), mix=mix
        )
        if project_has_listed_budget(project):
            listed = prefer_response_price_when_no_budget(
                project, listed, response_text or ""
            )
        else:
            response_price = prefer_response_price_when_no_budget(
                project, 0, response_text or ""
            )
            listed = (
                response_price
                or max(0, int(offer_price or 0))
                or listed
                or max(0, int(fair_price or 0))
            )
        if gap is not None:
            gap = dict(gap)
            gap["fill_price"] = int(listed)
        return listed, gap, gap_fair

    async def _send_manual_copy(
        self,
        offer: PendingOffer,
        *,
        skip_checklist_enrich: bool = False,
    ) -> None:
        """Manual-copy platforms: text + estimate to TG, no site prepare/submit."""
        platform = offer.platform
        response_text = await self._ensure_response_text(offer)
        await self._refresh_offer_project(offer)
        context = await asyncio.to_thread(self.lightrag.get_full_context)

        soft_limits = {
            "yandex_uslugi": int(self.settings.yandex_max_daily_responses or 7),
            "flru": int(self.settings.flru_max_daily_responses or 10),
        }
        platform_labels = {
            "yandex_uslugi": "Яндекс Услуги",
            "flru": "FL.ru",
        }
        today_count = count_today_platform_prepared(self.prepared_store, platform)
        soft_limit = soft_limits.get(platform, 10)
        label = platform_labels.get(platform, platform)
        if today_count >= soft_limit:
            await self.review_service.tg_bot.notify(
                f"⚠️ {label}: сегодня уже ~{today_count} откликов "
                f"(мягкий лимит {soft_limit}/сутки). "
                "Генерирую всё равно — копируй вручную осторожно."
            )

        terms = await asyncio.to_thread(
            self.offer_estimator.estimate,
            offer.project,
            response_text,
            lightrag_context=context,
        )
        fair_price = 0
        try:
            fair_price = int(
                await asyncio.to_thread(
                    self.offer_estimator.estimate_market_cost,
                    offer.project,
                    context,
                )
            )
        except Exception:
            logger.exception(
                "manual_copy_fair_price_failed project_id=%s platform=%s",
                offer.project_id,
                platform,
            )
        offer_price = int(terms.price_rub or 0)
        volume = self._scope_volume(offer)
        price_rub, gap, _gap_fair = self._resolve_listed_fill(
            offer.project,
            fair_price=fair_price,
            offer_price=offer_price,
            volume=volume,
            response_text=response_text,
        )
        delivery_days = self._scoped_delivery_days(offer, terms.delivery_days)

        if not skip_checklist_enrich:
            response_text = append_missing_checklist_answers(
                response_text,
                offer.project,
                price_rub=price_rub,
                delivery_days=delivery_days,
            )
        response_text = self._align_offer_commercials(
            response_text,
            price=price_rub,
            delivery_days=delivery_days,
            gap=gap,
            seed=str(offer.project_id or ""),
        )
        if response_text != (offer.response_text or ""):
            offer.response_text = response_text
            self.review_service.store.save(offer)

        price = str(price_rub)
        await self._save_prepared_response(
            offer, response_text, price, delivery_days
        )
        if platform == "yandex_uslugi":
            project_url = offer.url or yandex_order_url(offer.project_id)
        elif platform == "flru":
            project_url = offer.url or flru_project_url(offer.project_id)
        else:
            project_url = offer.url or ""
        await self.review_service.tg_bot.send_manual_copy(
            offer,
            response_text=response_text,
            price=price,
            delivery_days=delivery_days,
            project_url=project_url,
            soft_limit=soft_limit,
        )

    async def _prepare_offer_on_site(
        self,
        offer: PendingOffer,
        *,
        skip_checklist_enrich: bool = False,
    ) -> None:
        source = self._resolve_source_config(offer.source_key)
        if source is None:
            await self.review_service.tg_bot.notify(
                f"⚠️ Источник {offer.source_key} не найден"
            )
            return

        response_text = await self._ensure_response_text(offer)
        await self._refresh_offer_project(offer)
        context = await asyncio.to_thread(self.lightrag.get_full_context)
        terms = await asyncio.to_thread(
            self.offer_estimator.estimate,
            offer.project,
            response_text,
            lightrag_context=context,
        )
        fair_price = 0
        try:
            fair_price = int(
                await asyncio.to_thread(
                    self.offer_estimator.estimate_market_cost,
                    offer.project,
                    context,
                )
            )
        except Exception:
            logger.exception(
                "fair_price_estimate_failed project_id=%s", offer.project_id
            )
        offer_price = int(terms.price_rub or 0)
        volume = self._scope_volume(offer)
        fill_price, gap, gap_fair = self._resolve_listed_fill(
            offer.project,
            fair_price=fair_price,
            offer_price=offer_price,
            volume=volume,
            response_text=response_text,
        )
        price = str(fill_price)
        delivery_days = self._scoped_delivery_days(offer, terms.delivery_days)
        if not skip_checklist_enrich:
            response_text = append_missing_checklist_answers(
                response_text,
                offer.project,
                price_rub=fill_price,
                delivery_days=delivery_days,
            )
        response_text = self._align_offer_commercials(
            response_text,
            price=fill_price,
            delivery_days=delivery_days,
            gap=gap,
            seed=str(offer.project_id or ""),
        )
        if response_text != (offer.response_text or ""):
            offer.response_text = response_text
            self.review_service.store.save(offer)

        def _run_prepare(text: str, price_val: str, days: int):
            browser = get_browser_client(self.settings)
            try:
                adapter = self._adapter_factory(source, browser)
                if not hasattr(adapter, "prepare_response"):
                    raise RuntimeError("Adapter does not support prepare_response")
                return adapter.prepare_response(
                    offer.project_id,
                    text,
                    price_val,
                    delivery_days=days,
                    order_title=offer.title or offer.project.title,
                    project=offer.project,
                )
            finally:
                close_browser_client(browser)

        max_attempts = 2
        result = None
        last_msg = ""
        try:
            ceiling = (
                int(gap["ceiling"])
                if gap
                else parse_budget_ceiling_rub(offer.project)
            )
            # Gap: fair as полный объём; без gap — listed в коридоре заказа.
            notify_price = gap_fair if gap else fill_price
            if gap and ceiling is not None:
                estimate_line = (
                    f"Оценка полный объём: {format_rub_amount(notify_price)} ₽ "
                    f"(потолок {format_rub_amount(ceiling)}) · "
                    f"{delivery_days} дн."
                )
            else:
                estimate_line = (
                    f"Оценка: {format_rub_amount(notify_price)} ₽ "
                    f"· {delivery_days} дн."
                )
            await self.review_service.tg_bot.notify(
                f"⏳ Готовлю отклик: {html.escape(offer.title)}\n{estimate_line}"
            )
            for attempt in range(max_attempts):
                if attempt > 0:
                    form_only = any(token in last_msg for token in _PREPARE_FORM_ONLY_RETRY)
                    if form_only:
                        await self.review_service.tg_bot.notify(
                            f"🔄 Повтор {attempt + 1}/{max_attempts}: "
                            f"заполнение формы (без GPT, {price} ₽ · {delivery_days} дн.)…"
                        )
                    else:
                        await self.review_service.tg_bot.notify(
                            f"🔄 Повтор {attempt + 1}/{max_attempts}: "
                            f"перегенерация GPT и заполнение формы…"
                        )
                        offer.response_text = ""
                        self.review_service.store.save(offer)
                        async def _progress(msg: str) -> None:
                            await self.review_service.tg_bot.notify(msg)

                        response_text = await self._generate_response_text(
                            offer, notify=_progress
                        )
                        offer.response_text = response_text
                        self.review_service.store.save(offer)
                        context = await asyncio.to_thread(self.lightrag.get_full_context)
                        terms = await asyncio.to_thread(
                            self.offer_estimator.estimate,
                            offer.project,
                            response_text,
                            lightrag_context=context,
                        )
                        fair_price = 0
                        try:
                            fair_price = int(
                                await asyncio.to_thread(
                                    self.offer_estimator.estimate_market_cost,
                                    offer.project,
                                    context,
                                )
                            )
                        except Exception:
                            logger.exception(
                                "fair_price_estimate_failed project_id=%s",
                                offer.project_id,
                            )
                        offer_price = int(terms.price_rub or 0)
                        volume = self._scope_volume(offer)
                        fill_price, gap, gap_fair = self._resolve_listed_fill(
                            offer.project,
                            fair_price=fair_price,
                            offer_price=offer_price,
                            volume=volume,
                            response_text=response_text,
                        )
                        price = str(fill_price)
                        delivery_days = self._scoped_delivery_days(
                            offer, terms.delivery_days
                        )
                        if not skip_checklist_enrich:
                            response_text = append_missing_checklist_answers(
                                response_text,
                                offer.project,
                                price_rub=fill_price,
                                delivery_days=delivery_days,
                            )
                        response_text = self._align_offer_commercials(
                            response_text,
                            price=fill_price,
                            delivery_days=delivery_days,
                            gap=gap,
                            seed=str(offer.project_id or ""),
                        )
                        offer.response_text = response_text
                        self.review_service.store.save(offer)

                result = await asyncio.to_thread(
                    _run_prepare, response_text, price, delivery_days
                )
                last_msg = result.message or ""
                if result.success:
                    break
                msg = last_msg
                if any(
                    token in msg
                    for token in (
                        "not_logged_in",
                        "offer_already_submitted",
                        "offer_form_unavailable",
                    )
                ):
                    break
        except Exception as exc:
            logger.exception("prepare_failed project_id=%s", offer.project_id)
            await self._save_prepared_response(
                offer,
                response_text,
                price,
                delivery_days,
                lock_offer=False,
            )
            await self.review_service.tg_bot.send_prepare_retry(
                offer,
                error=f"Браузер упал: {exc}",
            )
            return

        if not result.success:
            await self._save_prepared_response(
                offer,
                response_text,
                price,
                delivery_days,
                lock_offer=False,
            )
            msg = result.message or "unknown"
            if "not_logged_in" in msg:
                await self.review_service.tg_bot.notify(
                    f"⚠️ Kwork не залогинен на VPS — форма не заполнена\n"
                    f"Отклик сохранён в prepared_responses/{offer.project_id}\n"
                    f"Нужен: deploy/kwork_save_session.py или KWORK_AUTO_LOGIN=true"
                )
            elif "offer_already_submitted" in msg:
                await self.review_service.tg_bot.notify(
                    f"ℹ️ <b>Отклик уже отправлен</b>: {html.escape(offer.title)}\n"
                    f"Проект {offer.project_id} есть в "
                    f"<a href=\"https://kwork.ru/offers\">Мои отклики</a>.\n"
                    "Kwork больше не открывает форму new_offer — повторно заполнить нельзя.\n"
                    "Если нужно изменить предложение — только вручную на Kwork."
                )
            elif "offer_form_unavailable" in msg:
                await self.review_service.tg_bot.notify(
                    f"⚠️ Форма отклика недоступна: {html.escape(offer.title)}\n"
                    f"{html.escape(msg)}"
                )
            else:
                await self.review_service.tg_bot.send_prepare_retry(
                    offer,
                    error=msg,
                )
            return

        await self._save_prepared_response(
            offer, response_text, price, delivery_days
        )
        deadline_manual = bool(
            result.message and "deadline_not_set" in result.message
        )
        offer_url = kwork_offer_form_url(offer.project_id)
        await self.review_service.tg_bot.send_form_prepared_ready(
            offer,
            price=price,
            delivery_days=delivery_days,
            offer_url=offer_url,
            deadline_manual=deadline_manual,
        )

    async def handle_journal_confirm(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        callback: Any,
    ) -> None:
        item = self.prepared_store.load(platform, source_key, project_id)
        if item is None:
            await callback.answer("Отклик не найден", show_alert=True)
            return
        if item.journal_confirmed:
            try:
                await self.review_service.tg_bot.mark_journal_confirmed(callback)
            except Exception:
                logger.exception(
                    "journal_confirm_mark_ui_failed project_id=%s", project_id
                )
            await callback.answer("Уже в журнале", show_alert=True)
            return
        if item.project_id in self.journal.project_ids_in_journal():
            item.journal_confirmed = True
            item.journal_exported = True
            self.prepared_store.save(item)
            try:
                await self.review_service.tg_bot.mark_journal_confirmed(callback)
            except Exception:
                logger.exception(
                    "journal_confirm_mark_ui_failed project_id=%s", project_id
                )
            await callback.answer("Уже в журнале", show_alert=True)
            return

        item.journal_confirmed = True
        self.prepared_store.save(item)

        response_text = item.response_text
        price = item.price
        delivery_days = item.delivery_days
        flru_guest_fallback = False
        if platform == "kwork":
            try:
                snap = await asyncio.to_thread(
                    self._fetch_submitted_offer_text, project_id
                )
            except Exception as exc:
                logger.warning(
                    "journal_confirm_kwork_read_failed project_id=%s err=%s",
                    project_id,
                    exc,
                )
                snap = OfferFormSnapshot(
                    description="", ok=False, error=f"read_exception: {exc}"
                )
            if snap.ok and (snap.description or "").strip():
                response_text = snap.description.strip()
                if snap.price:
                    price = snap.price
                if snap.delivery_days is not None:
                    delivery_days = snap.delivery_days
                item.response_text = response_text
                item.price = price
                item.delivery_days = delivery_days
                self.prepared_store.save(item)
                logger.info(
                    "journal_confirm_used_kwork_text project_id=%s len=%d",
                    project_id,
                    len(response_text),
                )
            elif snap.error:
                logger.warning(
                    "journal_confirm_fallback_prepared project_id=%s err=%s",
                    project_id,
                    snap.error,
                )
        elif platform == "yandex_uslugi":
            try:
                ysnap = await asyncio.to_thread(
                    self._fetch_yandex_submitted_offer, project_id
                )
            except Exception as exc:
                logger.warning(
                    "journal_confirm_yandex_read_failed project_id=%s err=%s",
                    project_id,
                    exc,
                )
                from src.adapters.yandex_uslugi import YandexSubmittedOffer

                ysnap = YandexSubmittedOffer(
                    ok=False, error=f"read_exception: {exc}"
                )
            if ysnap.ok and (ysnap.description or "").strip():
                response_text = ysnap.description.strip()
                if ysnap.price:
                    price = ysnap.price
                item.response_text = response_text
                item.price = price
                self.prepared_store.save(item)
                logger.info(
                    "journal_confirm_used_yandex_text project_id=%s len=%d",
                    project_id,
                    len(response_text),
                )
            elif ysnap.error:
                logger.warning(
                    "journal_confirm_yandex_fallback_prepared project_id=%s err=%s",
                    project_id,
                    ysnap.error,
                )
        elif platform == "flru":
            try:
                fsnap = await asyncio.to_thread(
                    self._fetch_flru_submitted_offer, project_id
                )
            except Exception as exc:
                logger.warning(
                    "journal_confirm_flru_read_failed project_id=%s err=%s",
                    project_id,
                    exc,
                )
                from src.adapters.flru import FlruSubmittedOffer

                fsnap = FlruSubmittedOffer(
                    ok=False, error=f"read_exception: {exc}"
                )
            if fsnap.ok and (fsnap.description or "").strip():
                response_text = fsnap.description.strip()
                if fsnap.price:
                    price = fsnap.price
                if fsnap.delivery_days is not None:
                    delivery_days = fsnap.delivery_days
                item.response_text = response_text
                item.price = price
                item.delivery_days = delivery_days
                self.prepared_store.save(item)
                logger.info(
                    "journal_confirm_used_flru_text project_id=%s len=%d",
                    project_id,
                    len(response_text),
                )
            elif fsnap.error:
                err = str(fsnap.error)
                logger.warning(
                    "journal_confirm_flru_fallback_prepared project_id=%s err=%s",
                    project_id,
                    err,
                )
                if "not_logged_in" in err:
                    flru_guest_fallback = True
                    logger.warning(
                        "journal_confirm_flru_not_logged_in project_id=%s "
                        "hint=deploy/flru_login_interactive.py",
                        project_id,
                    )

        journal_status, journal_result = journal_status_for_confirm(platform)
        try:
            row = self.journal.append_prepared(
                item.project,
                item.score,
                response_text,
                price=price,
                delivery_days=delivery_days,
                status=journal_status,
                result=journal_result,
            )
        except Exception as exc:
            item.journal_confirmed = False
            self.prepared_store.save(item)
            logger.exception("journal_confirm_failed project_id=%s", project_id)
            await callback.answer("Ошибка записи в Excel", show_alert=True)
            await self.review_service.tg_bot.notify(
                f"⚠️ Не удалось записать в журнал: {html.escape(str(exc))}"
            )
            return

        item.journal_exported = True
        self.prepared_store.save(item)

        try:
            offer = self.review_service.store.load(platform, source_key, project_id)
        except Exception:
            logger.exception(
                "journal_confirm_pending_load_failed project_id=%s", project_id
            )
            offer = None
        if offer is not None:
            offer.status = "submitted"
            self.review_service.store.save(offer)
        try:
            self.repository.update_status(
                platform, source_key, project_id, "submitted"
            )
        except Exception:
            logger.exception(
                "journal_confirm_status_update_failed project_id=%s", project_id
            )

        try:
            await self.review_service.tg_bot.mark_journal_confirmed(callback)
        except Exception:
            logger.exception(
                "journal_confirm_mark_ui_failed project_id=%s", project_id
            )
        await callback.answer("Записано в журнал", show_alert=True)
        journal_note = (
            f"📒 Журнал: строка {row} · {html.escape(item.title)}\n"
            "На ПК: /journal в TG — пришлёт актуальный journal.xlsx с VPS."
        )
        if flru_guest_fallback:
            journal_note += (
                "\n⚠️ FL.ru guest/not_logged_in — записан prepared-черновик. "
                "Обнови сессию: deploy/flru_login_interactive.py"
            )
        await self.review_service.tg_bot.notify(journal_note)
        logger.info("journal_confirmed project_id=%s row=%s", project_id, row)

    def _fetch_submitted_offer_text(self, project_id: str) -> OfferFormSnapshot:
        storage = (self.settings.kwork_storage_state or "").strip() or None
        browser = get_browser_client(self.settings, storage_state_path=storage)
        try:
            if pair := self.settings.kwork_credentials():
                if not is_logged_in(browser):
                    ensure_logged_in(
                        browser, KworkCredentials(login=pair[0], password=pair[1])
                    )
            return read_submitted_offer_text(browser, project_id)
        finally:
            close_browser_client(browser)

    def _fetch_yandex_submitted_offer(self, order_id: str):
        from src.adapters.yandex_uslugi import (
            read_submitted_offer_text as read_yandex_submitted_offer,
        )

        storage = (self.settings.yandex_storage_state or "").strip() or None
        browser = get_browser_client(self.settings, storage_state_path=storage)
        try:
            return read_yandex_submitted_offer(browser, order_id)
        finally:
            close_browser_client(browser)

    def _fetch_flru_submitted_offer(self, project_id: str):
        from src.adapters.flru import (
            read_submitted_offer_text as read_flru_submitted_offer,
        )

        storage = (self.settings.flru_storage_state or "").strip() or None
        browser = get_browser_client(self.settings, storage_state_path=storage)
        try:
            return read_flru_submitted_offer(browser, project_id)
        finally:
            close_browser_client(browser)

    async def _save_prepared_response(
        self,
        offer: PendingOffer,
        response_text: str,
        price: str,
        delivery_days: int,
        *,
        lock_offer: bool = True,
    ) -> None:
        prepared = PreparedResponse(
            platform=offer.platform,
            source_key=offer.source_key,
            project_id=offer.project_id,
            url=offer.url,
            title=offer.title,
            project=offer.project,
            score=offer.score,
            response_text=response_text,
            price=price,
            delivery_days=delivery_days,
            screenshot_path=None,
            evidence=offer.evidence,
        )
        self.prepared_store.save(prepared)
        if lock_offer:
            offer.status = "prepared"
            self.review_service.store.save(offer)
            self.repository.update_status(
                offer.platform, offer.source_key, offer.project_id, "prepared"
            )

    async def export_prepared_to_journal(self, message: Any = None) -> int:
        async with self._journal_sync_lock:
            try:
                sync = await asyncio.to_thread(
                    sync_journal_on_vps,
                    settings=self.settings,
                    writer=self.journal,
                    prepared_store=self.prepared_store,
                )
            except Exception as exc:
                logger.exception("journal_export_failed")
                await self.review_service.tg_bot.notify(
                    f"⚠️ Ошибка Excel: {html.escape(str(exc))}"
                )
                return 0

            caption = (
                f"📒 Журнал обновлён\n"
                f"Prepared добавлено: {sync.appended_prepared}\n"
                f"Notes обновлено: {sync.updated_notes}\n"
                f"Offers обновлено: {sync.offers_updated}\n"
                f"Offers добавлено: {sync.offers_appended}"
            )
            if sync.offers_error:
                caption += f"\n⚠️ Offers sync: {sync.offers_error}"
            try:
                await self.review_service.tg_bot.send_document(
                    self.settings.response_journal,
                    caption=caption[:1000],
                )
            except Exception as exc:
                logger.exception("journal_send_file_failed")
                await self.review_service.tg_bot.notify(
                    f"⚠️ Журнал обновлён, но отправка файла не удалась: {html.escape(str(exc))}\n"
                    f"Файл: {html.escape(self.settings.response_journal)}"
                )
            return sync.appended_prepared + sync.offers_appended

    async def send_scan_report(self, message: Any = None) -> None:
        reports = self.scan_reports.list_recent(limit=3)
        text = format_scan_reports_message(
            reports,
            timezone_name=self.settings.operator_timezone,
            limit=3,
        )
        await self.review_service.tg_bot.notify(text)

    async def check_flru_health(
        self, message: Any, *, repair: bool = False
    ) -> None:
        sources = [
            source
            for source in get_enabled_sources(self.settings.sources_config_path)
            if source.platform == "flru"
        ]
        if not sources:
            await message.answer("⚠️ FL.ru: нет включённых источников")
            return
        if self._flru_scan_lock.locked():
            await message.answer(
                "⏳ FL.ru: проверка уже выполняется плановым сканом или /flcheck"
            )
            return

        started = time.perf_counter()
        proxy_enabled = bool(
            (self.settings.flru_proxy or "").strip()
            or (self.settings.tg_user_proxy or "").strip()
        )
        results: list[str] = []
        all_ok = True
        stripped = 0
        cards_total = 0

        async with self._flru_scan_lock:
            if repair:
                await message.answer(
                    "🔧 FL.ru: recovery — strip __ddg + auth scan…"
                )
                storage = (self.settings.flru_storage_state or "").strip()
                if storage:
                    stripped = await asyncio.to_thread(
                        persist_strip_ddos_guard_cookies, storage
                    )
                logger.info(
                    "flru_repair_strip_ddg dropped=%d path=%s",
                    stripped,
                    storage or "(empty)",
                )
            else:
                await message.answer(
                    "🔎 FL.ru: проверяю auth_fetch и early scan…"
                )
            for source in sources:
                source_started = time.perf_counter()
                try:
                    previews = await asyncio.to_thread(
                        self._scan_listings,
                        source,
                        allow_guest_fallback=False,
                    )
                except Exception as exc:
                    all_ok = False
                    category = _classify_flru_check_error(exc)
                    detail = str(exc).splitlines()[0].strip() or exc.__class__.__name__
                    logger.warning(
                        "flru_manual_check_failed source=%s category=%s err=%s",
                        source.id,
                        category,
                        detail[:300],
                    )
                    results.append(
                        f"❌ {html.escape(source.id)}: {category}\n"
                        f"auth_fetch/early_scan: FAIL\n"
                        f"{html.escape(detail[:300])}"
                    )
                    continue

                elapsed = time.perf_counter() - source_started
                cards_total += len(previews)
                via = getattr(self, "_last_flru_scan_via", None)
                via_part = f" · via={via}" if via else ""
                results.append(
                    f"✅ {html.escape(source.id)}: источник жив\n"
                    f"auth_fetch: OK · early_scan: OK · "
                    f"карточек: {len(previews)}{via_part}\n"
                    f"время: {elapsed:.1f} с"
                )

        if repair and all_ok:
            mark_platform_up(self.settings, "flru")

        if repair:
            status = (
                "✅ FL.ru recovery OK" if all_ok else "❌ FL.ru recovery FAIL"
            )
            recovery_line = f"recovery: strip __ddg × {stripped}\n"
        else:
            status = (
                "✅ FL.ru жив" if all_ok else "❌ FL.ru проверка не пройдена"
            )
            recovery_line = ""
        route = "proxy/SOCKS" if proxy_enabled else "direct"
        total = time.perf_counter() - started
        await message.answer(
            f"{status}\n"
            f"{recovery_line}"
            f"маршрут: {route} · cards={cards_total} · всего: {total:.1f} с\n\n"
            + "\n\n".join(results)
        )

    async def request_notebook_sync(self, message: Any, days: int) -> None:
        """VPS side of Bridge A: write job JSON for local Google watcher."""
        n_days = max(1, min(int(days), 90))
        job_path = Path(self.settings.notebook_job_path)
        if not job_path.is_absolute():
            job_path = Path.cwd() / job_path
        job = {
            "n_days": n_days,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "chat_id": str(self.settings.telegram_chat_id),
            "request_id": uuid.uuid4().hex[:12],
        }
        try:
            job_path.parent.mkdir(parents=True, exist_ok=True)
            job_path.write_text(
                json.dumps(job, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.exception("notebook_job_write_failed")
            await message.answer(f"⚠️ Не удалось записать notebook job: {exc}")
            return
        logger.info(
            "notebook_job_written path=%s days=%s request_id=%s",
            job_path,
            n_days,
            job["request_id"],
        )
        await message.answer(
            f"Принято: notebook sync за {n_days} дней — "
            "watcher скачает journal с VPS и обновит Google Sheet"
        )

    async def handle_approved(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        offer: PendingOffer,
    ) -> None:
        if not self.settings.require_telegram_approval and offer.status != "approved":
            offer.status = "approved"

        if self.settings.require_telegram_approval and offer.status != "approved":
            logger.warning("submit_blocked: no approval project_id=%s", project_id)
            return

        if is_daily_limit_reached(
            self.settings.response_journal,
            self.settings.max_daily_responses,
        ):
            await self.review_service.tg_bot.notify(
                f"⚠️ Дневной лимит откликов ({self.settings.max_daily_responses}) достигнут"
            )
            return

        project = offer.project
        response_text = (offer.response_text or "").strip()
        if not response_text:
            await self.review_service.tg_bot.notify(
                f"⚠️ Пустой текст отклика для {project_id}"
            )
            return

        source = self._resolve_source_config(source_key)
        if source is None:
            logger.warning(
                "submit_blocked: unknown source_key=%s project_id=%s",
                source_key,
                project_id,
            )
            await self.review_service.tg_bot.notify(
                f"⚠️ Источник {source_key} не найден, отклик не отправлен: {project_id}"
            )
            return

        adapter = self._adapter_factory(source)
        price = project.desired_budget
        result = adapter.submit_response(project_id, response_text, price)

        if result.success:
            offer.status = "submitted"
            self.review_service.store.save(offer)
            self.repository.update_status(platform, source_key, project_id, "submitted")
            self.journal.append_submission(project, offer.score, response_text)
            await self.review_service.tg_bot.notify(
                f"✅ Отклик отправлен: {project.title}\n{project.url}"
            )
        else:
            await self.review_service.tg_bot.notify(
                f"❌ Ошибка отклика: {project.title}\n{result.message or 'unknown'}"
            )

    async def handle_manual_kwork_project(self, message: Any, project_id: str) -> None:
        await message.answer(
            f"🔍 Загружаю Kwork-проект <code>{html.escape(project_id)}</code>…",
            parse_mode="HTML",
        )
        await self.process_manual_kwork_project(project_id)

    async def process_manual_yandex_order(self, order_id: str) -> dict[str, str]:
        yandex_source = self._first_yandex_source()
        if yandex_source is None:
            raise RuntimeError(
                "Нет включённого источника yandex_uslugi в config/sources.yaml"
            )
        oid = order_id.lower().strip()
        preview = ProjectPreview(
            platform="yandex_uslugi",
            source_key=self.YANDEX_MANUAL_SOURCE_KEY,
            project_id=oid,
            url=yandex_order_url(oid),
            title="",
        )
        if not self.repository.is_known(
            preview.platform, preview.source_key, preview.project_id
        ):
            self.repository.insert_new(
                platform=preview.platform,
                source_key=preview.source_key,
                project_id=preview.project_id,
                url=preview.url,
                status="new",
            )
        try:
            outcome = await self._process_new_project(
                yandex_source, preview, manual=True
            )
        except Exception as exc:
            logger.exception("manual_yandex_order_failed project_id=%s", oid)
            await self.review_service.tg_bot.notify(
                f"⚠️ Не удалось обработать заказ Яндекс {oid}: {exc}"
            )
            return {"project_id": oid, "outcome": "error"}
        await self._notify_manual_outcome(
            platform_label="Яндекс Услуги",
            project_id=oid,
            url=preview.url or "",
            outcome=str(outcome),
        )
        return {"project_id": oid, "outcome": outcome}

    async def process_manual_flru_project(self, project_id: str) -> dict[str, str]:
        flru_source = self._first_flru_source()
        if flru_source is None:
            raise RuntimeError(
                "Нет включённого источника flru в config/sources.yaml"
            )
        pid = str(project_id).strip()
        preview = ProjectPreview(
            platform="flru",
            source_key=self.FLRU_MANUAL_SOURCE_KEY,
            project_id=pid,
            url=flru_project_url(pid),
            title="",
        )
        if not self.repository.is_known(
            preview.platform, preview.source_key, preview.project_id
        ):
            self.repository.insert_new(
                platform=preview.platform,
                source_key=preview.source_key,
                project_id=preview.project_id,
                url=preview.url,
                status="new",
            )
        try:
            outcome = await self._process_new_project(
                flru_source, preview, manual=True
            )
        except Exception as exc:
            logger.exception("manual_flru_project_failed project_id=%s", pid)
            await self.review_service.tg_bot.notify(
                f"⚠️ Не удалось обработать проект FL.ru {pid}: {exc}"
            )
            return {"project_id": pid, "outcome": "error"}
        await self._notify_manual_outcome(
            platform_label="FL.ru",
            project_id=pid,
            url=preview.url or "",
            outcome=str(outcome),
        )
        return {"project_id": pid, "outcome": outcome}

    async def _notify_manual_outcome(
        self,
        *,
        platform_label: str,
        project_id: str,
        url: str,
        outcome: str,
    ) -> None:
        text = _manual_outcome_tg_text(
            platform_label=platform_label,
            project_id=project_id,
            url=url,
            outcome=outcome,
        )
        if text is None:
            return
        await self.review_service.tg_bot.notify(text)

    async def handle_manual_project(
        self, message: Any, project_id: str, platform: str | None = None
    ) -> None:
        pid = (project_id or "").strip()
        if platform is None:
            platform = self._infer_manual_platform(pid, message)
        if platform == "yandex_uslugi":
            await message.answer(
                f"🔍 Загружаю заказ Яндекс Услуги "
                f"<code>{html.escape(pid)}</code>…",
                parse_mode="HTML",
            )
            await self.process_manual_yandex_order(pid)
            return
        if platform == "flru":
            await message.answer(
                f"🔍 Загружаю проект FL.ru <code>{html.escape(pid)}</code>…",
                parse_mode="HTML",
            )
            await self.process_manual_flru_project(pid)
            return
        await self.handle_manual_kwork_project(message, pid)

    async def handle_force_project(
        self, message: Any, project_id: str, platform: str | None = None
    ) -> None:
        pid = (project_id or "").strip()
        platform = platform or self._infer_manual_platform(pid, message)
        if platform == "yandex_uslugi":
            await message.answer(
                f"⚡ Force: гружу заказ Яндекс "
                f"<code>{html.escape(pid)}</code>… мимо фильтра",
                parse_mode="HTML",
            )
            await self.process_manual_yandex_order(pid)
            return
        if platform == "flru":
            await message.answer(
                f"⚡ Force: гружу FL.ru "
                f"<code>{html.escape(pid)}</code>… мимо фильтра",
                parse_mode="HTML",
            )
            await self.process_manual_flru_project(pid)
            return
        await message.answer(
            f"⚡ Force: гружу Kwork "
            f"<code>{html.escape(pid)}</code>… мимо фильтра",
            parse_mode="HTML",
        )
        await self.process_manual_kwork_project(pid)

    async def handle_kwork_pause(self, message: Any, paused: bool) -> None:
        state = set_kwork_paused(self.settings, paused=paused)
        label = "ВЫКЛ" if state else "ВКЛ"
        await message.answer(f"Kwork авто: {label}")

    def _infer_manual_platform(self, pid: str, message: Any) -> str:
        if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            pid,
            flags=re.I,
        ):
            return "yandex_uslugi"
        if extract_flru_project_id(pid) or (
            pid.isdigit()
            and len(pid) >= 6
            and "fl.ru" in (getattr(message, "text", "") or "")
        ):
            return "flru"
        return "kwork"

    async def handle_manual_tz(self, message: Any, text: str) -> None:
        body = (text or "").strip()
        if len(body) < TZ_MIN_CHARS:
            await message.answer(
                f"Слишком короткий текст (минимум {TZ_MIN_CHARS} символов). "
                "Пришли полное ТЗ: /tz текст_заказа"
            )
            return
        preview_title = body.splitlines()[0][:80] if body else "ТЗ"
        await message.answer(
            f"🔍 Обрабатываю ТЗ: <code>{html.escape(preview_title)}</code>…",
            parse_mode="HTML",
        )
        await self.process_manual_tz_text(body)

    async def process_manual_tz_text(self, text: str) -> dict[str, str]:
        full = build_tz_project(text)
        if not self.repository.is_known(
            full.platform, full.source_key, full.project_id
        ):
            self.repository.insert_new(
                platform=full.platform,
                source_key=full.source_key,
                project_id=full.project_id,
                url=full.url,
                title=full.title,
                status="new",
            )
        try:
            outcome = await self._process_project_full(full, manual=True)
        except Exception as exc:
            logger.exception("manual_tz_failed project_id=%s", full.project_id)
            await self.review_service.tg_bot.notify(
                f"⚠️ Не удалось обработать ТЗ: {html.escape(str(exc)[:300])}"
            )
            return {"project_id": full.project_id, "outcome": "error"}
        if outcome == "extract_fail":
            await self.review_service.tg_bot.notify(
                "⚠️ Текст ТЗ слишком короткий или нечитаемый для анализа."
            )
        elif outcome == "stack_reject":
            await self.review_service.tg_bot.notify(
                f"⏭ Пропущено (не наш стек): {html.escape(full.title)}"
            )
        return {"project_id": full.project_id, "outcome": outcome}


def build_orchestrator(settings: Settings | None = None) -> PipelineOrchestrator:
    settings = settings or get_settings()
    repository = ProjectRepository(settings.database_path)
    store = PendingStore()
    tg_bot = TelegramReviewBot(settings.telegram_bot_token, settings.telegram_chat_id)
    review_service = ReviewService(settings, store, tg_bot, repository)
    scorer = GptScorer(settings)
    response_generator = GptResponseGenerator(settings)
    lightrag = LightRagClient(
        base_url=settings.lightrag_base_url or None,
        api_key=settings.lightrag_api_key,
        github_username=settings.github_username,
        github_token=settings.github_token,
        github_stack_cache=settings.github_stack_cache,
    )
    journal = JournalWriter(settings.response_journal)
    prepared_store = PreparedResponseStore(settings.prepared_responses_dir)
    offer_estimator = GptOfferEstimator(settings)
    evidence_service = EvidenceService()
    return PipelineOrchestrator(
        settings=settings,
        repository=repository,
        review_service=review_service,
        scorer=scorer,
        response_generator=response_generator,
        lightrag=lightrag,
        journal=journal,
        prepared_store=prepared_store,
        offer_estimator=offer_estimator,
        evidence_service=evidence_service,
    )
