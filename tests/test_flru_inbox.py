from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.adapters.flru_inbox import (
    FlruInboxClient,
    FlruInboxMessage,
    OfferPreview,
    format_inbox_notify,
    normalize_message_text,
    parse_inbox_message,
    parse_offers_payload,
    parse_thread_payload,
    should_fetch_thread,
)
from src.pipeline.flru_inbox_poller import (
    collect_candidates_for_offer,
    poll_flru_inbox,
    select_incoming,
)
from src.store.kwork_inbox_seen import KworkInboxSeenStore


SELF_ID = "501"
CUSTOMER_ID = "902"
OFFER_ID = "75725313"
PROJECT_ID = "5518223"
OTHER_OFFER_ID = "88888888"
OTHER_PROJECT_ID = "5518999"
INBOX_URL = "https://www.fl.ru/messages/"


def _auth_eval_mocks(*payloads: object) -> list[object]:
    return ["https://www.fl.ru/messages/", *payloads]


@pytest.fixture
def store(tmp_path: Path) -> KworkInboxSeenStore:
    return KworkInboxSeenStore(tmp_path / "flru_inbox_seen.db")


def _offer(
    *,
    mid: int = 10,
    frl_new: int = 0,
    from_id: str = CUSTOMER_ID,
    text: str = "hello",
    title: str = "Разработка сайта",
    offer_id: str = OFFER_ID,
    project_id: str = PROJECT_ID,
    last_message_at: str = "2026-08-20T10:00:00+00:00",
) -> OfferPreview:
    return OfferPreview(
        offer_id=offer_id,
        project_id=project_id,
        title=title,
        mid=mid,
        frl_new_msg_count=frl_new,
        last_message_at=last_message_at,
        last_message=FlruInboxMessage(
            mid=mid,
            from_id=from_id,
            text=text,
            offer_id=offer_id,
            project_id=project_id,
        ),
    )


def test_normalize_message_text_empty_attachment() -> None:
    assert normalize_message_text("") == "(вложение)"
    assert normalize_message_text("   ") == "(вложение)"
    assert normalize_message_text("hi") == "hi"


def test_parse_offers_payload_list() -> None:
    payload = {
        "ok": True,
        "json": {
            "last_offer_time": "2026-08-20T10:00:00+00:00",
            "has_next_page": False,
            "items": [
                {
                    "id": int(OFFER_ID),
                    "project_id": int(PROJECT_ID),
                    "last_message_at": "2026-08-20T10:00:00+00:00",
                    "frl_new_msg_count": 2,
                    "title": "Разработка сайта",
                    "author": {"id": int(SELF_ID), "login": "me"},
                }
            ],
            "messages": [
                {
                    "id": 55,
                    "from_id": int(CUSTOMER_ID),
                    "time": "2026-08-20T10:00:00+00:00",
                    "text": "привет",
                    "offer_id": int(OFFER_ID),
                    "project_id": int(PROJECT_ID),
                }
            ],
        },
    }
    offers = parse_offers_payload(payload)
    assert len(offers) == 1
    assert offers[0].offer_id == OFFER_ID
    assert offers[0].project_id == PROJECT_ID
    assert offers[0].mid == 55
    assert offers[0].frl_new_msg_count == 2
    assert offers[0].last_message is not None
    assert offers[0].last_message.text == "привет"


def test_parse_thread_payload() -> None:
    payload = {
        "ok": True,
        "json": {
            "last_message_time": "2026-08-20T10:05:00+00:00",
            "items": [
                {
                    "id": 11,
                    "from_id": int(SELF_ID),
                    "time": "2026-08-20T10:01:00+00:00",
                    "text": "mine",
                },
                {
                    "id": 12,
                    "from_id": int(CUSTOMER_ID),
                    "time": "2026-08-20T10:05:00+00:00",
                    "text": "client ping",
                },
            ],
            "has_next_page": False,
        },
    }
    msgs = parse_thread_payload(payload)
    assert [m.mid for m in msgs] == [11, 12]
    assert msgs[1].text == "client ping"


def test_select_incoming_skips_own_and_old() -> None:
    msgs = [
        FlruInboxMessage(mid=10, from_id=CUSTOMER_ID, text="old"),
        FlruInboxMessage(mid=11, from_id=SELF_ID, text="mine"),
        FlruInboxMessage(mid=12, from_id=CUSTOMER_ID, text="new"),
    ]
    got = select_incoming(msgs, self_user_id=SELF_ID, last_mid=10)
    assert [m.mid for m in got] == [12]


def test_should_fetch_thread() -> None:
    dialog = _offer(mid=10, frl_new=0)
    assert should_fetch_thread(dialog, stored_mid=10) is False
    assert should_fetch_thread(_offer(mid=11), stored_mid=10) is True
    assert should_fetch_thread(_offer(mid=10, frl_new=1), stored_mid=10) is True


def test_collect_candidates_fallback_last_message() -> None:
    dialog = _offer(mid=20, from_id=CUSTOMER_ID, text="fallback")
    got = collect_candidates_for_offer(
        dialog,
        stored_mid=10,
        fetched=[],
        self_user_id=SELF_ID,
    )
    assert len(got) == 1
    assert got[0].mid == 20

    own = _offer(mid=21, from_id=SELF_ID, text="mine")
    assert (
        collect_candidates_for_offer(
            own, stored_mid=10, fetched=[], self_user_id=SELF_ID
        )
        == []
    )


@pytest.mark.asyncio
async def test_poll_bootstrap_no_notify(store: KworkInboxSeenStore) -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = _auth_eval_mocks(
        {
            "ok": True,
            "status": 200,
            "json": {
                "items": [
                    {
                        "id": int(OFFER_ID),
                        "project_id": int(PROJECT_ID),
                        "last_message_at": "2026-08-20T10:00:00+00:00",
                        "frl_new_msg_count": 3,
                        "title": "Разработка сайта",
                        "author": {"id": int(SELF_ID)},
                    }
                ],
                "messages": [
                    {
                        "id": 40,
                        "from_id": int(CUSTOMER_ID),
                        "text": "x",
                        "offer_id": int(OFFER_ID),
                        "project_id": int(PROJECT_ID),
                    }
                ],
            },
        },
    )
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_flru_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["bootstrapped"] == 1
    assert notified == []
    assert store.is_bootstrapped() is True
    assert store.get_last_mid(OFFER_ID) == 40


@pytest.mark.asyncio
async def test_poll_notifies_new_incoming_and_bumps_mid(
    store: KworkInboxSeenStore,
) -> None:
    store.set_last_mid(OFFER_ID, "Разработка сайта", 10)
    store.mark_bootstrap_done()

    list_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "items": [
                {
                    "id": int(OFFER_ID),
                    "project_id": int(PROJECT_ID),
                    "last_message_at": "2026-08-20T10:05:00+00:00",
                    "frl_new_msg_count": 1,
                    "title": "Разработка сайта",
                    "author": {"id": int(SELF_ID)},
                }
            ],
            "messages": [
                {
                    "id": 12,
                    "from_id": int(CUSTOMER_ID),
                    "text": "new",
                    "offer_id": int(OFFER_ID),
                    "project_id": int(PROJECT_ID),
                }
            ],
        },
    }
    thread_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "items": [
                {
                    "id": 11,
                    "from_id": int(SELF_ID),
                    "text": "my reply",
                },
                {
                    "id": 12,
                    "from_id": int(CUSTOMER_ID),
                    "text": 'client "ping"',
                },
            ],
        },
    }

    browser = MagicMock()
    browser.evaluate.side_effect = _auth_eval_mocks(list_payload, thread_payload)
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_flru_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 1
    assert len(notified) == 1
    assert 'client "ping"' in notified[0]
    assert "FL.ru · входящее" in notified[0]
    assert "Разработка сайта" in notified[0]
    assert store.get_last_mid(OFFER_ID) == 12


@pytest.mark.asyncio
async def test_poll_new_offer_after_bootstrap_notifies(
    store: KworkInboxSeenStore,
) -> None:
    store.set_last_mid(OFFER_ID, "Разработка сайта", 10)
    store.mark_bootstrap_done()
    assert store.get_last_mid(OTHER_OFFER_ID) is None

    list_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "items": [
                {
                    "id": int(OFFER_ID),
                    "project_id": int(PROJECT_ID),
                    "last_message_at": "2026-08-20T10:00:00+00:00",
                    "frl_new_msg_count": 0,
                    "title": "Разработка сайта",
                    "author": {"id": int(SELF_ID)},
                },
                {
                    "id": int(OTHER_OFFER_ID),
                    "project_id": int(OTHER_PROJECT_ID),
                    "last_message_at": "2026-08-20T11:00:00+00:00",
                    "frl_new_msg_count": 1,
                    "title": "Новый проект",
                    "author": {"id": int(SELF_ID)},
                },
            ],
            "messages": [
                {
                    "id": 10,
                    "from_id": int(CUSTOMER_ID),
                    "text": "old",
                    "offer_id": int(OFFER_ID),
                },
                {
                    "id": 50,
                    "from_id": int(CUSTOMER_ID),
                    "text": "first hello",
                    "offer_id": int(OTHER_OFFER_ID),
                    "project_id": int(OTHER_PROJECT_ID),
                },
            ],
        },
    }
    thread_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "items": [
                {
                    "id": 50,
                    "from_id": int(CUSTOMER_ID),
                    "text": "first hello",
                }
            ],
        },
    }

    browser = MagicMock()
    browser.evaluate.side_effect = _auth_eval_mocks(list_payload, thread_payload)
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_flru_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 1
    assert result["checked"] == 2
    assert "first hello" in notified[0]
    assert store.get_last_mid(OTHER_OFFER_ID) == 50
    assert store.get_last_mid(OFFER_ID) == 10


@pytest.mark.asyncio
async def test_poll_thread_fail_fallback_list_message(
    store: KworkInboxSeenStore,
) -> None:
    store.set_last_mid(OFFER_ID, "Разработка сайта", 10)
    store.mark_bootstrap_done()

    list_payload = {
        "ok": True,
        "status": 200,
        "json": {
            "items": [
                {
                    "id": int(OFFER_ID),
                    "project_id": int(PROJECT_ID),
                    "last_message_at": "2026-08-20T11:00:00+00:00",
                    "frl_new_msg_count": 1,
                    "title": "Разработка сайта",
                    "author": {"id": int(SELF_ID)},
                }
            ],
            "messages": [
                {
                    "id": 15,
                    "from_id": int(CUSTOMER_ID),
                    "text": "",
                    "offer_id": int(OFFER_ID),
                    "project_id": int(PROJECT_ID),
                    "files": [{"id": 1}],
                }
            ],
        },
    }
    thread_payload = {"ok": False, "status": 500, "error": "fail"}

    browser = MagicMock()
    browser.evaluate.side_effect = _auth_eval_mocks(list_payload, thread_payload)
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_flru_inbox(
        settings=MagicMock(),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 1
    assert "(вложение)" in notified[0]
    assert store.get_last_mid(OFFER_ID) == 15


def test_format_inbox_notify_escapes_html() -> None:
    text = format_inbox_notify("Site<script>", "<b>hi</b>")
    assert "<script>" not in text
    assert "&lt;b&gt;hi&lt;/b&gt;" in text
    assert "https://www.fl.ru/messages/" in text


def test_parse_inbox_message() -> None:
    msg = parse_inbox_message({"id": 7, "from_id": 902, "text": "  "})
    assert msg is not None
    assert msg.text == "(вложение)"


def test_client_fetch_offers_mock_browser() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = {
        "ok": True,
        "status": 200,
        "json": {
            "items": [
                {
                    "id": 123,
                    "project_id": 456,
                    "title": "T",
                    "author": {"id": 501},
                }
            ],
            "messages": [
                {
                    "id": 1,
                    "from_id": 902,
                    "text": "m",
                    "offer_id": 123,
                    "project_id": 456,
                }
            ],
        },
    }
    client = FlruInboxClient(browser)
    offers, _raw = client.fetch_offers()
    assert len(offers) == 1
    assert client.self_user_id == "501"


def test_fetch_thread_js_url_no_json_quotes() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = {"ok": True, "status": 200, "json": {"items": []}}
    client = FlruInboxClient(browser)
    client._inbox_ready = True
    client.fetch_thread_messages("5518223", "75725313")
    js = browser.evaluate.call_args[0][0]
    assert "/projects/5518223/offers/75725313/" in js
    assert '/projects/"5518223"' not in js
    assert '/offers/"75725313"' not in js
