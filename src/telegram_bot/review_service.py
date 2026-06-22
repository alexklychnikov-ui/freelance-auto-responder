from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from aiogram.types import CallbackQuery, Message

from src.config import Settings
from src.models import GptScoreResult, PendingOffer, ProjectFull
from src.store.repository import ProjectRepository
from src.telegram_bot.bot import TelegramReviewBot
from src.telegram_bot.pending_store import PendingStore

logger = logging.getLogger(__name__)

ApproveHandler = Callable[
    [str, str, str, PendingOffer, CallbackQuery],
    Awaitable[None],
]
SubmitTextHandler = Callable[[PendingOffer, str], Awaitable[None]]
ExportJournalHandler = Callable[[Message], Awaitable[None]]


class ReviewService:
    def __init__(
        self,
        settings: Settings,
        store: PendingStore,
        tg_bot: TelegramReviewBot,
        repository: ProjectRepository,
        *,
        on_approve: ApproveHandler | None = None,
        on_submit_text: SubmitTextHandler | None = None,
        on_export_journal: ExportJournalHandler | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.tg_bot = tg_bot
        self.repository = repository
        self._on_approve = on_approve
        self._on_submit_text = on_submit_text
        self._on_export_journal = on_export_journal
        self.tg_bot.register_handlers(
            on_approve=self._handle_approve,
            on_reject=self._handle_reject,
            on_response_text=self._handle_response_text,
            on_export_journal=self._handle_export_journal,
        )

    def set_approve_handler(self, handler: ApproveHandler) -> None:
        self._on_approve = handler

    def set_submit_text_handler(self, handler: SubmitTextHandler) -> None:
        self._on_submit_text = handler

    def set_export_journal_handler(self, handler: ExportJournalHandler) -> None:
        self._on_export_journal = handler

    async def request_review(
        self,
        project: ProjectFull,
        score: GptScoreResult,
    ) -> PendingOffer:
        offer = PendingOffer(
            platform=project.platform,
            source_key=project.source_key,
            project_id=project.project_id,
            url=project.url,
            title=project.title,
            project=project,
            score=score,
            created_at=datetime.now(timezone.utc),
            status="pending",
        )
        if self.settings.require_telegram_approval:
            msg_id = await self.tg_bot.send_review_card(offer)
            offer.telegram_message_id = msg_id
        self.store.save(offer)
        self.repository.update_status(
            project.platform,
            project.source_key,
            project.project_id,
            "notified",
            fit=score.fit,
            score=float(score.score),
        )
        return offer

    async def _handle_approve(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        callback: CallbackQuery,
    ) -> None:
        offer = self.store.load(platform, source_key, project_id)
        if offer is None:
            await self.tg_bot.notify(f"⚠️ Pending offer не найден: {project_id}")
            return
        if offer.status != "pending":
            await callback.answer(f"Уже обработано: {offer.status}", show_alert=True)
            return

        offer.status = "approved"
        offer.approved_at = datetime.now(timezone.utc)
        self.store.save(offer)
        self.repository.update_status(platform, source_key, project_id, "approved")

        if self._on_approve is not None:
            await self._on_approve(platform, source_key, project_id, offer, callback)

    async def _handle_reject(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        callback: CallbackQuery,
    ) -> None:
        offer = self.store.load(platform, source_key, project_id)
        if offer is None:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        if offer.status != "pending":
            await callback.answer(f"Уже обработано: {offer.status}", show_alert=True)
            return

        offer.status = "rejected"
        self.store.save(offer)
        self.repository.update_status(platform, source_key, project_id, "rejected")
        await self.tg_bot.mark_review_skipped(callback)
        await callback.answer("Пропущено")
        await self.tg_bot.notify(f"❌ Пропущен: {offer.title}")
        logger.info(
            "review_rejected platform=%s source=%s project_id=%s",
            platform,
            source_key,
            project_id,
        )

    async def _handle_response_text(self, message: Message) -> None:
        if self._on_submit_text is None or not message.text:
            return
        if str(message.chat.id) != str(self.tg_bot.chat_id):
            return

        offer: PendingOffer | None = None
        if message.reply_to_message and message.reply_to_message.message_id:
            offer = self.store.find_by_draft_message_id(message.reply_to_message.message_id)

        if offer is None:
            awaiting = self.store.list_awaiting_submit()
            if len(awaiting) == 1:
                offer = awaiting[0]

        if offer is None:
            return

        await self._on_submit_text(offer, message.text.strip())

    async def _handle_export_journal(self, message: Message) -> None:
        if self._on_export_journal is None:
            await message.answer("Команда /journal недоступна")
            return
        if str(message.chat.id) != str(self.tg_bot.chat_id):
            return
        await self._on_export_journal(message)

    def expire_stale_pending(self) -> list[PendingOffer]:
        expired = self.store.expire_stale(self.settings.pending_timeout_hours)
        for offer in expired:
            self.repository.update_status(
                offer.platform,
                offer.source_key,
                offer.project_id,
                "expired",
            )
        return expired

    def get_approved_offer(
        self, platform: str, source_key: str, project_id: str
    ) -> PendingOffer | None:
        offer = self.store.load(platform, source_key, project_id)
        if offer is None or offer.status != "approved":
            return None
        return offer

    async def run_bot(self) -> None:
        await self.tg_bot.run_polling()
