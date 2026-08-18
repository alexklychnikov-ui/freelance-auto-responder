from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.adapters.kwork_inbox import (
    DialogPreview,
    InboxMessage,
    KworkInboxClient,
    detect_self_user_id_from_dialogs,
    format_inbox_notify,
    parse_dialogs_payload,
    parse_new_messages_payload,
    unescape_message,
)
from src.pipeline.kwork_inbox_poller import (
    collect_candidates_for_dialog,
    poll_kwork_inbox,
    select_incoming,
)
from src.store.kwork_inbox_seen import KworkInboxSeenStore


SELF_ID = "100"
OTHER_ID = "200"
OTHER_USER = "client_alice"
NEW_ID = "300"
NEW_USER = "client_new"


@pytest.fixture
def store(tmp_path: Path) -> KworkInboxSeenStore:
    return KworkInboxSeenStore(tmp_path / "kwork_inbox_seen.db")


def _dialog(
    *,
    mid: int = 10,
    unread: int = 0,
    from_id: str = OTHER_ID,
    to_id: str = SELF_ID,
    text: str = "hello",
    is_support: bool = False,
    username: str = OTHER_USER,
    interlocutor_id: str = OTHER_ID,
) -> DialogPreview:
    return DialogPreview(
        interlocutor_id=interlocutor_id,
        username=username,
        mid=mid,
        unread_count=unread,
        message=text,
        is_support=is_support,
        last_message=InboxMessage(
            mid=mid,
            from_id=from_id,
            to_id=to_id,
            text=text,
            username=username,
        ),
    )


def test_unescape_message() -> None:
    assert unescape_message("say &quot;hi&quot; &amp; bye") == 'say "hi" & bye'
    assert unescape_message(None) == ""


def test_parse_dialogs_filters_and_fields() -> None:
    payload = {
        "ok": True,
        "json": {
            "data": {
                "rows": [
                    {
                        "username": "client_alice",
                        "USERID": 200,
                        "unread_count": 2,
                        "MID": 55,
                        "message": "hi &quot;there&quot;",
                        "is_support": False,
                        "lastMessage": {
                            "MID": 55,
                            "MSGFROM": 200,
                            "MSGTO": 100,
                            "message": "hi &quot;there&quot;",
                            "time": "1710000000",
                        },
                    },
                    {
                        "username": "Support",
                        "USERID": 1,
                        "unread_count": 1,
                        "MID": 9,
                        "message": "help",
                        "is_support": True,
                        "lastMessage": {
                            "MID": 9,
                            "MSGFROM": 1,
                            "MSGTO": 100,
                            "message": "help",
                        },
                    },
                ]
            }
        },
    }
    dialogs = parse_dialogs_payload(payload)
    assert len(dialogs) == 2
    assert dialogs[0].username == "client_alice"
    assert dialogs[0].message == 'hi "there"'
    assert dialogs[0].last_message is not None
    assert dialogs[0].last_message.text == 'hi "there"'
    assert dialogs[1].is_support is True
    assert detect_self_user_id_from_dialogs(dialogs) == "100"


def test_parse_new_messages_payload() -> None:
    payload = {
        "ok": True,
        "json": {
            "data": {
                "messagesData": [
                    {
                        "MID": 12,
                        "MSGFROM": 200,
                        "MSGTO": 100,
                        "message": "a &amp; b",
                    },
                    {
                        "MID": 11,
                        "MSGFROM": 100,
                        "MSGTO": 200,
                        "message": "mine",
                    },
                ]
            }
        },
    }
    msgs = parse_new_messages_payload(payload, username="client_alice")
    assert [m.mid for m in msgs] == [11, 12]
    assert msgs[0].text == "mine"
    assert msgs[1].text == "a & b"


def test_select_incoming_skips_own_and_old() -> None:
    msgs = [
        InboxMessage(mid=10, from_id=OTHER_ID, to_id=SELF_ID, text="old"),
        InboxMessage(mid=11, from_id=SELF_ID, to_id=OTHER_ID, text="mine"),
        InboxMessage(mid=12, from_id=OTHER_ID, to_id=SELF_ID, text="new"),
    ]
    got = select_incoming(msgs, self_user_id=SELF_ID, last_mid=10)
    assert [m.mid for m in got] == [12]


def test_store_bootstrap_and_mid_bump(store: KworkInboxSeenStore) -> None:
    assert store.is_bootstrapped() is False
    assert store.get_last_mid(OTHER_ID) is None
    store.set_last_mid(OTHER_ID, OTHER_USER, 10)
    store.mark_bootstrap_done()
    assert store.is_bootstrapped() is True
    assert store.get_last_mid(OTHER_ID) == 10
    store.set_last_mid(OTHER_ID, OTHER_USER, 15)
    assert store.get_last_mid(OTHER_ID) == 15
    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0].last_mid == 15


def test_collect_candidates_fallback_last_message() -> None:
    dialog = _dialog(mid=20, from_id=OTHER_ID, text="fallback")
    got = collect_candidates_for_dialog(
        dialog,
        stored_mid=10,
        fetched=[],
        self_user_id=SELF_ID,
    )
    assert len(got) == 1
    assert got[0].mid == 20
    assert got[0].text == "fallback"

    own = _dialog(mid=21, from_id=SELF_ID, text="mine")
    assert (
        collect_candidates_for_dialog(
            own, stored_mid=10, fetched=[], self_user_id=SELF_ID
        )
        == []
    )


@pytest.mark.asyncio
async def test_poll_bootstrap_no_notify(store: KworkInboxSeenStore) -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        {
            "ok": True,
            "status": 200,
            "json": {
                "data": {
                    "rows": [
                        {
                            "username": OTHER_USER,
                            "USERID": int(OTHER_ID),
                            "unread_count": 3,
                            "MID": 40,
                            "message": "x",
                            "is_support": False,
                            "lastMessage": {
                                "MID": 40,
                                "MSGFROM": int(OTHER_ID),
                                "MSGTO": int(SELF_ID),
                                "message": "x",
                            },
                        },
                        {
                            "username": "Support",
                            "USERID": 1,
                            "unread_count": 1,
                            "MID": 2,
                            "message": "s",
                            "is_support": True,
                            "lastMessage": {
                                "MID": 2,
                                "MSGFROM": 1,
                                "MSGTO": int(SELF_ID),
                                "message": "s",
                            },
                        },
                    ]
                }
            },
        },
        SELF_ID,
    ]
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_kwork_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
        credentials=None,
        auto_login=False,
    )
    assert result["bootstrapped"] == 1
    assert notified == []
    assert store.is_bootstrapped() is True
    assert store.get_last_mid(OTHER_ID) == 40
    assert store.get_last_mid("1") is None


@pytest.mark.asyncio
async def test_poll_notifies_new_incoming_and_bumps_mid(
    store: KworkInboxSeenStore,
) -> None:
    store.set_last_mid(OTHER_ID, OTHER_USER, 10)
    store.mark_bootstrap_done()

    dialogs_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "data": {
                "rows": [
                    {
                        "username": OTHER_USER,
                        "USERID": int(OTHER_ID),
                        "unread_count": 1,
                        "MID": 12,
                        "message": "new",
                        "is_support": False,
                        "lastMessage": {
                            "MID": 12,
                            "MSGFROM": int(OTHER_ID),
                            "MSGTO": int(SELF_ID),
                            "message": "new",
                        },
                    }
                ]
            }
        },
    }
    messages_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "data": {
                "messagesData": [
                    {
                        "MID": 11,
                        "MSGFROM": int(SELF_ID),
                        "MSGTO": int(OTHER_ID),
                        "message": "my reply",
                    },
                    {
                        "MID": 12,
                        "MSGFROM": int(OTHER_ID),
                        "MSGTO": int(SELF_ID),
                        "message": "client &quot;ping&quot;",
                    },
                ]
            }
        },
    }

    browser = MagicMock()
    browser.evaluate.side_effect = [dialogs_payload, SELF_ID, messages_payload]
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_kwork_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
        credentials=None,
        auto_login=False,
    )
    assert result["notified"] == 1
    assert len(notified) == 1
    assert 'client "ping"' in notified[0]
    assert "Kwork · входящее" in notified[0]
    assert OTHER_USER in notified[0]
    assert store.get_last_mid(OTHER_ID) == 12


@pytest.mark.asyncio
async def test_poll_new_interlocutor_after_bootstrap_notifies(
    store: KworkInboxSeenStore,
) -> None:
    store.set_last_mid(OTHER_ID, OTHER_USER, 10)
    store.mark_bootstrap_done()
    assert store.get_last_mid(NEW_ID) is None

    dialogs_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "data": {
                "rows": [
                    {
                        "username": OTHER_USER,
                        "USERID": int(OTHER_ID),
                        "unread_count": 0,
                        "MID": 10,
                        "message": "old",
                        "is_support": False,
                        "lastMessage": {
                            "MID": 10,
                            "MSGFROM": int(OTHER_ID),
                            "MSGTO": int(SELF_ID),
                            "message": "old",
                        },
                    },
                    {
                        "username": NEW_USER,
                        "USERID": int(NEW_ID),
                        "unread_count": 1,
                        "MID": 50,
                        "message": "first hello",
                        "is_support": False,
                        "lastMessage": {
                            "MID": 50,
                            "MSGFROM": int(NEW_ID),
                            "MSGTO": int(SELF_ID),
                            "message": "first hello",
                        },
                    },
                ]
            }
        },
    }
    messages_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "data": {
                "messagesData": [
                    {
                        "MID": 50,
                        "MSGFROM": int(NEW_ID),
                        "MSGTO": int(SELF_ID),
                        "message": "first hello",
                    }
                ]
            }
        },
    }

    browser = MagicMock()
    browser.evaluate.side_effect = [dialogs_payload, SELF_ID, messages_payload]
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_kwork_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
        credentials=None,
        auto_login=False,
    )
    assert result["notified"] == 1
    assert result["checked"] == 2
    assert len(notified) == 1
    assert "first hello" in notified[0]
    assert NEW_USER in notified[0]
    assert store.get_last_mid(NEW_ID) == 50
    assert store.get_last_mid(OTHER_ID) == 10


@pytest.mark.asyncio
async def test_poll_new_dialog_own_last_message_no_notify(
    store: KworkInboxSeenStore,
) -> None:
    store.mark_bootstrap_done()
    assert store.get_last_mid(NEW_ID) is None

    dialogs_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "data": {
                "rows": [
                    {
                        "username": NEW_USER,
                        "USERID": int(NEW_ID),
                        "unread_count": 0,
                        "MID": 77,
                        "message": "i wrote first",
                        "is_support": False,
                        "lastMessage": {
                            "MID": 77,
                            "MSGFROM": int(SELF_ID),
                            "MSGTO": int(NEW_ID),
                            "message": "i wrote first",
                        },
                    }
                ]
            }
        },
    }
    messages_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "data": {
                "messagesData": [
                    {
                        "MID": 77,
                        "MSGFROM": int(SELF_ID),
                        "MSGTO": int(NEW_ID),
                        "message": "i wrote first",
                    }
                ]
            }
        },
    }

    browser = MagicMock()
    browser.evaluate.side_effect = [dialogs_payload, SELF_ID, messages_payload]
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_kwork_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
        credentials=None,
        auto_login=False,
    )
    assert result["notified"] == 0
    assert notified == []
    assert store.get_last_mid(NEW_ID) == 77


def test_format_inbox_notify_escapes_html() -> None:
    text = format_inbox_notify("bob<script>", "<b>hi</b>")
    assert "<script>" not in text
    assert "&lt;b&gt;hi&lt;/b&gt;" in text
    assert "https://kwork.ru/inbox/bob" in text


def test_client_fetch_dialogs_mock_browser() -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        {
            "ok": True,
            "status": 200,
            "json": {
                "data": {
                    "rows": [
                        {
                            "username": "u",
                            "USERID": "9",
                            "MID": 1,
                            "unread_count": 0,
                            "message": "m",
                            "is_support": False,
                            "lastMessage": {
                                "MID": 1,
                                "MSGFROM": "9",
                                "MSGTO": "8",
                                "message": "m",
                            },
                        }
                    ]
                }
            },
        },
        "8",
    ]
    client = KworkInboxClient(browser)
    dialogs = client.fetch_dialogs()
    assert len(dialogs) == 1
    assert client.self_user_id == "8"
