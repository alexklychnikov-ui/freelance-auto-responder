from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from src.adapters.kwork_auth import KworkAuthError, KworkCredentials, ensure_logged_in
from src.adapters.kwork_inbox import (
    DialogPreview,
    InboxMessage,
    KworkInboxClient,
    format_inbox_notify,
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
    messages: list[InboxMessage],
    *,
    self_user_id: str | None,
    last_mid: int,
) -> list[InboxMessage]:
    out: list[InboxMessage] = []
    for msg in messages:
        if msg.mid <= last_mid:
            continue
        if self_user_id and msg.from_id and msg.from_id == self_user_id:
            continue
        out.append(msg)
    out.sort(key=lambda m: m.mid)
    return out


def collect_candidates_for_dialog(
    dialog: DialogPreview,
    *,
    stored_mid: int,
    fetched: list[InboxMessage],
    self_user_id: str | None,
) -> list[InboxMessage]:
    messages = list(fetched)
    if not messages and dialog.mid > stored_mid and dialog.last_message is not None:
        lm = dialog.last_message
        if not self_user_id or lm.from_id != self_user_id:
            messages = [lm]
    return select_incoming(messages, self_user_id=self_user_id, last_mid=stored_mid)


def _poll_kwork_inbox_sync(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
    credentials: KworkCredentials | None,
    auto_login: bool = True,
    browser: Any | None = None,
) -> tuple[dict[str, int], list[str]]:
    """Sync browser/store work (Playwright must not run inside asyncio loop)."""
    owns_browser = browser is None
    if owns_browser:
        storage = (settings.kwork_storage_state or "").strip() or None
        browser = get_browser_client(settings, storage_state_path=storage)
    try:
        return _poll_kwork_inbox_with_browser(
            browser=browser,
            store=store,
            credentials=credentials,
            auto_login=auto_login,
        )
    finally:
        if owns_browser and browser is not None:
            close_browser_client(browser)


def _poll_kwork_inbox_with_browser(
    *,
    browser: Any,
    store: KworkInboxSeenStore,
    credentials: KworkCredentials | None,
    auto_login: bool = True,
) -> tuple[dict[str, int], list[str]]:
    result = {
        "bootstrapped": 0,
        "checked": 0,
        "fetched": 0,
        "notified": 0,
        "errors": 0,
    }
    pending_notify: list[str] = []
    if auto_login:
        ensure_logged_in(browser, credentials)

    client = KworkInboxClient(browser)
    dialogs = client.fetch_dialogs()
    self_id = client.detect_self_user_id(dialogs)
    non_support = [d for d in dialogs if not d.is_support]

    if not store.is_bootstrapped():
        for d in non_support:
            store.set_last_mid(d.interlocutor_id, d.username, d.mid)
            result["bootstrapped"] += 1
        store.mark_bootstrap_done()
        logger.info(
            "kwork_inbox_bootstrap_done dialogs=%s self_user_id=%s",
            result["bootstrapped"],
            self_id,
        )
        return result, pending_notify

    for dialog in non_support:
        result["checked"] += 1
        stored = store.get_last_mid(dialog.interlocutor_id)
        if stored is None:
            stored = 0

        if dialog.unread_count <= 0 and dialog.mid <= stored:
            continue

        try:
            fetched = client.fetch_new_messages(
                dialog.interlocutor_id,
                stored,
                username=dialog.username,
            )
        except Exception:
            result["errors"] += 1
            logger.exception(
                "kwork_inbox_fetch_failed interlocutor=%s",
                dialog.interlocutor_id,
            )
            continue

        result["fetched"] += len(fetched)
        incoming = collect_candidates_for_dialog(
            dialog,
            stored_mid=stored,
            fetched=fetched,
            self_user_id=self_id,
        )

        max_mid = stored
        for msg in incoming:
            text = format_inbox_notify(dialog.username or msg.username, msg.text)
            pending_notify.extend(_chunk_text(text))
            result["notified"] += 1
            if msg.mid > max_mid:
                max_mid = msg.mid

        if dialog.mid > max_mid:
            max_mid = dialog.mid
        if max_mid > stored or dialog.username:
            store.set_last_mid(dialog.interlocutor_id, dialog.username, max_mid)

    return result, pending_notify


async def poll_kwork_inbox(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
    notify: NotifyFn,
    credentials: KworkCredentials | None,
    auto_login: bool = True,
    browser: Any | None = None,
) -> dict[str, int]:
    """Poll Kwork inbox and mirror new incoming messages to Telegram."""
    result, pending = await asyncio.to_thread(
        _poll_kwork_inbox_sync,
        settings=settings,
        store=store,
        credentials=credentials,
        auto_login=auto_login,
        browser=browser,
    )
    for chunk in pending:
        try:
            await notify(chunk)
        except Exception:
            result["errors"] += 1
            logger.exception("kwork_inbox_notify_failed")
    return result
