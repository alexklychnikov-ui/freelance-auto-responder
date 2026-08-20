from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.browser.base import BrowserClient

logger = logging.getLogger(__name__)

INBOX_URL = "https://www.fl.ru/messages/"
CHAT_URL = "https://www.fl.ru/messages/"
ATTACHMENT_PLACEHOLDER = "(вложение)"

FETCH_OFFERS_JS = """
async () => {
  const url = '/projects/offers/?limit=20&dialogues=1&deleted=1&sort=lastMessage&offset=0';
  const r = await fetch(url, {
    credentials: 'include',
    headers: {
      'Accept': 'application/json, text/plain, */*',
      'X-Requested-With': 'XMLHttpRequest',
    },
  });
  const text = await r.text();
  try {
    return { ok: true, status: r.status, json: JSON.parse(text) };
  } catch (e) {
    return { ok: false, status: r.status, error: String(e), text: text.slice(0, 500) };
  }
}
"""

FETCH_THREAD_JS_TEMPLATE = """
async () => {{
  const url = '/projects/{project_id}/offers/{offer_id}/messages/?limit=50&offset=0';
  const r = await fetch(url, {{
    credentials: 'include',
    headers: {{
      'Accept': 'application/json, text/plain, */*',
      'X-Requested-With': 'XMLHttpRequest',
    }},
  }});
  const text = await r.text();
  try {{
    return {{ ok: true, status: r.status, json: JSON.parse(text) }};
  }} catch (e) {{
    return {{ ok: false, status: r.status, error: String(e), text: text.slice(0, 500) }};
  }}
}}
"""


@dataclass(frozen=True)
class FlruInboxMessage:
    mid: int
    from_id: str
    text: str
    offer_id: str = ""
    project_id: str = ""
    time: str | None = None


@dataclass(frozen=True)
class OfferPreview:
    offer_id: str
    project_id: str
    title: str
    mid: int
    frl_new_msg_count: int
    last_message_at: str | None
    last_message: FlruInboxMessage | None = None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_message_text(text: str | None) -> str:
    body = html.unescape(str(text or "")).strip()
    return body or ATTACHMENT_PLACEHOLDER


def _path_id(value: str) -> str:
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        raise ValueError(f"invalid flru path id: {value!r}")
    return digits


def parse_flru_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except ValueError:
            continue
    return None


def parse_inbox_message(raw: dict[str, Any] | None) -> FlruInboxMessage | None:
    if not isinstance(raw, dict):
        return None
    mid = _as_int(raw.get("id") or raw.get("MID") or raw.get("mid"))
    if mid <= 0:
        return None
    from_id = _as_str_id(raw.get("from_id") or raw.get("fromId"))
    text = normalize_message_text(raw.get("text") or raw.get("message"))
    time_val = raw.get("time") or raw.get("created_at")
    return FlruInboxMessage(
        mid=mid,
        from_id=from_id,
        text=text,
        offer_id=_as_str_id(raw.get("offer_id") or raw.get("offerId")),
        project_id=_as_str_id(raw.get("project_id") or raw.get("projectId")),
        time=str(time_val) if time_val is not None else None,
    )


def _messages_index(payload: dict[str, Any]) -> dict[str, FlruInboxMessage]:
    raw_messages = payload.get("messages")
    out: dict[str, FlruInboxMessage] = {}
    if isinstance(raw_messages, dict):
        for key, value in raw_messages.items():
            msg = parse_inbox_message(value if isinstance(value, dict) else None)
            if msg is not None:
                out[str(key)] = msg
        return out
    if not isinstance(raw_messages, list):
        return out
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        msg = parse_inbox_message(item)
        if msg is None:
            continue
        key = msg.offer_id or _as_str_id(item.get("offer_id"))
        if key:
            out[key] = msg
    return out


def parse_offer_row(raw: dict[str, Any], *, last_message: FlruInboxMessage | None) -> OfferPreview | None:
    if not isinstance(raw, dict):
        return None
    offer_id = _as_str_id(raw.get("id") or raw.get("offer_id"))
    project_id = _as_str_id(raw.get("project_id") or raw.get("projectId"))
    if not offer_id:
        return None
    title = str(raw.get("title") or "").strip()
    mid = _as_int(raw.get("last_message_id"))
    if mid <= 0 and last_message is not None:
        mid = last_message.mid
    if mid <= 0:
        embedded = raw.get("messages")
        if isinstance(embedded, list) and embedded:
            lm = parse_inbox_message(embedded[-1] if isinstance(embedded[-1], dict) else None)
            if lm is not None:
                last_message = lm
                mid = lm.mid
        elif isinstance(embedded, dict):
            lm = parse_inbox_message(embedded)
            if lm is not None:
                last_message = lm
                mid = lm.mid
    last_message_at = raw.get("last_message_at") or raw.get("lastMessageAt")
    time_str = str(last_message_at) if last_message_at is not None else None
    if last_message is not None and not last_message.offer_id:
        last_message = FlruInboxMessage(
            mid=last_message.mid,
            from_id=last_message.from_id,
            text=last_message.text,
            offer_id=offer_id,
            project_id=project_id,
            time=last_message.time or time_str,
        )
    return OfferPreview(
        offer_id=offer_id,
        project_id=project_id,
        title=title,
        mid=mid,
        frl_new_msg_count=_as_int(raw.get("frl_new_msg_count") or raw.get("frlNewMsgCount")),
        last_message_at=time_str,
        last_message=last_message,
    )


def parse_offers_payload(payload: Any) -> list[OfferPreview]:
    data = payload
    if isinstance(payload, dict) and "json" in payload:
        data = payload.get("json")
    if not isinstance(data, dict):
        return []
    msg_index = _messages_index(data)
    rows = data.get("items")
    if not isinstance(rows, list):
        return []
    out: list[OfferPreview] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        offer_id = _as_str_id(row.get("id") or row.get("offer_id"))
        last_message = msg_index.get(offer_id)
        parsed = parse_offer_row(row, last_message=last_message)
        if parsed is not None:
            out.append(parsed)
    return out


def detect_self_user_id_from_offers(items: list[dict[str, Any]]) -> str | None:
    for row in items:
        if not isinstance(row, dict):
            continue
        author = row.get("author")
        if isinstance(author, dict):
            author_id = _as_str_id(author.get("id"))
            if author_id:
                return author_id
    return None


def detect_self_user_id(payload: Any) -> str | None:
    data = payload
    if isinstance(payload, dict) and "json" in payload:
        data = payload.get("json")
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if isinstance(items, list):
        return detect_self_user_id_from_offers(items)
    return None


def parse_thread_payload(payload: Any) -> list[FlruInboxMessage]:
    data = payload
    if isinstance(payload, dict) and "json" in payload:
        data = payload.get("json")
    if not isinstance(data, dict):
        return []
    raw_list = data.get("items") or data.get("messages") or []
    if not isinstance(raw_list, list):
        return []
    out: list[FlruInboxMessage] = []
    for item in raw_list:
        msg = parse_inbox_message(item if isinstance(item, dict) else None)
        if msg is not None:
            out.append(msg)
    out.sort(key=lambda m: m.mid)
    return out


def should_fetch_thread(
    dialog: OfferPreview,
    *,
    stored_mid: int,
) -> bool:
    if dialog.mid > stored_mid:
        return True
    if dialog.frl_new_msg_count > 0:
        return True
    return False


def format_inbox_notify(title: str, message_text: str) -> str:
    safe_title = html.escape(title or "?", quote=False)
    safe_url = html.escape(CHAT_URL, quote=True)
    safe_text = html.escape(message_text or "", quote=False)
    lines = ["💬 FL.ru · входящее"]
    if title:
        lines.append(f"📋 {safe_title}")
    lines.append(f"🔗 {safe_url}")
    lines.append("")
    lines.append(safe_text)
    return "\n".join(lines)


class FlruInboxClient:
    def __init__(self, browser: BrowserClient) -> None:
        self.browser = browser
        self.self_user_id: str | None = None
        self._inbox_ready = False

    def ensure_inbox_page(self) -> None:
        if self._inbox_ready:
            return
        self.browser.navigate(INBOX_URL)
        self.browser.wait_ms(1500)
        self._inbox_ready = True

    def detect_self_user_id(self, raw_list_payload: Any | None = None) -> str | None:
        if self.self_user_id:
            return self.self_user_id
        if raw_list_payload is not None:
            self.self_user_id = detect_self_user_id(raw_list_payload)
        return self.self_user_id

    def fetch_offers(self) -> tuple[list[OfferPreview], Any | None]:
        self.ensure_inbox_page()
        raw = self.browser.evaluate(FETCH_OFFERS_JS)
        if not isinstance(raw, dict) or not raw.get("ok"):
            logger.warning("flru_offers_list_failed payload=%s", raw)
            return [], raw
        offers = parse_offers_payload(raw)
        self.detect_self_user_id(raw)
        return offers, raw

    def fetch_thread_messages(
        self,
        project_id: str,
        offer_id: str,
    ) -> list[FlruInboxMessage]:
        self.ensure_inbox_page()
        js = FETCH_THREAD_JS_TEMPLATE.format(
            project_id=_path_id(project_id),
            offer_id=_path_id(offer_id),
        )
        raw = self.browser.evaluate(js)
        if not isinstance(raw, dict) or not raw.get("ok"):
            logger.warning(
                "flru_thread_fetch_failed project=%s offer=%s payload=%s",
                project_id,
                offer_id,
                raw,
            )
            return []
        return parse_thread_payload(raw)
