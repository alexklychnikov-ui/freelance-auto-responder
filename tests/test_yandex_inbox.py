from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.adapters.yandex_inbox import (
    CHATS_URL,
    YandexInboxClient,
    build_dialog_preview,
    chat_id_from_name_element_id,
    format_inbox_notify,
    is_dialog_parse_reliable,
    is_own_last_author,
    is_own_last_message,
    is_own_last_preview,
    make_fingerprint_mid,
    parse_dialogs_payload,
    should_notify_dialog,
)
from src.adapters.yandex_uslugi import YandexAuthError
from src.pipeline.yandex_inbox_poller import poll_yandex_inbox
from src.store.kwork_inbox_seen import KworkInboxSeenStore


CHAT_ID = "4219269039"
CHAT_URL = CHATS_URL
TITLE = "ЗИК Фотин Алексей"


def _tab_ok(tab: str = "Все") -> dict:
    return {"ok": True, "already": True, "tab": tab}


def _auth_ok() -> dict:
    return {"href": CHATS_URL, "not_logged_in": False, "body_snip": ""}


def _eval_queue(tab: str, *payloads: dict) -> list[dict]:
    """select_chats_tab + auth probe + list_dialogs evaluate payloads."""
    return [_tab_ok(tab), _auth_ok(), *payloads]


@pytest.fixture
def store(tmp_path: Path) -> KworkInboxSeenStore:
    return KworkInboxSeenStore(tmp_path / "yandex_inbox_seen.db")


def _dialog(
    *,
    chat_id: str = CHAT_ID,
    title: str = TITLE,
    preview: str = "привет",
    author: str = "Nikolay:",
    is_own_last: bool | None = None,
    unread_count: int | None = None,
):
    return build_dialog_preview(
        chat_id=chat_id,
        title=title,
        last_preview=preview,
        chat_url=CHATS_URL,
        author=author,
        is_own_last=is_own_last,
        unread_count=unread_count,
    )


def test_is_own_last_preview() -> None:
    assert is_own_last_preview("Вы: ок") is True
    assert is_own_last_preview("  Вы: ок") is True
    assert is_own_last_preview("You: ok") is True
    assert is_own_last_preview("привет") is False
    assert is_own_last_preview("вы: lower") is False


def test_is_own_last_author() -> None:
    assert is_own_last_author("Вы:") is True
    assert is_own_last_author("You:") is True
    assert is_own_last_author("Nikolay:") is False
    assert is_own_last_message(author="Вы:", preview="ок") is True
    assert is_own_last_message(author="Nikolay:", preview="ок") is False


def test_chat_id_from_name_element_id() -> None:
    assert chat_id_from_name_element_id("4219269039_name") == "4219269039"
    assert chat_id_from_name_element_id("bad") == ""


def test_fingerprint_changes_with_preview() -> None:
    a = make_fingerprint_mid(CHAT_ID, "Nikolay: привет")
    b = make_fingerprint_mid(CHAT_ID, "Nikolay: новое")
    c = make_fingerprint_mid(CHAT_ID, "Nikolay: привет")
    assert a == c
    assert a != b
    assert a >= 0
    assert b >= 0


def test_fingerprint_whitespace_normalized() -> None:
    a = make_fingerprint_mid(CHAT_ID, "Nikolay:  привет\n\tмир")
    b = make_fingerprint_mid(CHAT_ID, "Nikolay: привет мир")
    assert a == b
    d1 = build_dialog_preview(
        chat_id=CHAT_ID,
        title=TITLE,
        last_preview="Nikolay:  привет\n\tмир",
        author="Nikolay:",
    )
    d2 = build_dialog_preview(
        chat_id=CHAT_ID,
        title=TITLE,
        last_preview="Nikolay: привет мир",
        author="Nikolay:",
    )
    assert d1 is not None and d2 is not None
    assert d1.fingerprint_mid == d2.fingerprint_mid
    assert d1.last_preview == "Nikolay: привет мир"


def test_is_dialog_parse_reliable() -> None:
    ok = _dialog(preview="привет", author="Nikolay:")
    assert ok is not None
    assert is_dialog_parse_reliable(ok) is True

    author_only = _dialog(preview="", author="Nikolay:")
    assert author_only is not None
    assert is_dialog_parse_reliable(author_only) is True

    empty = _dialog(preview="", author="")
    assert empty is not None
    assert empty.last_preview == ""
    assert is_dialog_parse_reliable(empty) is False

    ws = _dialog(preview="  \n\t  ", author="")
    assert ws is not None
    assert is_dialog_parse_reliable(ws) is False

    short = _dialog(preview="a", author="")
    assert short is not None
    assert is_dialog_parse_reliable(short) is False


def test_should_notify_skips_own_and_unchanged() -> None:
    d = _dialog(preview="клиент пишет", author="Nikolay:")
    assert d is not None
    assert d.is_own_last is False
    assert should_notify_dialog(d, stored_mid=0) is False
    assert should_notify_dialog(d, stored_mid=d.fingerprint_mid) is False
    assert should_notify_dialog(d, stored_mid=d.fingerprint_mid + 1) is True

    own = _dialog(preview="мой ответ", author="Вы:")
    assert own is not None
    assert own.is_own_last is True
    assert should_notify_dialog(own, stored_mid=1) is False


def test_should_notify_skips_unreliable_and_unread_zero() -> None:
    bad = _dialog(preview="", author="")
    assert bad is not None
    assert should_notify_dialog(bad, stored_mid=1) is False

    d = _dialog(preview="клиент пишет", author="Nikolay:", unread_count=0)
    assert d is not None
    assert should_notify_dialog(d, stored_mid=1) is False

    d2 = _dialog(preview="клиент пишет", author="Nikolay:", unread_count=2)
    assert d2 is not None
    assert should_notify_dialog(d2, stored_mid=1) is True

    d3 = _dialog(preview="клиент пишет", author="Nikolay:", unread_count=None)
    assert d3 is not None
    assert should_notify_dialog(d3, stored_mid=1) is True


def test_own_preview_overrides_false_js_flag() -> None:
    leaked = build_dialog_preview(
        chat_id=CHAT_ID,
        title=TITLE,
        last_preview="Вы: не утекай",
        chat_url=CHAT_URL,
        is_own_last=False,
    )
    assert leaked is not None
    assert leaked.is_own_last is True
    assert should_notify_dialog(leaked, stored_mid=1) is False


def test_own_author_overrides_false_js_flag() -> None:
    leaked = build_dialog_preview(
        chat_id=CHAT_ID,
        title=TITLE,
        last_preview="ок",
        author="Вы:",
        chat_url=CHAT_URL,
        is_own_last=False,
    )
    assert leaked is not None
    assert leaked.is_own_last is True
    assert should_notify_dialog(leaked, stored_mid=1) is False


def test_format_inbox_notify_no_body_has_link() -> None:
    text = format_inbox_notify(TITLE, CHAT_URL)
    assert "Яндекс Мессенджер · входящее" in text
    assert TITLE in text
    assert CHATS_URL in text
    assert "привет" not in text
    assert "\n\n" not in text

    escaped = format_inbox_notify('A<script>x</script>', 'https://x.test/"')
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped


def test_parse_dialogs_payload_yamb_shape() -> None:
    payload = {
        "ok": True,
        "dialogs": [
            {
                "chat_id": CHAT_ID,
                "title": TITLE,
                "author": "Вы:",
                "last_preview": "Вы: ок",
                "chat_url": CHATS_URL,
                "is_own_last": True,
            },
            {
                "chat_id": "999001",
                "title": "Клиент",
                "author": "Nikolay:",
                "last_preview": "Nikolay: нужна помощь",
                "chat_url": CHATS_URL,
                "is_own_last": False,
                "unread_count": 1,
            },
        ],
    }
    dialogs = parse_dialogs_payload(payload)
    assert len(dialogs) == 2
    assert dialogs[0].is_own_last is True
    assert dialogs[0].chat_url == CHATS_URL
    assert dialogs[0].unread_count is None
    assert dialogs[1].is_own_last is False
    assert dialogs[1].author == "Nikolay:"
    assert dialogs[1].unread_count == 1
    assert dialogs[1].fingerprint_mid == make_fingerprint_mid(
        "999001", "Nikolay: нужна помощь"
    )


@pytest.mark.asyncio
async def test_poll_bootstrap_empty_does_not_mark_done(
    store: KworkInboxSeenStore,
) -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = _eval_queue(
        "Все",
        {"ok": True, "href": CHATS_URL, "dialogs": [], "count": 0},
        {"ok": True, "href": CHATS_URL, "dialogs": [], "count": 0},
    )
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_yandex_inbox(
        settings=MagicMock(yandex_storage_state=""),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["bootstrapped"] == 0
    assert result["notified"] == 0
    assert notified == []
    assert store.is_bootstrapped() is False


@pytest.mark.asyncio
async def test_poll_bootstrap_no_notify(store: KworkInboxSeenStore) -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = _eval_queue(
        "Все",
        {
            "ok": True,
            "href": CHATS_URL,
            "count": 1,
            "dialogs": [
                {
                    "chat_id": CHAT_ID,
                    "title": TITLE,
                    "author": "Nikolay:",
                    "last_preview": "Nikolay: уже было",
                    "chat_url": CHATS_URL,
                    "is_own_last": False,
                }
            ],
        },
    )
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_yandex_inbox(
        settings=MagicMock(yandex_storage_state=""),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["bootstrapped"] == 1
    assert result["notified"] == 0
    assert notified == []
    assert store.is_bootstrapped() is True
    expected = make_fingerprint_mid(CHAT_ID, "Nikolay: уже было")
    assert store.get_last_mid(CHAT_ID) == expected


@pytest.mark.asyncio
async def test_poll_notifies_incoming_preview_change(
    store: KworkInboxSeenStore,
) -> None:
    old_fp = make_fingerprint_mid(CHAT_ID, "Nikolay: старое")
    store.set_last_mid(CHAT_ID, TITLE, old_fp)
    store.mark_bootstrap_done()

    browser = MagicMock()
    browser.evaluate.side_effect = _eval_queue(
        "Новые",
        {
            "ok": True,
            "href": CHATS_URL,
            "count": 1,
            "dialogs": [
                {
                    "chat_id": CHAT_ID,
                    "title": TITLE,
                    "author": "Nikolay:",
                    "last_preview": "Nikolay: новое от клиента",
                    "chat_url": CHATS_URL,
                    "is_own_last": False,
                }
            ],
        },
    )
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_yandex_inbox(
        settings=MagicMock(yandex_storage_state=""),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 1
    assert len(notified) == 1
    assert "Яндекс Мессенджер · входящее" in notified[0]
    assert TITLE in notified[0]
    assert CHATS_URL in notified[0]
    assert "новое от клиента" not in notified[0]
    assert store.get_last_mid(CHAT_ID) == make_fingerprint_mid(
        CHAT_ID, "Nikolay: новое от клиента"
    )


@pytest.mark.asyncio
async def test_poll_skips_own_preview_but_updates_mid(
    store: KworkInboxSeenStore,
) -> None:
    old_fp = make_fingerprint_mid(CHAT_ID, "Nikolay: старое")
    store.set_last_mid(CHAT_ID, TITLE, old_fp)
    store.mark_bootstrap_done()

    browser = MagicMock()
    browser.evaluate.side_effect = _eval_queue(
        "Новые",
        {
            "ok": True,
            "href": CHATS_URL,
            "count": 1,
            "dialogs": [
                {
                    "chat_id": CHAT_ID,
                    "title": TITLE,
                    "author": "Вы:",
                    "last_preview": "Вы: ответил",
                    "chat_url": CHATS_URL,
                    "is_own_last": True,
                }
            ],
        },
    )
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_yandex_inbox(
        settings=MagicMock(yandex_storage_state=""),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 0
    assert notified == []
    assert store.get_last_mid(CHAT_ID) == make_fingerprint_mid(CHAT_ID, "Вы: ответил")


@pytest.mark.asyncio
async def test_poll_unreliable_empty_preview_no_notify_no_mid_poison(
    store: KworkInboxSeenStore,
) -> None:
    old_fp = make_fingerprint_mid(CHAT_ID, "Nikolay: старое")
    store.set_last_mid(CHAT_ID, TITLE, old_fp)
    store.mark_bootstrap_done()

    browser = MagicMock()
    browser.evaluate.side_effect = _eval_queue(
        "Новые",
        {
            "ok": True,
            "href": CHATS_URL,
            "count": 1,
            "dialogs": [
                {
                    "chat_id": CHAT_ID,
                    "title": TITLE,
                    "author": "",
                    "last_preview": "",
                    "chat_url": CHATS_URL,
                    "is_own_last": False,
                }
            ],
        },
    )
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_yandex_inbox(
        settings=MagicMock(yandex_storage_state=""),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 0
    assert result["unreliable"] == 1
    assert notified == []
    assert store.get_last_mid(CHAT_ID) == old_fp


@pytest.mark.asyncio
async def test_poll_silent_adopt_stored_none(store: KworkInboxSeenStore) -> None:
    store.mark_bootstrap_done()
    assert store.get_last_mid(CHAT_ID) is None

    browser = MagicMock()
    browser.evaluate.side_effect = _eval_queue(
        "Новые",
        {
            "ok": True,
            "href": CHATS_URL,
            "count": 1,
            "dialogs": [
                {
                    "chat_id": CHAT_ID,
                    "title": TITLE,
                    "author": "Nikolay:",
                    "last_preview": "Nikolay: первое появление",
                    "chat_url": CHATS_URL,
                    "is_own_last": False,
                }
            ],
        },
    )
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_yandex_inbox(
        settings=MagicMock(yandex_storage_state=""),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 0
    assert result["silent_adopt"] == 1
    assert notified == []
    assert store.get_last_mid(CHAT_ID) == make_fingerprint_mid(
        CHAT_ID, "Nikolay: первое появление"
    )


@pytest.mark.asyncio
async def test_poll_skips_when_novye_tab_missing(store: KworkInboxSeenStore) -> None:
    """If tab «Новые» is missing, do not scrape the default list (false spam)."""
    old_fp = make_fingerprint_mid(CHAT_ID, "Nikolay: старое")
    store.set_last_mid(CHAT_ID, TITLE, old_fp)
    store.mark_bootstrap_done()

    browser = MagicMock()
    browser.evaluate.side_effect = [{"ok": False, "tab": "Новые"}]
    notified: list[str] = []

    async def notify(text: str) -> None:
        notified.append(text)

    result = await poll_yandex_inbox(
        settings=MagicMock(yandex_storage_state=""),
        browser=browser,
        store=store,
        notify=notify,
    )
    assert result["notified"] == 0
    assert result.get("tab_missing") == 1
    assert result["fetched"] == 0
    assert notified == []
    assert store.get_last_mid(CHAT_ID) == old_fp
    # only tab probe evaluate — no list_dialogs scrape
    assert browser.evaluate.call_count == 1


@pytest.mark.asyncio
async def test_poll_auth_error(store: KworkInboxSeenStore) -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        _tab_ok("Новые"),
        {
            "href": "https://passport.yandex.ru/auth",
            "not_logged_in": True,
            "body_snip": "Войдите",
        },
    ]
    store.mark_bootstrap_done()

    with pytest.raises(YandexAuthError):
        await poll_yandex_inbox(
            settings=MagicMock(yandex_storage_state=""),
            browser=browser,
            store=store,
            notify=MagicMock(),
        )


def test_client_list_dialogs_mock_browser() -> None:
    browser = MagicMock()
    browser.evaluate.side_effect = [
        {"href": CHATS_URL, "not_logged_in": False, "body_snip": ""},
        {
            "ok": True,
            "href": CHATS_URL,
            "count": 1,
            "dialogs": [
                {
                    "chat_id": "4219269039",
                    "title": "T",
                    "author": "Nikolay:",
                    "last_preview": "Nikolay: m",
                    "chat_url": CHATS_URL,
                    "is_own_last": False,
                }
            ],
        },
    ]
    client = YandexInboxClient(browser)
    client._chats_ready = True
    dialogs = client.list_dialogs()
    assert len(dialogs) == 1
    assert dialogs[0].chat_id == "4219269039"
    assert dialogs[0].chat_url == CHATS_URL
