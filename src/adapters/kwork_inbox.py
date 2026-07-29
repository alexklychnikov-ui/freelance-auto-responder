from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
from typing import Any

from src.browser.base import BrowserClient

logger = logging.getLogger(__name__)

INBOX_URL = "https://kwork.ru/inbox"

FETCH_DIALOGS_JS = """
async () => {
  const fd = new FormData();
  const r = await fetch('https://kwork.ru/getdialogs', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json, text/javascript, */*; q=0.01',
    },
    body: fd,
  });
  const text = await r.text();
  try {
    return { ok: true, status: r.status, json: JSON.parse(text) };
  } catch (e) {
    return { ok: false, status: r.status, error: String(e), text: text.slice(0, 500) };
  }
}
"""

FETCH_NEW_MESSAGES_JS_TEMPLATE = """
async () => {{
  const body = new URLSearchParams();
  body.set('interlocutorId', {interlocutor_id_json});
  body.set('lastMessageId', {last_message_id_json});
  const r = await fetch('https://kwork.ru/api/inbox/getnewmessages', {{
    method: 'POST',
    credentials: 'include',
    headers: {{
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json, text/javascript, */*; q=0.01',
      'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    }},
    body: body.toString(),
  }});
  const text = await r.text();
  try {{
    return {{ ok: true, status: r.status, json: JSON.parse(text) }};
  }} catch (e) {{
    return {{ ok: false, status: r.status, error: String(e), text: text.slice(0, 500) }};
  }}
}}
"""

DETECT_SELF_USER_JS = """
(() => {
  const w = window;
  const candidates = [
    w.USERID,
    w.userId,
    w.USER_ID,
    w.actor && (w.actor.id || w.actor.USERID),
    w.USER && (w.USER.id || w.USER.USERID),
    w.__INITIAL_STATE__ && w.__INITIAL_STATE__.user && (
      w.__INITIAL_STATE__.user.id || w.__INITIAL_STATE__.user.USERID
    ),
  ];
  for (const c of candidates) {
    if (c == null || c === '') continue;
    const n = Number(c);
    if (Number.isFinite(n) && n > 0) return String(n);
    const s = String(c).trim();
    if (/^\\d+$/.test(s)) return s;
  }
  return null;
})()
"""


@dataclass(frozen=True)
class InboxMessage:
    mid: int
    from_id: str
    to_id: str
    text: str
    username: str = ""
    time: str | None = None


@dataclass(frozen=True)
class DialogPreview:
    interlocutor_id: str
    username: str
    mid: int
    unread_count: int
    message: str
    is_support: bool
    last_message: InboxMessage | None = None


def unescape_message(text: str | None) -> str:
    return html.unescape(str(text or "")).strip()


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


def parse_inbox_message(
    raw: dict[str, Any] | None,
    *,
    username: str = "",
) -> InboxMessage | None:
    if not isinstance(raw, dict):
        return None
    mid = _as_int(raw.get("MID") or raw.get("inbox_message_id") or raw.get("mid"))
    if mid <= 0:
        return None
    from_id = _as_str_id(
        raw.get("MSGFROM") or raw.get("mfrom") or raw.get("from_id") or raw.get("fromId")
    )
    to_id = _as_str_id(
        raw.get("MSGTO") or raw.get("mto") or raw.get("to_id") or raw.get("toId")
    )
    text = unescape_message(
        raw.get("message") or raw.get("text") or raw.get("msg") or ""
    )
    time_val = raw.get("time") or raw.get("TIME") or raw.get("created_at")
    return InboxMessage(
        mid=mid,
        from_id=from_id,
        to_id=to_id,
        text=text,
        username=username,
        time=str(time_val) if time_val is not None else None,
    )


def parse_dialog_row(raw: dict[str, Any]) -> DialogPreview | None:
    if not isinstance(raw, dict):
        return None
    interlocutor_id = _as_str_id(raw.get("USERID") or raw.get("user_id") or raw.get("id"))
    username = str(raw.get("username") or raw.get("login") or "").strip()
    if not interlocutor_id:
        return None
    mid = _as_int(raw.get("MID"))
    last_raw = raw.get("lastMessage")
    last_msg = parse_inbox_message(
        last_raw if isinstance(last_raw, dict) else None,
        username=username,
    )
    if mid <= 0 and last_msg is not None:
        mid = last_msg.mid
    is_support = bool(raw.get("is_support") or raw.get("isSupport"))
    if not is_support and username.lower() == "support":
        is_support = True
    return DialogPreview(
        interlocutor_id=interlocutor_id,
        username=username,
        mid=mid,
        unread_count=_as_int(raw.get("unread_count") or raw.get("unreadCount")),
        message=unescape_message(raw.get("message")),
        is_support=is_support,
        last_message=last_msg,
    )


def parse_dialogs_payload(payload: Any) -> list[DialogPreview]:
    data = payload
    if isinstance(payload, dict) and "json" in payload:
        data = payload.get("json")
    if not isinstance(data, dict):
        return []
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    rows = root.get("rows") if isinstance(root, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[DialogPreview] = []
    for row in rows:
        parsed = parse_dialog_row(row) if isinstance(row, dict) else None
        if parsed is not None:
            out.append(parsed)
    return out


def parse_new_messages_payload(
    payload: Any,
    *,
    username: str = "",
) -> list[InboxMessage]:
    data = payload
    if isinstance(payload, dict) and "json" in payload:
        data = payload.get("json")
    if not isinstance(data, dict):
        return []
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        return []
    raw_list = (
        root.get("messagesData")
        or root.get("messages")
        or root.get("items")
        or []
    )
    if not isinstance(raw_list, list):
        return []
    out: list[InboxMessage] = []
    for item in raw_list:
        msg = parse_inbox_message(item if isinstance(item, dict) else None, username=username)
        if msg is not None:
            out.append(msg)
    out.sort(key=lambda m: m.mid)
    return out


def detect_self_user_id_from_dialogs(dialogs: list[DialogPreview]) -> str | None:
    for d in dialogs:
        lm = d.last_message
        if lm is None:
            continue
        if lm.from_id == d.interlocutor_id and lm.to_id:
            return lm.to_id
        if lm.to_id == d.interlocutor_id and lm.from_id:
            return lm.from_id
        if lm.from_id and lm.from_id != d.interlocutor_id:
            return lm.from_id
    return None


def chat_url(username: str) -> str:
    return f"https://kwork.ru/inbox/{username}"


def format_inbox_notify(username: str, message_text: str) -> str:
    safe_user = html.escape(username or "?", quote=False)
    safe_url = html.escape(chat_url(username or ""), quote=True)
    safe_text = html.escape(message_text or "", quote=False)
    return (
        "💬 Kwork · входящее\n"
        f"👤 {safe_user}\n"
        f"🔗 {safe_url}\n\n"
        f"{safe_text}"
    )


class KworkInboxClient:
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

    def detect_self_user_id(self, dialogs: list[DialogPreview] | None = None) -> str | None:
        if self.self_user_id:
            return self.self_user_id
        try:
            raw = self.browser.evaluate(DETECT_SELF_USER_JS)
            if raw:
                self.self_user_id = str(raw).strip()
        except Exception:
            logger.debug("kwork_inbox_self_detect_js_failed", exc_info=True)
        if not self.self_user_id and dialogs:
            self.self_user_id = detect_self_user_id_from_dialogs(dialogs)
        return self.self_user_id

    def fetch_dialogs(self) -> list[DialogPreview]:
        self.ensure_inbox_page()
        raw = self.browser.evaluate(FETCH_DIALOGS_JS)
        if not isinstance(raw, dict) or not raw.get("ok"):
            logger.warning("kwork_getdialogs_failed payload=%s", raw)
            return []
        dialogs = parse_dialogs_payload(raw)
        self.detect_self_user_id(dialogs)
        return dialogs

    def fetch_new_messages(
        self,
        interlocutor_id: str,
        last_message_id: int,
        *,
        username: str = "",
    ) -> list[InboxMessage]:
        self.ensure_inbox_page()
        js = FETCH_NEW_MESSAGES_JS_TEMPLATE.format(
            interlocutor_id_json=json.dumps(str(interlocutor_id)),
            last_message_id_json=json.dumps(str(int(last_message_id))),
        )
        raw = self.browser.evaluate(js)
        if not isinstance(raw, dict) or not raw.get("ok"):
            logger.warning(
                "kwork_getnewmessages_failed interlocutor=%s payload=%s",
                interlocutor_id,
                raw,
            )
            return []
        return parse_new_messages_payload(raw, username=username)
