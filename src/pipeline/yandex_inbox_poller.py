from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from src.adapters.yandex_inbox import (
    YandexInboxClient,
    format_inbox_notify,
    is_dialog_parse_reliable,
    should_notify_dialog,
)
from src.browser.factory import close_browser_client, get_browser_client
from src.config import Settings
from src.store.kwork_inbox_seen import KworkInboxSeenStore

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]


def _poll_yandex_inbox_sync(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
    browser: Any | None = None,
) -> tuple[dict[str, int], list[str]]:
    owns_browser = browser is None
    if owns_browser:
        storage = (settings.yandex_storage_state or "").strip() or None
        browser = get_browser_client(settings, storage_state_path=storage)
    try:
        return _poll_yandex_inbox_with_browser(browser=browser, store=store)
    finally:
        if owns_browser and browser is not None:
            close_browser_client(browser)


def _poll_yandex_inbox_with_browser(
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
        "unreliable": 0,
        "silent_adopt": 0,
        "tab_missing": 0,
    }
    pending_notify: list[str] = []

    client = YandexInboxClient(browser)

    if not store.is_bootstrapped():
        # Seed fingerprints from full list («Все»). Empty = SPA not ready.
        client.select_chats_tab("Все")
        dialogs = client.list_dialogs()
        result["fetched"] = len(dialogs)
        if not dialogs:
            logger.warning("yandex_inbox_bootstrap_deferred empty_dialogs")
            return result, pending_notify
        for dialog in dialogs:
            if not is_dialog_parse_reliable(dialog):
                result["unreliable"] += 1
                continue
            store.set_last_mid(
                dialog.chat_id,
                dialog.title,
                dialog.fingerprint_mid,
            )
            result["bootstrapped"] += 1
        if result["bootstrapped"] <= 0:
            logger.warning(
                "yandex_inbox_bootstrap_deferred unreliable_only unreliable=%s",
                result["unreliable"],
            )
            return result, pending_notify
        store.mark_bootstrap_done()
        logger.info(
            "yandex_inbox_bootstrap_done dialogs=%s unreliable=%s",
            result["bootstrapped"],
            result["unreliable"],
        )
        return result, pending_notify

    # Ongoing polls: only folder «Новые» — old read chats stay out of the list.
    # Fail closed if the tab is missing: listing the default folder spam-notifies
    # every chat whose fingerprint drifted (seen 2026-11-06: notified=8).
    if not client.select_chats_tab("Новые"):
        result["tab_missing"] = 1
        logger.warning(
            "yandex_inbox_skip_poll reason=tab_novye_missing "
            "(no list_dialogs — avoid false TG spam)"
        )
        return result, pending_notify

    dialogs = client.list_dialogs()
    result["fetched"] = len(dialogs)

    for dialog in dialogs:
        result["checked"] += 1
        if not is_dialog_parse_reliable(dialog):
            result["unreliable"] += 1
            logger.debug(
                "yandex_inbox_unreliable chat_id=%s title=%r preview=%r",
                dialog.chat_id,
                dialog.title,
                dialog.last_preview,
            )
            continue

        stored = store.get_last_mid(dialog.chat_id)
        if stored is None:
            store.set_last_mid(
                dialog.chat_id,
                dialog.title,
                dialog.fingerprint_mid,
            )
            result["silent_adopt"] += 1
            continue

        if should_notify_dialog(dialog, stored_mid=stored):
            pending_notify.append(
                format_inbox_notify(dialog.title, dialog.chat_url)
            )
            result["notified"] += 1

        store.set_last_mid(
            dialog.chat_id,
            dialog.title,
            dialog.fingerprint_mid,
        )

    if result["unreliable"]:
        logger.warning(
            "yandex_inbox_unreliable_count count=%s checked=%s",
            result["unreliable"],
            result["checked"],
        )

    return result, pending_notify


async def poll_yandex_inbox(
    *,
    settings: Settings,
    store: KworkInboxSeenStore,
    notify: NotifyFn,
    browser: Any | None = None,
) -> dict[str, int]:
    """Poll Yandex Messenger chat list and mirror new incoming previews to Telegram."""
    result, pending = await asyncio.to_thread(
        _poll_yandex_inbox_sync,
        settings=settings,
        store=store,
        browser=browser,
    )
    for chunk in pending:
        try:
            await notify(chunk)
        except Exception:
            result["errors"] += 1
            logger.exception("yandex_inbox_notify_failed")
    return result
