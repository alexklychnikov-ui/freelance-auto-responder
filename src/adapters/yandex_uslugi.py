"""Yandex Uslugi (Яндекс Исполнители) adapter — scan + read only (manual reply MVP).

Critical: never reuse Kwork browser storage. This adapter opens its own Playwright
context with ``settings.yandex_storage_state`` and ignores any shared browser passed
from the orchestrator (which is typically bound to Kwork storage).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from src.adapters.yandex_urls import YANDEX_USLUGI_ORIGIN, yandex_order_url
from src.analyzer.response_text import sanitize_buyer_field
from src.browser.base import BrowserClient
from src.browser.factory import close_browser_client, get_browser_client
from src.config import Settings
from src.models import ProjectFull, ProjectPreview, ReplyEvent, SubmitResult

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)

LISTING_EXTRACTOR_JS = """
() => {
  const UUID =
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  const seen = new Set();
  const cards = [];
  const links = [...document.querySelectorAll('a[href*="/order/"]')];
  for (const link of links) {
    const href = link.getAttribute('href') || '';
    const m = href.match(UUID);
    if (!m) continue;
    const id = m[0].toLowerCase();
    if (seen.has(id)) continue;
    seen.add(id);
    const card =
      link.closest(
        '[data-id], [class*="order"], [class*="Order"], article, li, section'
      ) || link.parentElement;
    const titleEl =
      card?.querySelector('h1, h2, h3, [class*="title"], [class*="Title"]') ||
      link;
    let title = (titleEl?.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!title || title.length < 3) {
      title = (link.textContent || '').replace(/\\s+/g, ' ').trim();
    }
    const budgetEl = card?.querySelector(
      '[class*="price"], [class*="Price"], [class*="budget"], [class*="Budget"]'
    );
    const budget_text = budgetEl
      ? (budgetEl.textContent || '').replace(/\\s+/g, ' ').trim()
      : null;
    const url = href.startsWith('http')
      ? href.split('?')[0]
      : 'https://uslugi.yandex.ru' + href.split('?')[0];
    cards.push({
      project_id: id,
      url,
      title: title.slice(0, 300),
      budget_text: budget_text || null,
      responses_count: null,
      published_at: null,
    });
  }
  return cards;
}
"""

ORDER_EXTRACTOR_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const UUID =
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  const pathId = (location.pathname.match(UUID) || [])[0] || null;
  const title =
    norm(document.querySelector('h1')?.textContent) ||
    norm(document.querySelector('[class*="title"], [class*="Title"]')?.textContent) ||
    norm(document.title).replace(/\\s*[|·—-].*$/, '');

  const descCandidates = [
    ...document.querySelectorAll(
      '[class*="description"], [class*="Description"], [class*="text"], article, main p'
    ),
  ];
  let full_description = '';
  for (const el of descCandidates) {
    const t = norm(el.textContent);
    if (t.length > full_description.length) full_description = t;
  }
  if (full_description.length < 40) {
    const main = document.querySelector('main, [role="main"], article') || document.body;
    full_description = norm(main?.innerText || '').slice(0, 8000);
  }

  const moneyBits = [];
  const moneyRe = /(\\d[\\d\\s\\u00a0]*)\\s*₽|₽\\s*(\\d[\\d\\s\\u00a0]*)|до\\s*(\\d[\\d\\s\\u00a0]*)/gi;
  const bodyText = document.body?.innerText || '';
  let mm;
  while ((mm = moneyRe.exec(bodyText)) && moneyBits.length < 6) {
    moneyBits.push(norm(mm[0]));
  }
  const desired_budget = moneyBits[0] || null;
  const max_budget = moneyBits.length > 1 ? moneyBits[1] : desired_budget;

  let buyer = null;
  // Do NOT use [class*="user"] — matches logged-in account menu (own name).
  const buyerEl = document.querySelector(
    '[class*="clientName"], [class*="ClientName"], [class*="customerName"], [class*="CustomerName"], [data-qa*="customer"], [data-qa*="client"]'
  );
  if (buyerEl) buyer = norm(buyerEl.textContent).slice(0, 80);

  return {
    project_id: pathId,
    title,
    full_description: full_description.slice(0, 8000),
    desired_budget,
    max_budget,
    buyer,
    offers_count: null,
    time_left: null,
  };
}
"""


class YandexAuthError(RuntimeError):
    """Raised when Yandex session is missing or redirected to passport/login."""


def _is_login_url(url: str) -> bool:
    low = (url or "").lower()
    if "passport.yandex" in low:
        return True
    if "login" in low and "yandex" in low:
        return True
    parsed = urlparse(low)
    if "uslugi.yandex" in (parsed.netloc or "") and "/auth" in (parsed.path or ""):
        return True
    return False


def _is_executor_cab_missing(url: str) -> bool:
    """Logged-in Yandex account without executor profile lands on /registration."""
    low = (url or "").lower()
    if "uslugi.yandex" not in low:
        return False
    if "/registration" in low:
        return True
    if "/cab/" not in low and "/order/" not in low:
        return True
    return False


def parse_listing_from_html(html: str) -> list[dict[str, Any]]:
    """Regex fallback when JS evaluate is unavailable (fixtures / FakeBrowser)."""
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    for m in re.finditer(
        r'href=["\']([^"\']*/order/'
        r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[^"\']*)["\']',
        html,
        flags=re.I,
    ):
        href, oid = m.group(1), m.group(2).lower()
        if oid in seen:
            continue
        seen.add(oid)
        window = html[max(0, m.start() - 200) : m.end() + 400]
        title = ""
        tm = re.search(r">([^<]{5,200})</a>", window, flags=re.I)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()
        if not title:
            tm = re.search(r"<h[1-3][^>]*>([^<]+)", window, flags=re.I)
            if tm:
                title = re.sub(r"\s+", " ", tm.group(1)).strip()
        budget = None
        bm = re.search(
            r"(\d[\d\s\u00a0]*\s*₽|до\s*\d[\d\s\u00a0]*)",
            window,
            flags=re.I,
        )
        if bm:
            budget = re.sub(r"\s+", " ", bm.group(1)).strip()
        url = href if href.startswith("http") else f"{YANDEX_USLUGI_ORIGIN}{href}"
        url = url.split("?")[0]
        cards.append(
            {
                "project_id": oid,
                "url": url,
                "title": title or f"Заказ {oid[:8]}",
                "budget_text": budget,
                "responses_count": None,
                "published_at": None,
            }
        )
    return cards


def parse_order_from_html(html: str, order_id: str | None = None) -> dict[str, Any]:
    pid = (order_id or "").lower() or None
    if not pid:
        m = _UUID_RE.search(html)
        if m:
            pid = m.group(0).lower()

    title = ""
    for pat in (
        r"<h1[^>]*>(.*?)</h1>",
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r"<title[^>]*>([^<]+)</title>",
    ):
        m = re.search(pat, html, flags=re.I | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", " ", m.group(1))
            title = re.sub(r"\s+", " ", title).strip()
            title = re.sub(r"\s*[|·—-].*$", "", title).strip()
            if title:
                break

    desc = ""
    for pat in (
        r'class="[^"]*description[^"]*"[^>]*>(.*?)</(?:div|p|section)>',
        r"<article[^>]*>(.*?)</article>",
        r"<main[^>]*>(.*?)</main>",
    ):
        m = re.search(pat, html, flags=re.I | re.DOTALL)
        if m:
            raw = re.sub(r"<script[^>]*>.*?</script>", " ", m.group(1), flags=re.I | re.DOTALL)
            raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.I | re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > len(desc):
                desc = text

    money = re.findall(
        r"(?:до\s*)?\d[\d\s\u00a0]*\s*₽",
        html,
        flags=re.I,
    )
    money = [re.sub(r"\s+", " ", x).strip() for x in money[:4]]
    desired = money[0] if money else None
    max_b = money[1] if len(money) > 1 else desired

    buyer = None
    bm = re.search(
        r'(?:заказчик|клиент)[^<]{0,40}</[^>]+>\s*<[^>]+>([^<]{2,80})',
        html,
        flags=re.I,
    )
    if bm:
        buyer = re.sub(r"\s+", " ", bm.group(1)).strip()

    return {
        "project_id": pid,
        "title": title,
        "full_description": desc[:8000],
        "desired_budget": desired,
        "max_budget": max_b,
        "buyer": buyer,
        "offers_count": None,
        "time_left": None,
    }


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class YandexSubmittedOffer:
    description: str = ""
    price: str | None = None
    ok: bool = False
    error: str | None = None


SUBMITTED_OFFER_EXTRACTOR_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const isTimeAgo = (t) => /^\\d+\\s*(?:минут|минуту|час|часа|часов|день|дня|дней|недел)\\w*\\s+назад$/i.test(norm(t));
  const isEdit = (t) => /^редактировать$/i.test(norm(t));
  const isStatus = (t) => /^(?:не\\s+)?просмотрен$/i.test(norm(t));
  const isPriceLine = (t) => /^предложенная\\s+цена\\s*:/i.test(norm(t));
  const isHeading = (t) => /^ваш\\s+отклик$/i.test(norm(t));

  const pickPrice = (t) => {
    const m = norm(t).match(/предложенная\\s+цена\\s*:\\s*(.+)$/i);
    if (!m) return null;
    const digits = (m[1].match(/\\d[\\d\\s\\u00a0]*/) || [''])[0].replace(/[^\\d]/g, '');
    return digits || null;
  };

  // Prefer DOM: heading «Ваш отклик» → siblings until time-ago / Редактировать.
  const nodes = [...document.querySelectorAll('h1,h2,h3,h4,div,span,p,button,a,li')];
  const heading = nodes.find((el) => isHeading(el.textContent || ''));
  if (heading) {
    const root = heading.closest('section,article,[class*="offer"],[class*="response"],[class*="Response"]')
      || heading.parentElement;
    let price = null;
    const parts = [];
    const walk = root ? [...root.querySelectorAll('*')] : [];
    let passedHeading = false;
    for (const el of walk) {
      const t = (el.textContent || '').trim();
      if (!t) continue;
      if (isHeading(t)) { passedHeading = true; continue; }
      if (!passedHeading) continue;
      if (isTimeAgo(t) || isEdit(t)) break;
      if (isStatus(t)) continue;
      if (isPriceLine(t)) {
        price = pickPrice(t) || price;
        continue;
      }
      // Skip short chrome lines (price-only leftovers).
      if (/^\\d[\\d\\s\\u00a0]*\\s*₽/.test(t) && t.length < 40) {
        if (!price) {
          const digits = t.replace(/[^\\d]/g, '');
          if (digits) price = digits;
        }
        continue;
      }
      // Prefer leaf-ish text blocks (avoid duplicating parent containers).
      if (el.children && el.children.length > 2 && t.length > 200) continue;
      if (parts.length && parts[parts.length - 1].includes(t)) continue;
      if (parts.some((p) => t.includes(p) && t.length > p.length + 20)) {
        // parent swallowed previous — replace
        while (parts.length && t.includes(parts[parts.length - 1])) parts.pop();
      }
      parts.push(t);
    }
    const description = parts.join('\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
    if (description.length >= 20) {
      return { ok: true, error: null, description, price, via: 'dom' };
    }
  }

  // Fallback: page innerText + cut at first structural chrome line.
  const full = document.body?.innerText || '';
  const start = full.search(/ваш\\s+отклик/i);
  if (start < 0) {
    return { ok: false, error: 'block_missing', description: '', price: null };
  }
  let chunk = full.slice(start);
  const cutRe = /\\n\\s*(?:\\d+\\s*(?:минут|минуту|час|часа|часов|день|дня|дней|недел)\\w*\\s+назад|редактировать|другие\\s+заказы|предложения\\s+исполнителей)/i;
  const cut = chunk.search(cutRe);
  if (cut > 40) chunk = chunk.slice(0, cut);

  let price = null;
  const pm = chunk.match(/предложенная\\s+цена\\s*:\\s*([^\\n]+)/i);
  if (pm) {
    const digits = (pm[1].match(/\\d[\\d\\s\\u00a0]*/) || [''])[0].replace(/[^\\d]/g, '');
    if (digits) price = digits;
  }

  let body = chunk;
  body = body.replace(/^ваш\\s+отклик\\s*/i, '');
  body = body.replace(/^(?:не\\s+)?просмотрен\\s*/i, '');
  body = body.replace(/^предложенная\\s+цена\\s*:[^\\n]*\\n?/i, '');
  body = body.replace(/^\\s*\\d[\\d\\s\\u00a0]*\\s*₽[^\\n]*\\n?/i, '');
  body = body.replace(/\\n{3,}/g, '\\n\\n').trim();
  if (body.length < 20) {
    return { ok: false, error: 'empty_body', description: '', price };
  }
  return { ok: true, error: null, description: body, price, via: 'text' };
}
"""


# Structural page chrome after «Ваш отклик» — same on any order, not promo copy.
_SUBMITTED_OFFER_TAIL_RE = re.compile(
    r"\n\s*(?:"
    r"\d+\s*(?:минут|минуту|час|часа|часов|день|дня|дней|недел)\w*\s+назад|"
    r"редактировать|"
    r"другие\s+заказы|"
    r"предложения\s+исполнителей"
    r")",
    re.I,
)


def parse_submitted_offer_from_text(text: str) -> YandexSubmittedOffer:
    """Parse «Ваш отклик» from page text; stop at first structural UI boundary."""
    full = text or ""
    start = re.search(r"ваш\s+отклик", full, flags=re.I)
    if not start:
        return YandexSubmittedOffer(ok=False, error="block_missing")
    chunk = full[start.start() :]
    cut = _SUBMITTED_OFFER_TAIL_RE.search(chunk)
    if cut and cut.start() > 40:
        chunk = chunk[: cut.start()]

    price: str | None = None
    pm = re.search(r"предложенная\s+цена\s*:\s*([^\n]+)", chunk, flags=re.I)
    if pm:
        digits = re.sub(r"[^\d]", "", pm.group(1))
        if digits:
            price = digits

    body = chunk
    body = re.sub(r"^ваш\s+отклик\s*", "", body, count=1, flags=re.I)
    body = re.sub(r"^(?:не\s+)?просмотрен\s*", "", body, count=1, flags=re.I)
    body = re.sub(r"^предложенная\s+цена\s*:[^\n]*\n?", "", body, count=1, flags=re.I)
    body = re.sub(
        r"^\s*\d[\d\s\u00a0]*\s*₽[^\n]*\n?",
        "",
        body,
        count=1,
        flags=re.I,
    )
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 20:
        return YandexSubmittedOffer(ok=False, error="empty_body", price=price)
    return YandexSubmittedOffer(description=body, price=price, ok=True)


def read_submitted_offer_text(
    browser: BrowserClient, order_id: str
) -> YandexSubmittedOffer:
    """Open /order/{uuid} and read the «Ваш отклик» block (post-submit)."""
    oid = str(order_id or "").strip().lower()
    if not _UUID_RE.fullmatch(oid):
        return YandexSubmittedOffer(ok=False, error="bad_order_id")
    url = yandex_order_url(oid)
    try:
        browser.navigate(url)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(2500)
    except Exception as exc:
        return YandexSubmittedOffer(ok=False, error=f"navigate_failed: {exc}")

    try:
        raw = browser.evaluate(SUBMITTED_OFFER_EXTRACTOR_JS)
    except Exception as exc:
        logger.warning("yandex_submitted_js_failed order_id=%s err=%s", oid, exc)
        raw = None

    if isinstance(raw, dict) and raw.get("ok") and str(raw.get("description") or "").strip():
        return YandexSubmittedOffer(
            description=str(raw["description"]).strip(),
            price=(str(raw["price"]) if raw.get("price") else None),
            ok=True,
        )

    snap = browser.snapshot() or ""
    parsed = parse_submitted_offer_from_text(snap)
    if parsed.ok:
        return parsed
    err = None
    if isinstance(raw, dict):
        err = raw.get("error")
    return YandexSubmittedOffer(
        ok=False,
        error=str(err or parsed.error or "read_failed"),
        price=parsed.price,
    )


class YandexUslugiAdapter:
    """Scan cab/orders + read /order/{uuid}. No autofill/submit."""

    platform_id = "yandex_uslugi"

    def __init__(
        self,
        *,
        source_key: str,
        listing_url: str,
        settings: Settings,
        browser: Any | None = None,
    ) -> None:
        # Shared Kwork browser (if any) is intentionally ignored — see module docstring.
        _ = browser
        self.source_key = source_key
        self.listing_url = listing_url
        self.settings = settings
        self._browser: Any | None = None
        self._owns_browser = True

    def _storage_path(self) -> str | None:
        path = (self.settings.yandex_storage_state or "").strip()
        return path or None

    def _get_browser(self) -> Any:
        if self._browser is None:
            storage = self._storage_path()
            self._browser = get_browser_client(
                self.settings, storage_state_path=storage
            )
            self._owns_browser = True
        return self._browser

    def close(self) -> None:
        if self._browser is not None and self._owns_browser:
            close_browser_client(self._browser)
        self._browser = None

    def _current_url(self, browser: Any) -> str:
        try:
            return str(browser.evaluate("() => location.href") or "")
        except Exception:
            return ""

    def _ensure_logged_in(self, browser: Any) -> None:
        url = self._current_url(browser)
        if _is_login_url(url):
            raise YandexAuthError(
                "not_logged_in: нет сессии Яндекс Услуги — "
                "запусти deploy/yandex_login_interactive.py"
            )
        if _is_executor_cab_missing(url):
            raise YandexAuthError(
                "not_logged_in: нет кабинета исполнителя (редирект на "
                f"{url or '/registration'}) — пройди регистрацию на uslugi.yandex.ru "
                "и перезапусти deploy/yandex_login_interactive.py"
            )
        # Soft probe: cab/orders for guests often redirects to passport
        snap = ""
        try:
            snap = (browser.snapshot() or "")[:2000].lower()
        except Exception:
            pass
        if "passport.yandex" in snap or "войдите" in snap and "passport" in url.lower():
            raise YandexAuthError(
                "not_logged_in: редирект на passport — обнови yandex_storage.json"
            )

    def scan_new(self) -> list[ProjectPreview]:
        browser = self._get_browser()
        try:
            browser.navigate(self.listing_url)
            self._ensure_logged_in(browser)
            raw_cards = browser.evaluate(LISTING_EXTRACTOR_JS)
            if not isinstance(raw_cards, list) or not raw_cards:
                snapshot = browser.snapshot()
                raw_cards = parse_listing_from_html(snapshot)
                self._ensure_logged_in(browser)
        except YandexAuthError:
            raise
        except Exception as exc:
            logger.exception("yandex_scan_failed source=%s", self.source_key)
            raise YandexAuthError(f"yandex_scan_failed: {exc}") from exc

        previews: list[ProjectPreview] = []
        for item in raw_cards or []:
            pid = str(item.get("project_id") or "").lower()
            if not _UUID_RE.fullmatch(pid):
                continue
            previews.append(
                ProjectPreview(
                    platform=self.platform_id,
                    source_key=self.source_key,
                    project_id=pid,
                    url=str(item.get("url") or yandex_order_url(pid)),
                    title=str(item.get("title") or ""),
                    budget_text=item.get("budget_text"),
                    published_at=_parse_published(item.get("published_at")),
                    responses_count=item.get("responses_count"),
                )
            )
        logger.info(
            "yandex_scan source=%s cards=%d", self.source_key, len(previews)
        )
        return previews

    def read_full(self, project_id: str) -> ProjectFull:
        pid = str(project_id).lower().strip()
        url = yandex_order_url(pid)
        browser = self._get_browser()
        try:
            browser.navigate(url)
            self._ensure_logged_in(browser)
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(1500)
            raw = browser.evaluate(ORDER_EXTRACTOR_JS)
            if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
                snapshot = browser.snapshot()
                raw = parse_order_from_html(snapshot, order_id=pid)
            self._ensure_logged_in(browser)
        except YandexAuthError:
            raise
        except Exception as exc:
            logger.exception("yandex_read_failed project_id=%s", pid)
            raise RuntimeError(f"yandex_read_failed: {exc}") from exc

        title = str(raw.get("title") or "").strip()
        desc = str(raw.get("full_description") or "").strip()
        return ProjectFull(
            platform=self.platform_id,
            source_key=self.source_key,
            project_id=pid,
            url=url,
            title=title or f"Заказ {pid[:8]}",
            full_description=desc,
            desired_budget=raw.get("desired_budget"),
            max_budget=raw.get("max_budget"),
            offers_count=raw.get("offers_count"),
            buyer=sanitize_buyer_field(raw.get("buyer")),
            time_left=raw.get("time_left"),
        )

    def submit_response(
        self, project_id: str, text: str, price: str | None
    ) -> SubmitResult:
        return SubmitResult(
            success=False,
            project_id=project_id,
            message="manual_only: autofill/submit для Яндекс Услуги не реализован",
        )

    def prepare_response(self, *args: Any, **kwargs: Any) -> SubmitResult:
        project_id = str(kwargs.get("project_id") or (args[0] if args else ""))
        return SubmitResult(
            success=False,
            project_id=project_id,
            message="manual_only: prepare на сайте не поддерживается",
        )

    def monitor_replies(self) -> list[ReplyEvent]:
        return []
