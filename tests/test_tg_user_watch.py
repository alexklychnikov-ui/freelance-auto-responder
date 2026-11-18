from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.adapters.tg_user_watch import (
    WatchMessage,
    author_display_name,
    format_watch_notify,
    invite_url,
    is_forward_message,
    message_is_skippable,
    normalize_invite_hash,
    select_new_messages,
    truncate_preview,
)
from src.pipeline.tg_user_watch_poller import poll_tg_user_watch, process_watch_poll
from src.store.kwork_inbox_seen import KworkInboxSeenStore

INVITE = "sfP3Nf0dyJc0ZGQy"
CHAT_KEY = "123456789"


@pytest.fixture
def store(tmp_path: Path) -> KworkInboxSeenStore:
    return KworkInboxSeenStore(tmp_path / "tg_user_watch_seen.db")


def test_normalize_and_invite_url() -> None:
    assert normalize_invite_hash(INVITE) == INVITE
    assert normalize_invite_hash(f"https://t.me/+{INVITE}") == INVITE
    assert normalize_invite_hash(f"+{INVITE}") == INVITE
    assert invite_url(INVITE) == f"https://t.me/+{INVITE}"


def test_truncate_preview() -> None:
    assert truncate_preview("short") == "short"
    long = "x" * 500
    out = truncate_preview(long, max_len=400)
    assert len(out) == 400
    assert out.endswith("…")


def test_format_watch_notify_forward_and_plain() -> None:
    plain = format_watch_notify(
        author="Alice",
        text="hello <b>x</b>",
        is_forward=False,
        invite_hash=INVITE,
    )
    assert "🔔 VIP-чат · новое" in plain
    assert "👤 Alice" in plain
    assert "↪️ пересыл" not in plain
    assert "📄 hello &lt;b&gt;x&lt;/b&gt;" in plain
    assert f"🔗 https://t.me/+{INVITE}" in plain

    fwd = format_watch_notify(
        author="Bob",
        text="fwd body",
        is_forward=True,
        invite_hash=INVITE,
    )
    assert "↪️ пересыл" in fwd
    assert "👤 Bob" in fwd


def test_is_forward_and_skippable() -> None:
    assert is_forward_message(SimpleNamespace(fwd_from=object(), forward=None)) is True
    assert is_forward_message(SimpleNamespace(fwd_from=None, forward=object())) is True
    assert is_forward_message(SimpleNamespace(fwd_from=None, forward=None)) is False

    assert message_is_skippable(None) is True
    assert message_is_skippable(SimpleNamespace(id=1, action=object(), message="", media=None)) is True
    assert message_is_skippable(SimpleNamespace(id=1, action=None, message="", media=None)) is True
    assert message_is_skippable(SimpleNamespace(id=1, action=None, message="hi", media=None)) is False
    assert message_is_skippable(SimpleNamespace(id=1, action=None, message="", media=object())) is False


def test_author_display_name() -> None:
    from telethon.tl.types import User

    assert author_display_name(None) == "?"
    assert author_display_name(SimpleNamespace(title="VIP Group", id=9)) == "VIP Group"
    user = User(id=1, is_self=False, access_hash=0, first_name="Ann", last_name="Lee", username="ann")
    assert author_display_name(user) == "Ann Lee (@ann)"


def test_select_new_messages() -> None:
    msgs = [
        WatchMessage(mid=10, author="a", text="old", is_forward=False),
        WatchMessage(mid=12, author="b", text="new", is_forward=True),
        WatchMessage(mid=11, author="c", text="mid", is_forward=False),
    ]
    got = select_new_messages(msgs, last_mid=10)
    assert [m.mid for m in got] == [11, 12]


def test_process_bootstrap_no_notify(store: KworkInboxSeenStore) -> None:
    result, pending = process_watch_poll(
        store=store,
        chat_key=CHAT_KEY,
        chat_title="VIP",
        messages=[
            WatchMessage(mid=50, author="a", text="old1", is_forward=False),
            WatchMessage(mid=55, author="b", text="old2", is_forward=False),
        ],
        invite_hash=INVITE,
        latest_mid_hint=55,
    )
    assert result["bootstrapped"] == 1
    assert result["notified"] == 0
    assert pending == []
    assert store.is_bootstrapped() is True
    assert store.get_last_mid(CHAT_KEY) == 55


def test_process_bootstrap_latest_hint_only(store: KworkInboxSeenStore) -> None:
    result, pending = process_watch_poll(
        store=store,
        chat_key=CHAT_KEY,
        chat_title="VIP",
        messages=[],
        invite_hash=INVITE,
        latest_mid_hint=99,
    )
    assert result["bootstrapped"] == 1
    assert pending == []
    assert store.get_last_mid(CHAT_KEY) == 99


def test_process_new_mid_notifies_with_forward(store: KworkInboxSeenStore) -> None:
    store.set_last_mid(CHAT_KEY, "VIP", 10)
    store.mark_bootstrap_done()
    result, pending = process_watch_poll(
        store=store,
        chat_key=CHAT_KEY,
        chat_title="VIP",
        messages=[
            WatchMessage(mid=10, author="old", text="seen", is_forward=False),
            WatchMessage(mid=11, author="Alice", text="hello", is_forward=False),
            WatchMessage(mid=12, author="Bob", text="fwd", is_forward=True),
        ],
        invite_hash=INVITE,
    )
    assert result["notified"] == 2
    assert len(pending) == 2
    assert "↪️ пересыл" not in pending[0]
    assert "↪️ пересыл" in pending[1]
    assert store.get_last_mid(CHAT_KEY) == 12


@pytest.mark.asyncio
async def test_poll_enabled_false_noop(store: KworkInboxSeenStore) -> None:
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    settings = MagicMock(tg_user_watch_enabled=False)
    result = await poll_tg_user_watch(settings=settings, store=store, notify=notify)
    assert result["notified"] == 0
    assert result["checked"] == 0
    assert notified == []
    assert store.is_bootstrapped() is False
