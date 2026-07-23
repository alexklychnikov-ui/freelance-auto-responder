from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import httpx

from src.adapters.kwork_pricing import (
    budget_gap,
    clamp_price_to_budget,
    ensure_budget_mismatch_note,
    format_rub_amount,
    parse_budget_ceiling_rub,
    pick_commercial_price,
    price_exceeds_budget_ceiling,
)
from src.adapters.kwork import (
    KworkAdapter,
    OfferFormSnapshot,
    _is_weak_description,
    kwork_offer_form_url,
    merge_preview_into_full,
    read_submitted_offer_text,
)
from src.adapters.kwork_auth import KworkAuthError, KworkCredentials, ensure_logged_in
from src.adapters.flru import FlruAdapter, FlruAuthError
from src.adapters.flru_urls import flru_project_url, extract_flru_project_id
from src.adapters.yandex_urls import yandex_order_url
from src.adapters.yandex_uslugi import YandexAuthError, YandexUslugiAdapter
from src.analyzer.examples_loader import load_response_examples
from src.analyzer.gpt_offer_estimator import GptOfferEstimator
from src.analyzer.gpt_response_generator import GptResponseGenerator
from src.analyzer.response_text import append_missing_checklist_answers, finalize_response_text
from src.analyzer.gpt_scorer import GptScorer
from src.analyzer.lightrag_client import LightRagClient
from src.analyzer.project_brief import build_project_brief
from src.analyzer.project_tier import resolve_acceptance_tier
from src.analyzer.response_history import load_recent_response_context
from src.browser.factory import close_browser_client, get_browser_client
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
from src.models import PendingOffer, ProjectFull, ProjectPreview
from src.responses.prepared_store import PreparedResponse, PreparedResponseStore
from src.store.repository import ProjectRepository
from src.store.scan_reports import ScanCycleStats, ScanReportStore
from src.telegram_bot.bot import TelegramReviewBot
from src.telegram_bot.pending_store import PendingStore
from src.telegram_bot.review_service import ReviewService
from src.telegram_bot.scan_report import format_scan_reports_message

logger = logging.getLogger(__name__)


def _normalize_compare_title(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _titles_differ_substantially(preview_title: str, page_title: str) -> bool:
    a = _normalize_compare_title(preview_title)
    b = _normalize_compare_title(page_title)
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() < 0.35

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
        self._browser = browser
        self._adapter_factory = adapter_factory or self._default_adapter
        self._journal_sync_lock = asyncio.Lock()
        self.scan_reports = ScanReportStore(settings.database_path)
        self.review_service.set_approve_handler(self.handle_approve_click)
        self.review_service.set_journal_confirm_handler(self.handle_journal_confirm)
        self.review_service.set_prepare_retry_handler(self.handle_prepare_retry)
        self.review_service.set_regenerate_handler(self.handle_regenerate_response)
        self.review_service.set_submit_text_handler(self.handle_user_response_text)
        self.review_service.set_export_journal_handler(self.export_prepared_to_journal)
        self.review_service.set_scan_report_handler(self.send_scan_report)
        self.review_service.set_manual_project_handler(self.handle_manual_project)
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

        return {"project_id": project_id, "outcome": outcome}

    async def run_scan_cycle(self) -> dict[str, int]:
        totals = {"seen": 0, "new": 0, "skipped": 0, "scored": 0, "notified": 0}
        cycle_stats = ScanCycleStats()
        self.review_service.expire_stale_pending()

        for source in get_enabled_sources(self.settings.sources_config_path):
            source_stats = ScanCycleStats()
            try:
                previews = await asyncio.to_thread(self._scan_listings, source)
            except KworkAuthError as exc:
                logger.error("Kwork auth failed for %s: %s", source.id, exc)
                await self.review_service.tg_bot.notify(
                    f"⚠️ Kwork login failed ({source.id}): {exc}"
                )
                continue
            except YandexAuthError as exc:
                # Soft fail: no crash loop. not_logged_in → log only (avoid TG spam).
                msg = str(exc)
                logger.error("Yandex auth/scan failed for %s: %s", source.id, msg)
                if "not_logged_in" not in msg:
                    await self.review_service.tg_bot.notify(
                        f"⚠️ Яндекс Услуги ({source.id}): {html.escape(msg[:400])}"
                    )
                continue
            except FlruAuthError as exc:
                msg = str(exc)
                logger.error("FL.ru auth/scan failed for %s: %s", source.id, msg)
                if "not_logged_in" not in msg:
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
                if known:
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

                outcome = await self._process_new_project(source, preview)
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
            cycle_stats.merge(source_stats)

        self.scan_reports.save(cycle_stats)
        return totals

    def _scan_listings(self, source: SourceConfig) -> list[Any]:
        if source.platform in ("yandex_uslugi", "flru"):
            adapter = self._adapter_factory(source, None)
            try:
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

        if (
            not manual
            and acceptance_tier == "standard"
            and await self._skip_over_budget_ceiling(full, context)
        ):
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
                fit=score.fit,
                score=float(score.score),
            )
            return "budget_reject"

        if self.settings.require_telegram_approval:
            await self.review_service.request_review(
                full, score, acceptance_tier=acceptance_tier
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

    async def _skip_over_budget_ceiling(
        self, full: ProjectFull, lightrag_context: str
    ) -> bool:
        if parse_budget_ceiling_rub(full) is None:
            return False
        estimated = await asyncio.to_thread(
            self.offer_estimator.estimate_market_cost, full, lightrag_context
        )
        if price_exceeds_budget_ceiling(
            estimated,
            full,
            multiplier=self.settings.budget_ceiling_price_multiplier,
        ):
            ceiling = parse_budget_ceiling_rub(full)
            logger.info(
                "skip_budget_ceiling project_id=%s estimated=%s ceiling=%s mult=%s",
                full.project_id,
                estimated,
                ceiling,
                self.settings.budget_ceiling_price_multiplier,
            )
            return True
        return False

    async def _refresh_offer_project(self, offer: PendingOffer) -> ProjectFull:
        if offer.source_key == TZ_MANUAL_SOURCE_KEY:
            return offer.project
        brief = build_project_brief(offer.project)
        if len(brief) >= 80 and len(offer.project.full_description or "") >= 40:
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

    async def _generate_response_text(
        self,
        offer: PendingOffer,
        *,
        notify: Any | None = None,
    ) -> str:
        await self._refresh_offer_project(offer)
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
        gap = budget_gap(fair_price, offer.project) if fair_price > 0 else None
        price_hint: int | str | None = fair_price if fair_price > 0 else None
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
                budget_mismatch=gap,
            )
        else:
            text = await asyncio.to_thread(
                gen.generate,
                offer.project,
                context,
                examples=examples,
                recent_responses=recent,
                price_hint=price_hint,
                budget_mismatch=gap,
            )
        text = finalize_response_text(text.strip(), offer.project)
        return ensure_budget_mismatch_note(text, gap)

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

    async def _send_manual_copy(self, offer: PendingOffer) -> None:
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
        commercial = pick_commercial_price(fair_price, offer_price)
        gap_fair = fair_price if fair_price > 0 else offer_price
        gap = budget_gap(gap_fair, offer.project) if gap_fair > 0 else None
        price_rub = (
            int(gap["fill_price"])
            if gap
            else clamp_price_to_budget(commercial, offer.project)
        )
        price_rub = clamp_price_to_budget(price_rub, offer.project)
        delivery_days = terms.delivery_days
        tier = offer.acceptance_tier or resolve_acceptance_tier(
            offer.project, offer.score, self.settings
        )
        if tier in ("quick_win", "experience_win"):
            delivery_days = min(
                delivery_days, self.settings.quick_win_max_delivery_days
            )

        response_text = append_missing_checklist_answers(
            response_text,
            offer.project,
            price_rub=gap_fair if gap else price_rub,
            delivery_days=delivery_days,
        )
        response_text = ensure_budget_mismatch_note(response_text, gap)
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

    async def _prepare_offer_on_site(self, offer: PendingOffer) -> None:
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
        # commercial = min(market, offer) for TG/form when no gap; gap uses market fair.
        offer_price = int(terms.price_rub or 0)
        commercial = pick_commercial_price(fair_price, offer_price)
        gap_fair = fair_price if fair_price > 0 else offer_price
        gap = budget_gap(gap_fair, offer.project) if gap_fair > 0 else None
        fill_price = (
            int(gap["fill_price"])
            if gap
            else clamp_price_to_budget(commercial, offer.project)
        )
        # Always clamp form fill to ceiling / project budget (never put fair into form).
        fill_price = clamp_price_to_budget(fill_price, offer.project)
        price = str(fill_price)
        delivery_days = terms.delivery_days
        tier = offer.acceptance_tier or resolve_acceptance_tier(
            offer.project, offer.score, self.settings
        )
        if tier in ("quick_win", "experience_win"):
            delivery_days = min(
                delivery_days, self.settings.quick_win_max_delivery_days
            )
        response_text = append_missing_checklist_answers(
            response_text,
            offer.project,
            price_rub=gap_fair if gap else fill_price,
            delivery_days=delivery_days,
        )
        response_text = ensure_budget_mismatch_note(response_text, gap)
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
            # Gap: show market fair + потолок. No gap: same commercial for TG and form.
            notify_price = gap_fair if gap else (
                commercial if commercial > 0 else fill_price
            )
            if gap and ceiling is not None:
                estimate_line = (
                    f"Оценка: {format_rub_amount(notify_price)} ₽ "
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
                        commercial = pick_commercial_price(fair_price, offer_price)
                        gap_fair = fair_price if fair_price > 0 else offer_price
                        gap = (
                            budget_gap(gap_fair, offer.project)
                            if gap_fair > 0
                            else None
                        )
                        fill_price = (
                            int(gap["fill_price"])
                            if gap
                            else clamp_price_to_budget(commercial, offer.project)
                        )
                        fill_price = clamp_price_to_budget(fill_price, offer.project)
                        price = str(fill_price)
                        delivery_days = terms.delivery_days
                        response_text = append_missing_checklist_answers(
                            response_text,
                            offer.project,
                            price_rub=gap_fair if gap else fill_price,
                            delivery_days=delivery_days,
                        )
                        response_text = ensure_budget_mismatch_note(response_text, gap)
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
            await callback.answer("Уже в журнале", show_alert=True)
            return
        if item.project_id in self.journal.project_ids_in_journal():
            item.journal_confirmed = True
            item.journal_exported = True
            self.prepared_store.save(item)
            await self.review_service.tg_bot.mark_journal_confirmed(callback)
            await callback.answer("Уже в журнале")
            return

        item.journal_confirmed = True
        self.prepared_store.save(item)

        response_text = item.response_text
        price = item.price
        delivery_days = item.delivery_days
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

        offer = self.review_service.store.load(platform, source_key, project_id)
        if offer is not None:
            offer.status = "submitted"
            self.review_service.store.save(offer)
        self.repository.update_status(platform, source_key, project_id, "submitted")

        await self.review_service.tg_bot.mark_journal_confirmed(callback)
        await callback.answer("Записано в журнал")
        await self.review_service.tg_bot.notify(
            f"📒 Журнал: строка {row} · {html.escape(item.title)}\n"
            "На ПК: /journal в TG — пришлёт актуальный journal.xlsx с VPS."
        )
        logger.info("journal_confirmed project_id=%s row=%s", project_id, row)

    def _fetch_submitted_offer_text(self, project_id: str) -> OfferFormSnapshot:
        browser = get_browser_client(self.settings)
        try:
            if pair := self.settings.kwork_credentials():
                ensure_logged_in(
                    browser, KworkCredentials(login=pair[0], password=pair[1])
                )
            return read_submitted_offer_text(browser, project_id)
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
        return {"project_id": pid, "outcome": outcome}

    async def handle_manual_project(
        self, message: Any, project_id: str, *, platform: str | None = None
    ) -> None:
        pid = (project_id or "").strip()
        if platform is None:
            if re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                pid,
                flags=re.I,
            ):
                platform = "yandex_uslugi"
            elif extract_flru_project_id(pid) or (
                pid.isdigit() and len(pid) >= 6 and "fl.ru" in (getattr(message, "text", "") or "")
            ):
                platform = "flru"
            else:
                platform = "kwork"
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

    async def handle_manual_tz(self, message: Any, text: str) -> None:
        body = (text or "").strip()
        if len(body) < TZ_MIN_CHARS:
            await message.answer(
                f"Слишком короткий текст (минимум {TZ_MIN_CHARS} символов). "
                "Пришли полное ТЗ: /tz <текст>"
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
    )
