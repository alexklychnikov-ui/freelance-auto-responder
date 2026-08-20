from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from src.adapters.flru import FlruAuthError, _is_login_url
from src.adapters.flru_inbox import (
    FlruInboxClient,
    FlruInboxMessage,
    OfferPreview,
    format_inbox_notify,
    should_fetch_thread,
)
from src.browser.factory import close_browser_client, get_browser_client
from src.config import Settings
from src.store.kwork_inbox_seen import KworkInboxSeenStore

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]


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


def select_incoming(
    messages: list[FlruInboxMessage],
    *,
    self_user_id: str | None,
    last_mid: int,
) -> list[FlruInboxMessage]:
    out: list[FlruInboxMessage] = []
    for msg in messages:
        if msg.mid <= last_mid:
            continue
        if self_user_id and msg.from_id and msg.from_id == self_user_id:
            continue
        out.append(msg)
    out.sort(key=lambda m: m.mid)
    return out


def collect_candidates_for_offer(
    dialog: OfferPreview,
    *,
    stored_mid: int,
    fetched: list[FlruInboxMessage],
    self_user_id: str | None,
) -> list[FlruInboxMessage]:
    messages = list(fetched)
    if not messages and dialog.mid > stored_mid and dialog.last_message is not None:
        lm = dialog.last_message
        if not self_user_id or lm.from_id != self_user_id:
            messages = [lm]
    return select_incoming(messages, self_user_id=self_user_id, last_mid=stored_mid)


def _ensure_flru_logged_in(browser: Any) -> None:
    try:
        url = str(browser.evaluate("() => location.href") or "")
    except Exception:
        url = ""
    if _is_login_url(url):
        raise FlruAuthError(
            "not_logged_in: нет сессии FL.ru — запусти deploy/flru_login_interactive.py"
        )


def _poll_flru_inbox_sync(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
    browser: Any | None = None,
) -> tuple[dict[str, int], list[str]]:
    owns_browser = browser is None
    if owns_browser:
        storage = (settings.flru_storage_state or "").strip() or None
        browser = get_browser_client(settings, storage_state_path=storage)
    try:
        return _poll_flru_inbox_with_browser(
            browser=browser,
            store=store,
        )
    finally:
        if owns_browser and browser is not None:
            close_browser_client(browser)



def _poll_flru_inbox_with_browser(
    *,
    browser: Any,
    store: KworkInboxSeenStore,
) -> tuple[dict[str, int], list[str]]:
    result = {
        "bootstrapped": 0,
        "checked": 0,
        "fetched": 0,
        "notified": 0,
        "errors": 0,
    }
    pending_notify: list[str] = []

    client = FlruInboxClient(browser)
    client.ensure_inbox_page()
    _ensure_flru_logged_in(browser)
    offers, _raw = client.fetch_offers()
    self_id = client.detect_self_user_id(_raw)

    if not store.is_bootstrapped():
        for dialog in offers:
            store.set_last_mid(dialog.offer_id, dialog.title, dialog.mid)
            result["bootstrapped"] += 1
        store.mark_bootstrap_done()
        logger.info(
            "flru_inbox_bootstrap_done offers=%s self_user_id=%s",
            result["bootstrapped"],
            self_id,
        )
        return result, pending_notify

    for dialog in offers:
        result["checked"] += 1
        stored = store.get_last_mid(dialog.offer_id)
        if stored is None:
            stored = 0

        if not should_fetch_thread(dialog, stored_mid=stored):
            continue

        fetched: list[FlruInboxMessage] = []
        try:
            if dialog.project_id:
                fetched = client.fetch_thread_messages(
                    dialog.project_id,
                    dialog.offer_id,
                )
        except Exception:
            result["errors"] += 1
            logger.exception(
                "flru_inbox_fetch_failed offer=%s project=%s",
                dialog.offer_id,
                dialog.project_id,
            )

        result["fetched"] += len(fetched)
        incoming = collect_candidates_for_offer(
            dialog,
            stored_mid=stored,
            fetched=fetched,
            self_user_id=self_id,
        )

        max_mid = stored
        for msg in incoming:
            text = format_inbox_notify(dialog.title, msg.text)
            pending_notify.extend(_chunk_text(text))
            result["notified"] += 1
            if msg.mid > max_mid:
                max_mid = msg.mid

        if dialog.mid > max_mid:
            max_mid = dialog.mid
        if max_mid > stored or dialog.title:
            store.set_last_mid(dialog.offer_id, dialog.title, max_mid)

    return result, pending_notify


async def poll_flru_inbox(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
    notify: NotifyFn,
    browser: Any | None = None,
) -> dict[str, int]:
    """Poll FL.ru inbox and mirror new incoming messages to Telegram."""
    result, pending = await asyncio.to_thread(
        _poll_flru_inbox_sync,
        settings=settings,
        store=store,
        browser=browser,
    )
    for chunk in pending:
        try:
            await notify(chunk)
        except Exception:
            result["errors"] += 1
            logger.exception("flru_inbox_notify_failed")
    return result
