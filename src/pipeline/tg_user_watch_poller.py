from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from src.adapters.tg_user_watch import (
    WatchMessage,
    connect_resolve_fetch,
    format_watch_notify,
    select_new_messages,
)
from src.config import Settings
from src.store.kwork_inbox_seen import KworkInboxSeenStore

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]

EMPTY_STATS = {
    "bootstrapped": 0,
    "checked": 0,
    "fetched": 0,
    "notified": 0,
    "errors": 0,
}


def process_watch_poll(
    *,
    store: KworkInboxSeenStore,
    chat_key: str,
    chat_title: str,
    messages: list[WatchMessage],
    invite_hash: str,
    latest_mid_hint: int = 0,
) -> tuple[dict[str, int], list[str]]:
    """Pure store + select + format (no network)."""
    result = dict(EMPTY_STATS)
    pending_notify: list[str] = []

    if not store.is_bootstrapped():
        mid = int(latest_mid_hint or 0)
        if messages:
            mid = max(mid, max(m.mid for m in messages))
        store.set_last_mid(chat_key, chat_title, mid)
        store.mark_bootstrap_done()
        result["bootstrapped"] = 1
        result["checked"] = 1
        result["fetched"] = len(messages)
        logger.info(
            "tg_user_watch_bootstrap_done chat_id=%s mid=%s",
            chat_key,
            mid,
        )
        return result, pending_notify

    result["checked"] = 1
    result["fetched"] = len(messages)
    stored = store.get_last_mid(chat_key)
    if stored is None:
        # Silent adopt unknown chat key after bootstrap (e.g. migrated entity id)
        mid = int(latest_mid_hint or 0)
        if messages:
            mid = max(mid, max(m.mid for m in messages))
        store.set_last_mid(chat_key, chat_title, mid)
        logger.info(
            "tg_user_watch_silent_adopt chat_id=%s mid=%s",
            chat_key,
            mid,
        )
        return result, pending_notify

    incoming = select_new_messages(messages, last_mid=stored)
    max_mid = stored
    for msg in incoming:
        pending_notify.append(
            format_watch_notify(
                author=msg.author,
                text=msg.text,
                is_forward=msg.is_forward,
                invite_hash=invite_hash,
            )
        )
        result["notified"] += 1
        if msg.mid > max_mid:
            max_mid = msg.mid

    if latest_mid_hint > max_mid:
        max_mid = int(latest_mid_hint)
    if max_mid > stored or chat_title:
        store.set_last_mid(chat_key, chat_title, max_mid)

    return result, pending_notify


def _poll_tg_user_watch_sync(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
) -> tuple[dict[str, int], list[str]]:
    invite = (settings.tg_user_watch_invite or "").strip()
    bootstrapped = store.is_bootstrapped()

    # Prefer existing chat key if any; else invite hash as provisional key for mid lookup
    known_rows = store.list_all() if bootstrapped else []
    prior_key = known_rows[0].interlocutor_id if known_rows else ""
    stored_mid = store.get_last_mid(prior_key) if prior_key else None
    min_id = int(stored_mid or 0)

    chat_key, chat_title, messages, latest_hint = asyncio.run(
        connect_resolve_fetch(
            api_id=int(settings.tg_user_api_id or 0),
            api_hash=settings.tg_user_api_hash or "",
            session_path=settings.tg_user_session_path or "data/tg_user.session",
            invite_hash=invite,
            min_id=min_id,
            bootstrap=not bootstrapped,
            proxy_url=getattr(settings, "tg_user_proxy", "") or "",
        )
    )
    return process_watch_poll(
        store=store,
        chat_key=chat_key,
        chat_title=chat_title,
        messages=messages,
        invite_hash=invite,
        latest_mid_hint=latest_hint,
    )


async def poll_tg_user_watch(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
    notify: NotifyFn,
) -> dict[str, int]:
    """Poll VIP Telegram group via USER session; mirror new messages to approve-bot."""
    if not settings.tg_user_watch_enabled:
        return dict(EMPTY_STATS)
    if not (settings.tg_user_watch_invite or "").strip():
        logger.error("tg_user_watch_skip: TG_USER_WATCH_INVITE empty")
        stats = dict(EMPTY_STATS)
        stats["errors"] = 1
        return stats

    result, pending = await asyncio.to_thread(
        _poll_tg_user_watch_sync,
        settings=settings,
        store=store,
    )
    for chunk in pending:
        try:
            await notify(chunk)
        except Exception:
            result["errors"] += 1
            logger.exception("tg_user_watch_notify_failed")
    return result
