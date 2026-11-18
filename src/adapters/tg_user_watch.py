from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import AuthKeyError, SessionPasswordNeededError, UserDeactivatedError
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import (
    Channel,
    Chat,
    ChatInviteAlready,
    MessageService,
    User,
)

logger = logging.getLogger(__name__)

PREVIEW_MAX = 400


class TgUserAuthError(Exception):
    """USER session missing, expired, or not authorized."""


@dataclass(frozen=True)
class WatchMessage:
    mid: int
    author: str
    text: str
    is_forward: bool


def normalize_invite_hash(invite: str) -> str:
    raw = (invite or "").strip()
    if not raw:
        return ""
    if "t.me/+" in raw:
        raw = raw.split("t.me/+", 1)[1]
    elif "joinchat/" in raw:
        raw = raw.split("joinchat/", 1)[1]
    raw = raw.split("?", 1)[0].strip().strip("/")
    return raw.lstrip("+")


def invite_url(invite_hash: str) -> str:
    h = normalize_invite_hash(invite_hash)
    return f"https://t.me/+{h}" if h else "https://t.me/"


def truncate_preview(text: str, max_len: int = PREVIEW_MAX) -> str:
    body = (text or "").strip()
    if len(body) <= max_len:
        return body
    return body[: max_len - 1].rstrip() + "…"


def format_watch_notify(
    *,
    author: str,
    text: str,
    is_forward: bool,
    invite_hash: str,
) -> str:
    safe_author = html.escape((author or "?").strip() or "?", quote=False)
    safe_text = html.escape(truncate_preview(text), quote=False)
    safe_url = html.escape(invite_url(invite_hash), quote=True)
    lines = [
        "🔔 VIP-чат · новое",
        f"👤 {safe_author}",
    ]
    if is_forward:
        lines.append("↪️ пересыл")
    lines.append(f"📄 {safe_text}")
    lines.append(f"🔗 {safe_url}")
    return "\n".join(lines)


def is_forward_message(msg: Any) -> bool:
    if getattr(msg, "fwd_from", None) is not None:
        return True
    if getattr(msg, "forward", None) is not None:
        return True
    return False


def message_is_skippable(msg: Any) -> bool:
    if msg is None:
        return True
    if isinstance(msg, MessageService):
        return True
    if getattr(msg, "action", None) is not None:
        return True
    mid = int(getattr(msg, "id", 0) or 0)
    if mid <= 0:
        return True
    text = (getattr(msg, "message", None) or "").strip()
    media = getattr(msg, "media", None)
    if not text and media is None:
        return True
    return False


def author_display_name(sender: Any) -> str:
    if sender is None:
        return "?"
    if isinstance(sender, User):
        parts = [
            (sender.first_name or "").strip(),
            (sender.last_name or "").strip(),
        ]
        name = " ".join(p for p in parts if p).strip()
        username = (sender.username or "").strip()
        if name and username:
            return f"{name} (@{username})"
        if name:
            return name
        if username:
            return f"@{username}"
        return str(sender.id)
    title = (getattr(sender, "title", None) or "").strip()
    if title:
        return title
    return str(getattr(sender, "id", "?") or "?")


def select_new_messages(
    messages: list[WatchMessage],
    *,
    last_mid: int,
) -> list[WatchMessage]:
    out = [m for m in messages if m.mid > last_mid]
    out.sort(key=lambda m: m.mid)
    return out


def parse_socks_proxy(url: str) -> tuple | None:
    """Parse socks5/socks5h/http proxy URL for Telethon."""
    raw = (url or "").strip()
    if not raw:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        raise TgUserAuthError(f"invalid proxy URL: {raw!r}")
    user = parsed.username
    password = parsed.password
    if scheme in ("socks5", "socks5h"):
        from python_socks import ProxyType

        return (ProxyType.SOCKS5, host, int(port), True, user, password)
    if scheme in ("http", "https"):
        from python_socks import ProxyType

        return (ProxyType.HTTP, host, int(port), True, user, password)
    raise TgUserAuthError(f"unsupported proxy scheme: {scheme!r}")


def build_telegram_client(
    *,
    api_id: int,
    api_hash: str,
    session_path: str,
    proxy_url: str = "",
) -> TelegramClient:
    if int(api_id or 0) <= 0:
        raise TgUserAuthError("TG_USER_API_ID is missing or invalid")
    if not (api_hash or "").strip():
        raise TgUserAuthError("TG_USER_API_HASH is missing")
    path = Path(session_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    proxy = parse_socks_proxy(proxy_url)
    if proxy is None:
        return TelegramClient(str(path), int(api_id), api_hash.strip())
    return TelegramClient(str(path), int(api_id), api_hash.strip(), proxy=proxy)


async def ensure_user_authorized(client: TelegramClient) -> None:
    try:
        if not await client.is_user_authorized():
            raise TgUserAuthError(
                "Telethon session not authorized — run deploy/tg_user_login_interactive.py"
            )
    except (AuthKeyError, UserDeactivatedError) as exc:
        raise TgUserAuthError(str(exc)) from exc


async def resolve_invite_entity(client: TelegramClient, invite_hash: str) -> Any:
    h = normalize_invite_hash(invite_hash)
    if not h:
        raise TgUserAuthError("TG_USER_WATCH_INVITE is empty")
    try:
        result = await client(CheckChatInviteRequest(h))
    except Exception as exc:
        logger.warning("tg_user_watch_check_invite_failed: %s", exc)
        # Already a member → entity by invite link often works
        return await client.get_entity(invite_url(h))

    if isinstance(result, ChatInviteAlready):
        return result.chat
    try:
        updates = await client(ImportChatInviteRequest(h))
    except Exception as exc:
        raise TgUserAuthError(f"cannot join invite: {exc}") from exc
    chats = list(getattr(updates, "chats", None) or [])
    if not chats:
        raise TgUserAuthError("invite join returned no chats")
    return chats[0]


def entity_chat_key(entity: Any) -> str:
    eid = getattr(entity, "id", None)
    if eid is not None:
        return str(eid)
    return "unknown"


def entity_title(entity: Any) -> str:
    title = (getattr(entity, "title", None) or "").strip()
    if title:
        return title
    if isinstance(entity, User):
        return author_display_name(entity)
    if isinstance(entity, (Channel, Chat)):
        return str(entity.id)
    return "vip"


async def fetch_watch_messages_after(
    client: TelegramClient,
    entity: Any,
    *,
    min_id: int,
    limit: int = 50,
) -> list[WatchMessage]:
    raw = await client.get_messages(entity, min_id=max(0, int(min_id)), limit=limit)
    out: list[WatchMessage] = []
    for msg in reversed(list(raw or [])):
        if message_is_skippable(msg):
            continue
        try:
            sender = await msg.get_sender()
        except Exception:
            sender = getattr(msg, "sender", None)
        text = (getattr(msg, "message", None) or "").strip()
        if not text and getattr(msg, "media", None) is not None:
            text = "[медиа]"
        out.append(
            WatchMessage(
                mid=int(msg.id),
                author=author_display_name(sender),
                text=text,
                is_forward=is_forward_message(msg),
            )
        )
    return out


async def fetch_latest_mid(client: TelegramClient, entity: Any) -> int:
    raw = await client.get_messages(entity, limit=1)
    if not raw:
        return 0
    msg = raw[0]
    return int(getattr(msg, "id", 0) or 0)


async def connect_resolve_fetch(
    *,
    api_id: int,
    api_hash: str,
    session_path: str,
    invite_hash: str,
    min_id: int,
    bootstrap: bool,
    proxy_url: str = '',
) -> tuple[str, str, list[WatchMessage], int]:
    """
    Connect USER session, resolve invite entity, fetch messages.
    Returns (chat_key, chat_title, messages, latest_mid_hint).
    """
    client = build_telegram_client(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        proxy_url=proxy_url,
    )
    try:
        await client.connect()
        await ensure_user_authorized(client)
        entity = await resolve_invite_entity(client, invite_hash)
        chat_key = entity_chat_key(entity)
        title = entity_title(entity)
        if bootstrap:
            latest = await fetch_latest_mid(client, entity)
            return chat_key, title, [], latest
        messages = await fetch_watch_messages_after(
            client, entity, min_id=min_id
        )
        latest = messages[-1].mid if messages else min_id
        return chat_key, title, messages, latest
    except SessionPasswordNeededError as exc:
        raise TgUserAuthError(
            "2FA password required — complete login via deploy/tg_user_login_interactive.py"
        ) from exc
    except TgUserAuthError:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "auth" in msg or "session" in msg or "unauthorized" in msg:
            raise TgUserAuthError(str(exc)) from exc
        raise
    finally:
        await client.disconnect()
