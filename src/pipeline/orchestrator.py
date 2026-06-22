from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.adapters.kwork_auth import KworkAuthError
from src.adapters.kwork import KworkAdapter
from src.adapters.kwork_auth import KworkCredentials
from src.analyzer.examples_loader import load_response_examples
from src.analyzer.gpt_response_generator import GptResponseGenerator
from src.analyzer.gpt_scorer import GptScorer
from src.analyzer.lightrag_client import LightRagClient
from src.browser.factory import close_browser_client, get_browser_client
from src.adapters.kwork_pricing import suggest_offer_price
from src.config import Settings, SourceConfig, get_enabled_sources, get_settings
from src.journal.writer import JournalWriter
from src.limits.daily import is_daily_limit_reached
from src.models import PendingOffer, ProjectFull
from src.responses.prepared_store import PreparedResponse, PreparedResponseStore
from src.store.repository import ProjectRepository
from src.telegram_bot.bot import TelegramReviewBot
from src.telegram_bot.pending_store import PendingStore
from src.telegram_bot.review_service import ReviewService

logger = logging.getLogger(__name__)


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
        self._browser = browser
        self._adapter_factory = adapter_factory or self._default_adapter
        self.review_service.set_approve_handler(self.handle_approve_click)
        self.review_service.set_submit_text_handler(self.handle_user_response_text)

    def close(self) -> None:
        if self._browser is not None:
            close_browser_client(self._browser)
            self._browser = None

    def _get_browser(self):
        if self._browser is None:
            self._browser = get_browser_client(self.settings)
        return self._browser

    def _default_adapter(self, source: SourceConfig, browser: Any | None = None):
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

    async def run_scan_cycle(self) -> dict[str, int]:
        totals = {"seen": 0, "new": 0, "skipped": 0, "scored": 0, "notified": 0}
        self.review_service.expire_stale_pending()

        for source in get_enabled_sources(self.settings.sources_config_path):
            adapter = self._adapter_factory(source)
            try:
                previews = await asyncio.to_thread(adapter.scan_new)
            except KworkAuthError as exc:
                logger.error("Kwork auth failed for %s: %s", source.id, exc)
                await self.review_service.tg_bot.notify(
                    f"⚠️ Kwork login failed ({source.id}): {exc}"
                )
                continue
            bootstrap = self._is_bootstrap(source)

            known_streak = 0
            new_in_source = 0

            for preview in previews:
                totals["seen"] += 1
                known = self.repository.is_known(
                    preview.platform, preview.source_key, preview.project_id
                )
                if known:
                    totals["skipped"] += 1
                    known_streak += 1
                    if known_streak >= self.settings.scan_early_exit_known_count:
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

                await self._process_new_project(adapter, preview)

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
                "[scan] platform=%s source=%s | seen=%d | new=%d | skipped=%d",
                source.platform,
                source.id,
                len(previews),
                new_in_source,
                len(previews) - new_in_source,
            )

        return totals

    async def _process_new_project(self, adapter: Any, preview: Any) -> None:
        full = await asyncio.to_thread(adapter.read_full, preview.project_id)
        context = await asyncio.to_thread(self.lightrag.get_full_context)
        examples = await asyncio.to_thread(
            load_response_examples, self.settings.response_examples_dir
        )
        score = await asyncio.to_thread(
            self.scorer.score, full, context, examples=examples
        )
        self.repository.update_status(
            full.platform,
            full.source_key,
            full.project_id,
            "scored",
            fit=score.fit,
            score=float(score.score),
        )

        if not score.fit or score.score < self.settings.min_gpt_score:
            self.repository.update_status(
                full.platform,
                full.source_key,
                full.project_id,
                "skipped",
                fit=score.fit,
                score=float(score.score),
            )
            return

        if self.settings.require_telegram_approval:
            await self.review_service.request_review(full, score)
        else:
            offer = PendingOffer(
                platform=full.platform,
                source_key=full.source_key,
                project_id=full.project_id,
                url=full.url,
                title=full.title,
                project=full,
                score=score,
                created_at=datetime.now(timezone.utc),
                status="approved",
                approved_at=datetime.now(timezone.utc),
            )
            context = self.lightrag.get_full_context()
            examples = load_response_examples(self.settings.response_examples_dir)
            offer.response_text = self.response_generator.generate(
                full, context, examples=examples
            )
            await self.handle_approved(
                full.platform, full.source_key, full.project_id, offer
            )

    async def handle_approve_click(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        offer: PendingOffer,
        callback: Any | None,
    ) -> None:
        project = offer.project
        context = self.lightrag.get_full_context()
        examples = load_response_examples(self.settings.response_examples_dir)

        try:
            draft = self.response_generator.generate(
                project, context, examples=examples
            )
        except Exception:
            logger.exception("draft_generation_failed project_id=%s", project_id)
            await self.review_service.tg_bot.notify(
                f"❌ Не удалось сгенерировать черновик: {project.title}"
            )
            return

        offer.response_text = draft
        draft_msg_id = await self.review_service.tg_bot.send_draft_for_edit(
            offer, draft
        )
        offer.draft_message_id = draft_msg_id
        self.review_service.store.save(offer)

        if callback is not None:
            await self.review_service.tg_bot.mark_review_approved(callback)

        await self.review_service.tg_bot.notify(
            f"✍️ Черновик готов: {project.title}\n"
            "Отредактируйте и ответьте на сообщение с черновиком."
        )

    async def handle_user_response_text(
        self, offer: PendingOffer, text: str
    ) -> None:
        if offer.status not in ("approved",):
            await self.review_service.tg_bot.notify(
                f"⚠️ Проект {offer.project_id} уже в статусе {offer.status}"
            )
            return

        offer.response_text = text.strip()
        self.review_service.store.save(offer)

        if self.settings.prepare_only_no_submit:
            await self._prepare_offer_on_site(offer)
            return

        await self.handle_approved(
            offer.platform,
            offer.source_key,
            offer.project_id,
            offer,
        )

    async def _prepare_offer_on_site(self, offer: PendingOffer) -> None:
        source = next(
            (
                s
                for s in get_enabled_sources(self.settings.sources_config_path)
                if s.id == offer.source_key
            ),
            None,
        )
        if source is None:
            await self.review_service.tg_bot.notify(
                f"⚠️ Источник {offer.source_key} не найден"
            )
            return

        price = suggest_offer_price(offer.project)
        delivery_days = self.settings.default_offer_days
        response_text = offer.response_text or ""

        def _run_prepare():
            browser = get_browser_client(self.settings)
            try:
                adapter = self._adapter_factory(source, browser)
                if not hasattr(adapter, "prepare_response"):
                    raise RuntimeError("Adapter does not support prepare_response")
                result = adapter.prepare_response(
                    offer.project_id,
                    response_text,
                    price,
                    delivery_days=delivery_days,
                )
                screenshot = browser.screenshot()
                return result, screenshot, price
            finally:
                close_browser_client(browser)

        try:
            await self.review_service.tg_bot.notify(
                f"⏳ Заполняю форму Kwork: {offer.title}\n"
                f"Цена: {price} ₽ · Срок: {delivery_days} дн.\n"
                "Кнопку «Предложить» не нажимаю."
            )
            result, screenshot, price = await asyncio.to_thread(_run_prepare)
        except Exception as exc:
            logger.exception("prepare_failed project_id=%s", offer.project_id)
            await self._save_prepared_response(
                offer,
                response_text,
                price,
                delivery_days,
                screenshot=None,
                lock_offer=False,
            )
            await self.review_service.tg_bot.notify(
                "⚠️ Браузер упал, текст сохранён на сервере.\n"
                f"{html.escape(str(exc))}\n"
                "Ответьте ещё раз на черновик — повторю заполнение."
            )
            return

        if not result.success:
            await self._save_prepared_response(
                offer,
                response_text,
                price,
                delivery_days,
                screenshot=screenshot,
                lock_offer=False,
            )
            msg = result.message or "unknown"
            if "not_logged_in" in msg:
                await self.review_service.tg_bot.notify(
                    f"⚠️ Kwork не залогинен на VPS — форма не заполнена\n"
                    f"Отклик сохранён в prepared_responses/{offer.project_id}\n"
                    f"Нужен: deploy/kwork_save_session.py или KWORK_AUTO_LOGIN=true"
                )
            else:
                await self.review_service.tg_bot.notify(
                    f"⚠️ Форма не заполнена: {html.escape(offer.title)}\n"
                    f"{html.escape(msg)}\n"
                    "Текст сохранён — ответьте на черновик ещё раз"
                )
            return

        await self._save_prepared_response(
            offer, response_text, price, delivery_days, screenshot=screenshot
        )
        await self.review_service.tg_bot.send_photo(
            screenshot,
            caption=(
                f"🧪 Форма заполнена (без отправки)\n"
                f"{offer.title}\n"
                f"Цена: {price} ₽ · {delivery_days} дн.\n"
                "Excel: Sync-Journal.bat на ПК"
            ),
        )
        await self.review_service.tg_bot.notify(
            f"✅ Готово: {offer.title}\n"
            f"ID: {offer.project_id} · {len(response_text)} символов\n"
            "Excel: Sync-Journal.bat на ПК"
        )

    async def _save_prepared_response(
        self,
        offer: PendingOffer,
        response_text: str,
        price: str,
        delivery_days: int,
        *,
        screenshot: bytes | None,
        lock_offer: bool = True,
    ) -> None:
        shot_path: str | None = None
        if screenshot:
            shots_dir = Path(self.settings.prepared_responses_dir) / "screenshots"
            shots_dir.mkdir(parents=True, exist_ok=True)
            path = shots_dir / f"{offer.platform}_{offer.source_key}_{offer.project_id}.png"
            path.write_bytes(screenshot)
            shot_path = str(path)

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
            screenshot_path=shot_path,
        )
        self.prepared_store.save(prepared)
        if lock_offer:
            offer.status = "prepared"
            self.review_service.store.save(offer)
            self.repository.update_status(
                offer.platform, offer.source_key, offer.project_id, "prepared"
            )

    async def export_prepared_to_journal(self, message: Any = None) -> int:
        items = self.prepared_store.list_not_exported()
        if not items:
            text = "ℹ️ Нет подготовленных откликов для Excel"
            await self.review_service.tg_bot.notify(text)
            return 0

        try:
            count = 0
            for item in items:
                self.journal.append_prepared(
                    item.project,
                    item.score,
                    item.response_text,
                    price=item.price,
                )
                item.journal_exported = True
                self.prepared_store.save(item)
                count += 1
        except Exception as exc:
            logger.exception("journal_export_failed")
            await self.review_service.tg_bot.notify(
                f"⚠️ Ошибка Excel: {html.escape(str(exc))}"
            )
            return 0

        await self.review_service.tg_bot.notify(
            f"📒 Excel: добавлено {count} строк(и)\n"
            f"Файл: {self.settings.response_journal}"
        )
        return count

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

        source = next(
            (
                s
                for s in get_enabled_sources(self.settings.sources_config_path)
                if s.id == source_key
            ),
            None,
        )
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
    )
    journal = JournalWriter(settings.response_journal)
    prepared_store = PreparedResponseStore(settings.prepared_responses_dir)
    return PipelineOrchestrator(
        settings=settings,
        repository=repository,
        review_service=review_service,
        scorer=scorer,
        response_generator=response_generator,
        lightrag=lightrag,
        journal=journal,
        prepared_store=prepared_store,
    )
