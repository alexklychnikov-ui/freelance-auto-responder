from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.models import GptScoreResult, PendingOffer, ProjectFull

logger = logging.getLogger(__name__)

PLATFORM_LABELS = {
    "kwork": "Kwork",
    "flru": "FL.ru",
    "telegram": "Telegram",
}

CALLBACK_APPROVE = "approve"
CALLBACK_REJECT = "reject"
CALLBACK_JOURNAL_CONFIRM = "journal_ok"
CALLBACK_PREPARE_RETRY = "prepare_retry"
CALLBACK_OPEN = "open"


def _chunk_text(text: str, max_len: int = 4000) -> list[str]:
    body = text.strip()
    if len(body) <= max_len:
        return [body]
    chunks: list[str] = []
    start = 0
    while start < len(body):
        end = min(start + max_len, len(body))
        if end < len(body):
            split = body.rfind("\n\n", start, end)
            if split > start + 200:
                end = split
        chunks.append(body[start:end].strip())
        start = end
    return [c for c in chunks if c]


def _project_view_url(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/view"):
        return base
    return f"{base}/view"


def _callback_data(action: str, platform: str, source_key: str, project_id: str) -> str:
    return f"{action}:{platform}:{source_key}:{project_id}"


def parse_callback_data(data: str) -> tuple[str, str, str, str] | None:
    parts = data.split(":", 3)
    if len(parts) != 4:
        return None
    return parts[0], parts[1], parts[2], parts[3]


def _format_budget(value: str | None) -> str:
    if not value:
        return "—"
    text = value.strip()
    if "₽" in text:
        return text
    return f"{text} ₽"


def format_review_card(offer: PendingOffer) -> str:
    project = offer.project
    score = offer.score
    platform_label = PLATFORM_LABELS.get(offer.platform, offer.platform)
    desc_preview = (project.full_description or "")[:300]
    skills = ", ".join(score.matched_skills) if score.matched_skills else "—"
    risks = ", ".join(score.risks) if score.risks else "—"
    desired = _format_budget(project.desired_budget)
    max_b = _format_budget(project.max_budget)
    hire = project.buyer_hire_rate or "—"

    return (
        f"🆕 {platform_label} · {offer.source_key}\n"
        f"📌 {offer.title}\n"
        f"💰 {desired} / {max_b}\n"
        f"👥 Откликов: {project.offers_count if project.offers_count is not None else '—'} · "
        f"Покупатель: {project.buyer or '—'} ({hire})\n"
        f"⏱ {project.time_left or '—'}\n"
        f"🔗 {offer.url}\n\n"
        f"📊 Оценка GPT: {score.score}/10 — {score.reason}\n"
        f"✅ Стек: {skills}\n"
        f"⚠️ Риски: {risks}\n\n"
        f"📝 Кратко:\n{desc_preview}"
    )


def build_review_keyboard(offer: PendingOffer) -> InlineKeyboardMarkup:
    p, s, pid = offer.platform, offer.source_key, offer.project_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Откликнуть",
                    callback_data=_callback_data(CALLBACK_APPROVE, p, s, pid),
                ),
                InlineKeyboardButton(
                    text="❌ Пропустить",
                    callback_data=_callback_data(CALLBACK_REJECT, p, s, pid),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👁 Открыть",
                    url=offer.url,
                ),
            ],
        ]
    )


def build_journal_confirm_keyboard(offer: PendingOffer) -> InlineKeyboardMarkup:
    p, s, pid = offer.platform, offer.source_key, offer.project_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить отклик",
                    callback_data=_callback_data(CALLBACK_JOURNAL_CONFIRM, p, s, pid),
                ),
            ],
        ]
    )


def build_prepare_retry_keyboard(offer: PendingOffer) -> InlineKeyboardMarkup:
    p, s, pid = offer.platform, offer.source_key, offer.project_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Заполнить форму снова",
                    callback_data=_callback_data(CALLBACK_PREPARE_RETRY, p, s, pid),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👁 Открыть проект",
                    url=_project_view_url(offer.url),
                ),
            ],
        ]
    )


class TelegramReviewBot:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        bot: Bot | None = None,
    ) -> None:
        self.chat_id = chat_id
        self._bot = bot or Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._dp = Dispatcher()

    @property
    def bot(self) -> Bot:
        return self._bot

    @property
    def dispatcher(self) -> Dispatcher:
        return self._dp

    def register_handlers(
        self,
        on_approve: Any,
        on_reject: Any,
        on_response_text: Any | None = None,
        on_export_journal: Any | None = None,
        on_scan_report: Any | None = None,
        on_journal_confirm: Any | None = None,
        on_prepare_retry: Any | None = None,
    ) -> None:
        @self._dp.callback_query(F.data.startswith(f"{CALLBACK_APPROVE}:"))
        async def handle_approve(callback: CallbackQuery) -> None:
            parsed = parse_callback_data(callback.data or "")
            if not parsed:
                await callback.answer("Некорректный callback")
                return
            _, platform, source_key, project_id = parsed
            await callback.answer("Готовлю отклик для формы…")
            await on_approve(platform, source_key, project_id, callback)

        @self._dp.callback_query(F.data.startswith(f"{CALLBACK_REJECT}:"))
        async def handle_reject(callback: CallbackQuery) -> None:
            parsed = parse_callback_data(callback.data or "")
            if not parsed:
                await callback.answer("Некорректный callback")
                return
            _, platform, source_key, project_id = parsed
            await on_reject(platform, source_key, project_id, callback)

        if on_journal_confirm is not None:

            @self._dp.callback_query(F.data.startswith(f"{CALLBACK_JOURNAL_CONFIRM}:"))
            async def handle_journal_confirm(callback: CallbackQuery) -> None:
                parsed = parse_callback_data(callback.data or "")
                if not parsed:
                    await callback.answer("Некорректный callback")
                    return
                _, platform, source_key, project_id = parsed
                await on_journal_confirm(platform, source_key, project_id, callback)

        if on_prepare_retry is not None:

            @self._dp.callback_query(F.data.startswith(f"{CALLBACK_PREPARE_RETRY}:"))
            async def handle_prepare_retry(callback: CallbackQuery) -> None:
                parsed = parse_callback_data(callback.data or "")
                if not parsed:
                    await callback.answer("Некорректный callback")
                    return
                _, platform, source_key, project_id = parsed
                await callback.answer("Повторяю заполнение формы…")
                await on_prepare_retry(platform, source_key, project_id, callback)

        @self._dp.message(Command("start"))
        async def handle_start(message: Message) -> None:
            await message.answer(
                "Freelance Auto-Responder\n"
                "Отклик: карточка → автозаполнение формы на Kwork (VPS).\n"
                "Excel: /journal · отчёт сканов: /report"
            )

        @self._dp.message(Command("report"))
        async def handle_report(message: Message) -> None:
            if on_scan_report is None:
                return
            await on_scan_report(message)

        @self._dp.message(Command("journal", "journal_sync"))
        async def handle_journal_sync(message: Message) -> None:
            if on_export_journal is None:
                return
            await on_export_journal(message)

        if on_response_text is not None:

            @self._dp.message(F.text)
            async def handle_text_message(message: Message) -> None:
                if message.text and message.text.startswith("/"):
                    return
                await on_response_text(message)

    async def send_review_card(self, offer: PendingOffer) -> int:
        text = format_review_card(offer)
        msg = await self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=build_review_keyboard(offer),
            disable_web_page_preview=True,
        )
        return msg.message_id

    async def send_offer_link(self, offer: PendingOffer) -> int:
        view_url = html.escape(_project_view_url(offer.url), quote=True)
        safe_title = html.escape(offer.title, quote=False)
        text = f"✍️ <b>Черновик отклика</b> · {safe_title}\n🔗 {view_url}"
        msg = await self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            disable_web_page_preview=True,
        )
        return msg.message_id

    async def send_form_prepared_ready(
        self,
        offer: PendingOffer,
        *,
        price: str,
        delivery_days: int,
        offer_url: str,
        deadline_manual: bool = False,
    ) -> int:
        safe_title = html.escape(offer.title, quote=False)
        text = (
            f"✅ <b>Форма заполнена на Kwork</b> (без отправки)\n"
            f"{safe_title}\n"
            f"Цена: {html.escape(str(price))} ₽ · {delivery_days} дн.\n"
            f"🔗 {html.escape(offer_url, quote=True)}\n"
            "Открой ссылку <b>под тем же аккаунтом Kwork</b>, что на VPS.\n"
            "Проверь форму и нажми «Предложить» на Kwork.\n"
            "Затем нажми <b>Подтвердить отклик</b> ниже — только тогда запишу в журнал Excel."
        )
        if deadline_manual:
            text += "\n⚠️ Срок в форме — выбери вручную в dropdown"
        msg = await self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=build_journal_confirm_keyboard(offer),
            disable_web_page_preview=True,
        )
        return msg.message_id

    async def send_prepare_retry(
        self,
        offer: PendingOffer,
        *,
        error: str,
    ) -> int:
        safe_title = html.escape(offer.title, quote=False)
        safe_error = html.escape(error[:1200], quote=False)
        text = (
            f"⚠️ <b>Форма не заполнена:</b> {safe_title}\n"
            f"{safe_error}\n\n"
            "Нажми <b>«Заполнить форму снова»</b> ниже — повторю автозаполнение на Kwork."
        )
        msg = await self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=build_prepare_retry_keyboard(offer),
            disable_web_page_preview=True,
        )
        return msg.message_id

    async def send_prepared_offer_details(
        self,
        offer: PendingOffer,
        *,
        response_text: str,
        price: str,
        delivery_days: int,
        deadline_manual: bool = False,
    ) -> None:
        view = _project_view_url(offer.url)
        safe_title = html.escape(offer.title, quote=False)
        header = (
            f"✅ <b>Данные для отклика</b>\n"
            f"📌 {safe_title}\n"
            f"💰 {html.escape(str(price))} ₽ · {delivery_days} дн.\n"
            f"🔗 {html.escape(view, quote=True)}\n\n"
            "Открой заказ → <b>Предложить услугу</b> → вставь текст, цену и срок.\n"
            "<i>Автозаполнение на VPS не переносится в твой браузер.</i>"
        )
        if deadline_manual:
            header += "\n⚠️ Срок в dropdown — выбери вручную"
        await self._bot.send_message(
            chat_id=self.chat_id,
            text=header,
            disable_web_page_preview=True,
        )
        for i, chunk in enumerate(_chunk_text(response_text)):
            label = "📝 Текст для поля «Описание»:" if i == 0 else "📝 (продолжение)"
            await self._bot.send_message(
                chat_id=self.chat_id,
                text=f"{label}\n\n{chunk}",
                parse_mode=None,
            )

    async def mark_journal_confirmed(self, callback: CallbackQuery) -> None:
        if callback.message is None:
            return
        base = callback.message.text or callback.message.caption or ""
        await callback.message.edit_text(
            f"{base}\n\n📒 <b>Записано в журнал</b>",
            reply_markup=None,
            disable_web_page_preview=True,
        )

    async def mark_review_skipped(self, callback: CallbackQuery) -> None:
        if callback.message is None:
            return
        base = callback.message.text or callback.message.caption or ""
        await callback.message.edit_text(
            f"{base}\n\n❌ <b>Пропущено</b>",
            reply_markup=None,
            disable_web_page_preview=True,
        )

    async def mark_review_approved(self, callback: CallbackQuery) -> None:
        if callback.message is None:
            return
        base = callback.message.text or callback.message.caption or ""
        await callback.message.edit_text(
            f"{base}\n\n✅ <b>Готовлю данные для отклика</b>",
            reply_markup=None,
            disable_web_page_preview=True,
        )

    async def notify(self, text: str) -> None:
        await self._bot.send_message(chat_id=self.chat_id, text=text)

    async def send_photo(self, image: bytes, caption: str) -> None:
        await self._bot.send_photo(
            chat_id=self.chat_id,
            photo=BufferedInputFile(image, filename="kwork-form.png"),
            caption=caption,
        )

    async def send_document(self, path: str, caption: str | None = None) -> None:
        await self._bot.send_document(
            chat_id=self.chat_id,
            document=FSInputFile(path),
            caption=caption,
        )

    async def run_polling(self) -> None:
        await self._dp.start_polling(self._bot)

    async def close(self) -> None:
        await self._bot.session.close()
