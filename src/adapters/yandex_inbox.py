from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from src.adapters.yandex_uslugi import YandexAuthError, _is_login_url
from src.browser.base import BrowserClient

logger = logging.getLogger(__name__)

CHATS_URL = "https://telemost.yandex.ru/chats"
OWN_PREVIEW_PREFIX = "Вы:"
OWN_AUTHOR_MARKERS = frozenset({"Вы:", "You:"})
_NAME_ID_RE = re.compile(r"^(\d+)_name$")
_WS_RE = re.compile(r"\s+")

LIST_DIALOGS_JS = """
() => {
  const CHATS = 'https://telemost.yandex.ru/chats';
  const ownAuthors = new Set(['Вы:', 'You:']);
  const seen = new Set();
  const out = [];

  const textOf = (el) => {
    if (!el) return '';
    return String(el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  };

  const rows = Array.from(
    document.querySelectorAll(
      'div.yamb-chat-list-item, [role="link"].yamb-chat-list-item, .yamb-chat-list-item'
    )
  );

  for (const row of rows) {
    const nameEl =
      row.querySelector('.yamb-chat-list-item__name') ||
      row.querySelector('[id$="_name"]');
    const title = textOf(nameEl);
    if (!title) continue;

    let chatId = '';
    const nameId = nameEl && nameEl.id ? String(nameEl.id) : '';
    const m = nameId.match(/^(\\d+)_name$/);
    if (m) chatId = m[1];
    if (!chatId) {
      const labelled = row.getAttribute('aria-labelledby') || '';
      const first = labelled.trim().split(/\\s+/)[0] || '';
      const m2 = first.match(/^(\\d+)/);
      if (m2) chatId = m2[1];
    }
    if (!chatId || seen.has(chatId)) continue;

    const authorEl = row.querySelector('.yamb-chat-list-item__author');
    let author = textOf(authorEl);
    if (author && !author.endsWith(':')) {
      // keep as-is
    }

    let preview = '';
    const textEl =
      (chatId && document.getElementById(chatId + '_text')) ||
      row.querySelector('[id$="_text"]') ||
      row.querySelector('.yamb-chat-list-item__text') ||
      row.querySelector('.yamb-chat-list-item__message');
    if (textEl) {
      preview = textOf(textEl);
      // text node may include author prefix duplicated — strip leading author
      if (author && preview.startsWith(author)) {
        preview = preview.slice(author.length).trim();
      }
    }
    if (!preview) {
      const lines = String(row.innerText || '')
        .replace(/\\r/g, '')
        .split('\\n')
        .map((s) => s.trim())
        .filter(Boolean);
      for (const line of lines) {
        if (line === title) continue;
        if (/^\\d{1,2}:\\d{2}$/.test(line)) continue;
        if (author && line === author) continue;
        preview = line;
        break;
      }
    }

    const isOwn =
      ownAuthors.has(author) ||
      preview.startsWith('Вы:') ||
      preview.startsWith('You:');

    let lastPreview = preview;
    if (author) {
      lastPreview = author + (preview ? ' ' + preview : '');
    }

    // null = unread node absent (unknown); 0 = explicit empty/zero badge
    let unreadCount = null;
    if (chatId) {
      const unreadEl = document.getElementById(chatId + '_unread');
      if (unreadEl) {
        const unreadRaw = textOf(unreadEl);
        unreadCount = /^\\d+$/.test(unreadRaw) ? parseInt(unreadRaw, 10) : 0;
      }
    }

    seen.add(chatId);
    out.push({
      chat_id: chatId,
      title,
      author,
      last_preview: lastPreview,
      chat_url: CHATS,
      is_own_last: isOwn,
      unread_count: unreadCount,
    });
  }

  return {
    ok: true,
    href: location.href,
    count: out.length,
    dialogs: out,
  };
}
"""

AUTH_PROBE_JS = """
() => {
  const href = location.href || '';
  const body = (document.body && (document.body.innerText || '')) || '';
  const low = (href + ' ' + body.slice(0, 2000)).toLowerCase();
  const notLogged =
    href.toLowerCase().includes('passport.yandex') ||
    (href.toLowerCase().includes('login') && href.toLowerCase().includes('yandex')) ||
    low.includes('войдите') && low.includes('passport') ||
    low.includes('войти в аккаунт') ||
    low.includes('authorize');
  return { href, not_logged_in: !!notLogged, body_snip: body.slice(0, 200) };
}
"""


@dataclass(frozen=True)
class DialogPreview:
    chat_id: str
    title: str
    last_preview: str
    chat_url: str
    is_own_last: bool
    fingerprint_mid: int
    author: str = ""
    unread_count: int | None = None


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def _preview_body_without_prefixes(preview: str, author: str = "") -> str:
    text = _collapse_ws(preview)
    for prefix in (author, OWN_PREVIEW_PREFIX, "You:"):
        p = _collapse_ws(prefix)
        if p and text.startswith(p):
            text = text[len(p) :].lstrip(" :").strip()
            text = _collapse_ws(text)
    return text


def is_dialog_parse_reliable(dialog: DialogPreview) -> bool:
    title = _collapse_ws(dialog.title)
    if not title:
        return False
    author = _collapse_ws(dialog.author)
    body = _preview_body_without_prefixes(dialog.last_preview, author)
    if len(body) >= 2:
        return True
    if author:
        return True
    return False


def is_own_last_preview(preview: str | None) -> bool:
    text = str(preview or "").strip()
    if text.startswith(OWN_PREVIEW_PREFIX):
        return True
    if text.startswith("You:"):
        return True
    return False


def is_own_last_author(author: str | None) -> bool:
    text = str(author or "").strip()
    return text in OWN_AUTHOR_MARKERS


def is_own_last_message(*, author: str | None = None, preview: str | None = None) -> bool:
    if is_own_last_author(author):
        return True
    if is_own_last_preview(preview):
        return True
    return False


def make_fingerprint_mid(chat_id: str, last_preview: str) -> int:
    preview = _collapse_ws(last_preview)
    raw = f"{_collapse_ws(chat_id)}|{preview}".encode("utf-8")
    digest = hashlib.md5(raw).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


def chat_url_for(_chat_id: str = "", href: str | None = None) -> str:
    if href:
        raw = str(href).strip()
        if raw.startswith("http") and "telemost.yandex.ru/chats" in raw:
            return raw.split("#")[0]
    return CHATS_URL


def chat_id_from_name_element_id(element_id: str | None) -> str:
    raw = str(element_id or "").strip()
    m = _NAME_ID_RE.match(raw)
    return m.group(1) if m else ""


def build_dialog_preview(
    *,
    chat_id: str,
    title: str,
    last_preview: str,
    chat_url: str | None = None,
    is_own_last: bool | None = None,
    author: str | None = None,
    unread_count: int | None = None,
) -> DialogPreview | None:
    cid = _collapse_ws(chat_id)
    if not cid:
        return None
    author_s = _collapse_ws(author or "")
    preview = _collapse_ws(last_preview or "")
    if author_s and not preview.startswith(author_s):
        # Keep author in fingerprint body when JS did not already compose it.
        if preview and not is_own_last_preview(preview):
            composed = _collapse_ws(f"{author_s} {preview}")
        else:
            composed = preview or author_s
    else:
        composed = preview or author_s
    composed = _collapse_ws(composed)
    title_s = _collapse_ws(title or "") or cid
    url = (chat_url or "").strip() or chat_url_for(cid)
    own = is_own_last_message(author=author_s, preview=composed)
    if is_own_last is True:
        own = True
    unread: int | None = None
    if unread_count is not None:
        try:
            unread = max(0, int(unread_count))
        except (TypeError, ValueError):
            unread = None
    return DialogPreview(
        chat_id=cid,
        title=title_s,
        last_preview=composed,
        chat_url=url,
        is_own_last=own,
        fingerprint_mid=make_fingerprint_mid(cid, composed),
        author=author_s,
        unread_count=unread,
    )


def parse_dialogs_payload(payload: Any) -> list[DialogPreview]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("dialogs")
    if not isinstance(rows, list):
        return []
    out: list[DialogPreview] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chat_id = str(row.get("chat_id") or "").strip()
        if not chat_id:
            chat_id = chat_id_from_name_element_id(str(row.get("name_id") or ""))
        unread_raw = row.get("unread_count", None)
        unread_count: int | None
        if unread_raw is None and "unread_count" not in row:
            unread_count = None
        else:
            try:
                unread_count = int(unread_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                unread_count = None
        parsed = build_dialog_preview(
            chat_id=chat_id,
            title=str(row.get("title") or ""),
            last_preview=str(row.get("last_preview") or row.get("preview") or ""),
            chat_url=str(row.get("chat_url") or "") or CHATS_URL,
            is_own_last=bool(row["is_own_last"]) if "is_own_last" in row else None,
            author=str(row.get("author") or ""),
            unread_count=unread_count,
        )
        if parsed is not None:
            out.append(parsed)
    return out


def format_inbox_notify(title: str, chat_url: str) -> str:
    safe_title = html.escape(title or "?", quote=False)
    safe_url = html.escape(chat_url or CHATS_URL, quote=True)
    return (
        "💬 Яндекс Мессенджер · входящее\n"
        f"👤 {safe_title}\n"
        f"🔗 {safe_url}"
    )


def should_notify_dialog(dialog: DialogPreview, *, stored_mid: int) -> bool:
    if dialog.is_own_last:
        return False
    if not is_dialog_parse_reliable(dialog):
        return False
    if stored_mid <= 0:
        return False
    if dialog.fingerprint_mid == stored_mid:
        return False
    if dialog.unread_count is not None and dialog.unread_count == 0:
        return False
    return True


SELECT_TAB_JS_TEMPLATE = """
() => {{
  const want = {tab_json};
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  for (const el of tabs) {{
    const t = String(el.innerText || '').trim();
    if (t !== want) continue;
    if (el.getAttribute('aria-selected') === 'true') return {{ ok: true, already: true, tab: want }};
    el.click();
    return {{ ok: true, already: false, tab: want }};
  }}
  return {{ ok: false, tab: want }};
}}
"""


class YandexInboxClient:
    def __init__(self, browser: BrowserClient) -> None:
        self.browser = browser
        self._chats_ready = False
        self._active_tab: str | None = None

    def ensure_chats_page(self) -> None:
        if self._chats_ready:
            return
        self.browser.navigate(CHATS_URL)
        # Telemost SPA: chat list mounts asynchronously
        self.browser.wait_ms(5000)
        self._chats_ready = True
        self._active_tab = None

    def select_chats_tab(self, tab_name: str) -> bool:
        """Switch sidebar folder tab (Все / Личные / Новые / …)."""
        self.ensure_chats_page()
        want = str(tab_name or "").strip()
        if not want:
            return False
        if self._active_tab == want:
            return True
        js = SELECT_TAB_JS_TEMPLATE.format(
            tab_json=json.dumps(want, ensure_ascii=False)
        )
        raw = self.browser.evaluate(js)
        ok = isinstance(raw, dict) and bool(raw.get("ok"))
        if ok:
            self.browser.wait_ms(1500)
            self._active_tab = want
        else:
            logger.warning("yandex_inbox_tab_missing tab=%s", want)
        return ok

    def _ensure_logged_in(self) -> None:
        try:
            probe = self.browser.evaluate(AUTH_PROBE_JS)
        except Exception:
            probe = None
        href = ""
        if isinstance(probe, dict):
            href = str(probe.get("href") or "")
            if probe.get("not_logged_in"):
                raise YandexAuthError(
                    "not_logged_in: нет сессии Яндекс Мессенджер — "
                    "обнови data/yandex_storage.json (deploy/yandex_login_interactive.py)"
                )
        if not href:
            try:
                href = str(self.browser.evaluate("() => location.href") or "")
            except Exception:
                href = ""
        if _is_login_url(href):
            raise YandexAuthError(
                "not_logged_in: редирект на passport — обнови yandex_storage.json"
            )

    def list_dialogs(self) -> list[DialogPreview]:
        self.ensure_chats_page()
        self._ensure_logged_in()
        raw = self.browser.evaluate(LIST_DIALOGS_JS)
        if (
            isinstance(raw, dict)
            and raw.get("ok")
            and not (raw.get("dialogs") or [])
        ):
            # SPA still mounting — one short retry
            self.browser.wait_ms(3000)
            raw = self.browser.evaluate(LIST_DIALOGS_JS)
        if not isinstance(raw, dict) or not raw.get("ok"):
            logger.warning("yandex_inbox_list_failed payload=%s", raw)
            return []
        if _is_login_url(str(raw.get("href") or "")):
            raise YandexAuthError(
                "not_logged_in: редирект на passport — обнови yandex_storage.json"
            )
        dialogs = parse_dialogs_payload(raw)
        logger.info(
            "yandex_inbox_list_dialogs count=%s raw_count=%s",
            len(dialogs),
            raw.get("count"),
        )
        return dialogs
