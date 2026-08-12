"""FL.ru adapter — scan + read only (manual reply MVP, like Yandex Uslugi)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.adapters.flru_urls import FLRU_ORIGIN, ensure_flru_for_all, flru_project_url
from src.browser.base import BrowserClient
from src.browser.factory import close_browser_client, get_browser_client
from src.config import Settings
from src.models import ProjectFull, ProjectPreview, ReplyEvent, SubmitResult

logger = logging.getLogger(__name__)

_PROJECT_ID_RE = re.compile(r"^\d{5,9}$")

# FL.ru: баннер «Заказчик выбрал исполнителя» ≠ старое «Исполнитель определён»
_CLOSED_RE = re.compile(
    r"(?:исполнитель\s+определ|выбрал\s+исполнител|исполнитель\s+выбран)",
    re.I,
)


def is_flru_project_closed(text: str) -> bool:
    return bool(_CLOSED_RE.search(text or ""))


class FlruProjectClosed(RuntimeError):
    """Project page shows performer already chosen / order closed."""


def flru_project_closed_scope(html_or_text: str) -> str:
    """Main-project text for closed checks — exclude related feed / aside.

    Whole ``document.body.innerText`` false-positives when related cards say
    «Заказчик выбрал исполнителя». Scope to head before «Другие заказы» and
    drop ``<aside>`` blocks in HTML.
    """
    raw = html_or_text or ""
    if "<" in raw and ">" in raw:
        cleaned = re.sub(
            r"<aside\b[^>]*>.*?</aside>", " ", raw, flags=re.I | re.DOTALL
        )
        cleaned = re.sub(
            r"<script\b[^>]*>.*?</script>", " ", cleaned, flags=re.I | re.DOTALL
        )
        text = re.sub(r"<[^>]+>", " ", cleaned)
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = re.sub(r"\s+", " ", raw).strip()
    cut = re.search(
        r"другие\s+заказ|похожие\s+(?:заказ|проект)|рекоменд",
        text,
        flags=re.I,
    )
    if cut:
        return text[: cut.start()].strip()
    return text


def is_flru_project_page_closed(html_or_text: str) -> bool:
    return is_flru_project_closed(flru_project_closed_scope(html_or_text))


def extract_project_offers_count(text: str) -> int | None:
    """Project-page offers: prefer «Откликнулись: N».

    «N отклик*» / «N ответ*» only inside a «Статистика» window —
    related feed cards must not steal the first whole-body match.
    """
    if not text:
        return None
    m = re.search(r"Откликнулись:\s*(\d+)", text, flags=re.I)
    if m:
        return int(m.group(1))
    stats = re.search(r"Статистика.{0,400}", text, flags=re.I | re.DOTALL)
    if not stats:
        return None
    region = stats.group(0)
    m = re.search(r"(\d+)\s+отклик\w*", region, flags=re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s+ответ\w*", region, flags=re.I)
    if m:
        return int(m.group(1))
    return None


LISTING_EXTRACTOR_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const isClosed = (t) => /(?:исполнитель\\s+определ|выбрал\\s+исполнител|исполнитель\\s+выбран)/i.test(t || '');
  const seen = new Set();
  const cards = [];
  const links = [...document.querySelectorAll('a[href*="/projects/"]')];
  for (const link of links) {
    const href = link.getAttribute('href') || '';
    const m = href.match(/\\/projects\\/(\\d{5,9})/);
    if (!m) continue;
    const id = m[1];
    if (seen.has(id)) continue;
    const card =
      link.closest('article, section, li, [class*="post"], [class*="project"]') ||
      link.parentElement?.parentElement ||
      link.parentElement;
    const cardText = norm(card?.innerText || '');
    const low = cardText.toLowerCase();
    if (isClosed(cardText)) continue;
    if (low.includes('вакансия')) continue;
    seen.add(id);
    const titleEl =
      card?.querySelector('h2, h3, [class*="title"]') || link;
    let title = norm(titleEl?.textContent || link.textContent);
    if (!title || title.length < 3) title = `Проект ${id}`;
    const budgetMatch = cardText.match(
      /(?:^|\\s)(по договоренности|\\d[\\d\\s]*\\s*руб)/i
    );
    const budget_text = budgetMatch ? norm(budgetMatch[1]) : null;
    const respMatch =
      cardText.match(/(\\d+)\\s+ответ/i) ||
      cardText.match(/(\\d+)\\s+отклик/i);
    const responses_count = respMatch ? parseInt(respMatch[1], 10) : null;
    const url = href.startsWith('http')
      ? href.split('?')[0]
      : 'https://www.fl.ru' + href.split('?')[0];
    cards.push({
      project_id: id,
      url,
      title: title.slice(0, 300),
      budget_text,
      responses_count,
      published_at: null,
    });
  }
  return cards;
}
"""

PROJECT_EXTRACTOR_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const pathId = (location.pathname.match(/\\/projects\\/(\\d{5,9})/) || [])[1] || null;
  const title =
    norm(document.querySelector('h1')?.textContent) ||
    norm(document.querySelector('[class*="title"]')?.textContent) ||
    norm(document.title).replace(/\\s*[|·—-].*$/i, '');

  const closedRe = /(?:исполнитель\\s+определ|выбрал\\s+исполнител|исполнитель\\s+выбран)/i;
  const scopeRoot = document.body ? document.body.cloneNode(true) : null;
  if (scopeRoot) {
    scopeRoot.querySelectorAll(
      'aside, [class*="related"], [class*="similar"], [class*="recommend"]'
    ).forEach((el) => el.remove());
  }
  let closedScope = (scopeRoot?.innerText || document.body?.innerText || '');
  const relatedCut = closedScope.search(/другие\\s+заказ|похожие\\s+(?:заказ|проект)|рекоменд/i);
  if (relatedCut > 0) closedScope = closedScope.slice(0, relatedCut);
  const closed = closedRe.test(closedScope);
  const descCandidates = [
    ...document.querySelectorAll(
      '[class*="description"], [class*="text-qa"], article p, .b-layout__txt p, main p'
    ),
  ];
  let full_description = '';
  for (const el of descCandidates) {
    const t = norm(el.textContent);
    if (t.length > full_description.length) full_description = t;
  }
  if (full_description.length < 40) {
    const main = document.querySelector('main, article, [class*="layout"]') || document.body;
    full_description = norm(main?.innerText || '').slice(0, 12000);
  }

  const bodyText = document.body?.innerText || '';
  const moneyRe = /(\\d[\\d\\s]*)\\s*руб|по договоренности/gi;
  const moneyBits = [];
  let mm;
  while ((mm = moneyRe.exec(bodyText)) && moneyBits.length < 6) {
    moneyBits.push(norm(mm[0]));
  }
  const desired_budget = moneyBits[0] || null;
  const max_budget = moneyBits.length > 1 ? moneyBits[1] : desired_budget;

  let offers_count = null;
  const otkliknulis = bodyText.match(/Откликнулись:\\s*(\\d+)/i);
  if (otkliknulis) {
    offers_count = parseInt(otkliknulis[1], 10);
  } else {
    const statsWin = bodyText.match(/Статистика[\\s\\S]{0,400}/i);
    if (statsWin) {
      const region = statsWin[0];
      const otklik = region.match(/(\\d+)\\s+отклик\\w*/i);
      if (otklik) {
        offers_count = parseInt(otklik[1], 10);
      } else {
        const otvet = region.match(/(\\d+)\\s+ответ\\w*/i);
        if (otvet) offers_count = parseInt(otvet[1], 10);
      }
    }
  }

  let buyer = null;
  const buyerCandidates = [
    ...document.querySelectorAll(
      'a[href*="/users/"], a[href*="/freelancer/"], [class*="customer-name"], [class*="CustomerName"], [data-qa*="customer"]'
    ),
  ];
  for (const el of buyerCandidates) {
    const t = norm(el.textContent);
    if (t && t.length >= 2 && t.length <= 40 && !/войти|чат|отклик/i.test(t)) {
      buyer = t.slice(0, 80);
      break;
    }
  }

  return {
    project_id: pathId,
    title,
    full_description: full_description.slice(0, 12000),
    desired_budget,
    max_budget,
    buyer,
    offers_count,
    time_left: null,
    closed,
  };
}
"""


class FlruAuthError(RuntimeError):
    """Raised when FL.ru session is missing or login is required."""


def _is_login_url(url: str) -> bool:
    low = (url or "").lower()
    if "fl.ru/login" in low or "fl.ru/account/login" in low:
        return True
    if "/login" in low and "fl.ru" in low:
        return True
    return False


ENSURE_FOR_ALL_FILTER_JS = """
() => {
  const el = document.querySelector('#ui-checkbox-check-for-all');
  if (!el) {
    return { ok: false, reason: 'checkbox_missing' };
  }
  if (el.checked) {
    return { ok: true, already: true };
  }
  el.click();
  return { ok: true, clicked: true };
}
"""


def parse_listing_from_html(html: str, *, skip_closed: bool = True) -> list[dict[str, Any]]:
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    for m in re.finditer(
        r'href=["\']([^"\']*/projects/(\d{5,9})[^"\']*)["\']',
        html,
        flags=re.I,
    ):
        href, pid = m.group(1), m.group(2)
        if pid in seen:
            continue
        window = html[max(0, m.start() - 300) : m.end() + 800]
        low = window.lower()
        if skip_closed and is_flru_project_closed(window):
            continue
        if "вакансия" in low:
            continue
        seen.add(pid)
        title = ""
        tm = re.search(r">([^<]{5,200})</a>", window, flags=re.I)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()
        budget = None
        bm = re.search(
            r"(по договоренности|\d[\d\s]*\s*руб)",
            window,
            flags=re.I,
        )
        if bm:
            budget = re.sub(r"\s+", " ", bm.group(1)).strip()
        responses = None
        rm = re.search(r"(\d+)\s+(?:ответ|отклик)", window, flags=re.I)
        if rm:
            responses = int(rm.group(1))
        url = href if href.startswith("http") else f"{FLRU_ORIGIN}{href}"
        url = url.split("?")[0]
        cards.append(
            {
                "project_id": pid,
                "url": url,
                "title": title or f"Проект {pid}",
                "budget_text": budget,
                "responses_count": responses,
                "published_at": None,
            }
        )
    return cards


def parse_project_from_html(html: str, project_id: str | None = None) -> dict[str, Any]:
    pid = (project_id or "").strip() or None
    if not pid:
        m = re.search(r"/projects/(\d{5,9})", html, flags=re.I)
        if m:
            pid = m.group(1)

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
        r'class="[^"]*description[^"]*"[^>]*>(.*?)</(?:div|section)>',
        r'class="[^"]*text-qa[^"]*"[^>]*>(.*?)</(?:div|section)>',
        r"<article[^>]*>(.*?)</article>",
    ):
        m = re.search(pat, html, flags=re.I | re.DOTALL)
        if m:
            raw = re.sub(r"<script[^>]*>.*?</script>", " ", m.group(1), flags=re.I | re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > len(desc):
                desc = text

    money = re.findall(
        r"(?:по договоренности|\d[\d\s]*\s*руб)",
        html,
        flags=re.I,
    )
    money = [re.sub(r"\s+", " ", x).strip() for x in money[:4]]
    desired = money[0] if money else None
    max_b = money[1] if len(money) > 1 else desired

    offers = extract_project_offers_count(html)

    closed = is_flru_project_page_closed(html)

    return {
        "project_id": pid,
        "title": title,
        "full_description": desc[:12000],
        "desired_budget": desired,
        "max_budget": max_b,
        "buyer": None,
        "offers_count": offers,
        "time_left": None,
        "closed": closed,
    }


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class FlruAdapter:
    """Scan projects feed + read /projects/{id}. No autofill/submit."""

    platform_id = "flru"

    def __init__(
        self,
        *,
        source_key: str,
        listing_url: str,
        settings: Settings,
        filters: dict[str, Any] | None = None,
        browser: Any | None = None,
    ) -> None:
        self.source_key = source_key
        self.listing_url = listing_url
        self.settings = settings
        self.filters = filters or {}
        self._browser = browser
        self._owns_browser = browser is None

    def _storage_path(self) -> str | None:
        path = (self.settings.flru_storage_state or "").strip()
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
            raise FlruAuthError(
                "not_logged_in: нет сессии FL.ru — "
                "запусти deploy/flru_login_interactive.py"
            )

    def _listing_url(self) -> str:
        url = self.listing_url
        if bool(self.filters.get("for_all", True)):
            url = ensure_flru_for_all(url)
        return url

    def _ensure_for_all_filter(self, browser: Any) -> None:
        if not bool(self.filters.get("for_all", True)):
            return
        try:
            result = browser.evaluate(ENSURE_FOR_ALL_FILTER_JS)
        except Exception:
            logger.warning(
                "flru_for_all_filter_failed source=%s", self.source_key, exc_info=True
            )
            return
        if isinstance(result, dict) and result.get("clicked"):
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(2000)
            logger.info("flru_for_all_filter_applied source=%s", self.source_key)

    def scan_new(self) -> list[ProjectPreview]:
        browser = self._get_browser()
        skip_closed = bool(self.filters.get("skip_closed", True))
        try:
            browser.navigate(self._listing_url())
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(2500)
            self._ensure_logged_in(browser)
            self._ensure_for_all_filter(browser)
            raw_cards = browser.evaluate(LISTING_EXTRACTOR_JS)
            if not isinstance(raw_cards, list) or not raw_cards:
                snapshot = browser.snapshot()
                raw_cards = parse_listing_from_html(
                    snapshot, skip_closed=skip_closed
                )
        except FlruAuthError:
            raise
        except Exception as exc:
            logger.exception("flru_scan_failed source=%s", self.source_key)
            raise FlruAuthError(f"flru_scan_failed: {exc}") from exc

        previews: list[ProjectPreview] = []
        for item in raw_cards or []:
            pid = str(item.get("project_id") or "").strip()
            if not _PROJECT_ID_RE.fullmatch(pid):
                continue
            previews.append(
                ProjectPreview(
                    platform=self.platform_id,
                    source_key=self.source_key,
                    project_id=pid,
                    url=str(item.get("url") or flru_project_url(pid)),
                    title=str(item.get("title") or ""),
                    budget_text=item.get("budget_text"),
                    published_at=_parse_published(item.get("published_at")),
                    responses_count=item.get("responses_count"),
                )
            )
        logger.info("flru_scan source=%s cards=%d", self.source_key, len(previews))
        return previews

    def read_full(self, project_id: str) -> ProjectFull:
        pid = str(project_id).strip()
        url = flru_project_url(pid)
        browser = self._get_browser()
        try:
            browser.navigate(url)
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(2000)
            self._ensure_logged_in(browser)
            raw = browser.evaluate(PROJECT_EXTRACTOR_JS)
            if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
                snapshot = browser.snapshot()
                raw = parse_project_from_html(snapshot, project_id=pid)
            self._ensure_logged_in(browser)
        except FlruAuthError:
            raise
        except Exception as exc:
            logger.exception("flru_read_failed project_id=%s", pid)
            raise RuntimeError(f"flru_read_failed: {exc}") from exc

        if raw.get("closed"):
            raise FlruProjectClosed(
                "flru_project_closed: исполнитель уже определён"
            )

        title = str(raw.get("title") or "").strip()
        desc = str(raw.get("full_description") or "").strip()
        return ProjectFull(
            platform=self.platform_id,
            source_key=self.source_key,
            project_id=pid,
            url=url,
            title=title or f"Проект {pid}",
            full_description=desc,
            desired_budget=raw.get("desired_budget"),
            max_budget=raw.get("max_budget") or raw.get("desired_budget"),
            offers_count=raw.get("offers_count"),
            buyer=raw.get("buyer"),
            time_left=raw.get("time_left"),
        )

    def submit_response(
        self, project_id: str, text: str, price: str | None
    ) -> SubmitResult:
        return SubmitResult(
            success=False,
            project_id=project_id,
            message="manual_only: autofill/submit для FL.ru не реализован",
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


@dataclass
class FlruSubmittedOffer:
    description: str = ""
    price: str | None = None
    delivery_days: int | None = None
    ok: bool = False
    error: str | None = None


SUBMITTED_OFFER_EXTRACTOR_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const isHeading = (t) => /^ваш\\s+отклик$/i.test(norm(t));
  const isEdit = (t) => /^редактировать$/i.test(norm(t));
  const isRefuse = (t) => /^отказаться/i.test(norm(t));
  const isDate = (t) => /^\\d{2}\\.\\d{2}\\.\\d{4}\\s+в\\s+\\d{1,2}:\\d{2}/.test(norm(t));
  const isTerm = (t) => /^срок\\s*:/i.test(norm(t));
  const isPrice = (t) => /^стоимость\\s+работ\\s*:/i.test(norm(t));
  const isChat = (t) => /^чат$/i.test(norm(t));
  const isOther = (t) => /^другие\\s+заказы/i.test(norm(t));
  const isStats = (t) => /^статистика\\s+откликов/i.test(norm(t));
  const isTail = (t) => isChat(t) || isOther(t) || isStats(t);

  const pickDays = (t) => {
    const m = norm(t).match(/срок\\s*:\\s*(\\d+)\\s*дн/i);
    return m ? parseInt(m[1], 10) : null;
  };
  const pickPrice = (t) => {
    const m = norm(t).match(/стоимость\\s+работ\\s*:\\s*([\\d\\s\\u00a0]+)/i);
    if (!m) return null;
    const digits = m[1].replace(/[^\\d]/g, '');
    return digits || null;
  };

  const nodes = [...document.querySelectorAll('h1,h2,h3,h4,div,span,p,button,a,li')];
  const heading = nodes.find((el) => isHeading(el.textContent || ''));
  if (heading) {
    const root = heading.closest(
      'section,article,[class*="proposal"],[class*="offer"],[class*="response"],[class*="Response"]'
    ) || heading.parentElement;
    let price = null;
    let delivery_days = null;
    const parts = [];
    const walk = root ? [...root.querySelectorAll('*')] : [];
    let passedHeading = false;
    let pendingAuthor = null;
    for (const el of walk) {
      const raw = (el.textContent || '').trim();
      if (!raw) continue;
      const t = norm(raw);
      if (isHeading(t)) { passedHeading = true; continue; }
      if (!passedHeading) continue;
      if (isTail(t)) break;
      if (isEdit(t) || isRefuse(t)) continue;
      if (isDate(t)) { pendingAuthor = null; continue; }
      if (isTerm(t)) {
        delivery_days = pickDays(t) ?? delivery_days;
        continue;
      }
      if (isPrice(t)) {
        price = pickPrice(t) || price;
        continue;
      }
      if (el.children && el.children.length > 2 && t.length > 200) continue;
      if (parts.length && parts[parts.length - 1].includes(t)) continue;
      if (parts.some((p) => t.includes(p) && t.length > p.length + 20)) {
        while (parts.length && t.includes(parts[parts.length - 1])) parts.pop();
      }
      // Short name line before date — hold, drop if next is date.
      if (t.length < 60 && !/[.!?…]$/.test(t) && !isTerm(t) && !isPrice(t)) {
        if (pendingAuthor) parts.push(pendingAuthor);
        pendingAuthor = raw.includes('\\n') ? t : raw;
        continue;
      }
      if (pendingAuthor) { parts.push(pendingAuthor); pendingAuthor = null; }
      parts.push(raw.includes('\\n') ? t : raw);
    }
    if (pendingAuthor) parts.push(pendingAuthor);
    const description = parts.join('\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
    if (description.length >= 20) {
      return { ok: true, error: null, description, price, delivery_days, via: 'dom' };
    }
  }

  const full = document.body?.innerText || '';
  const start = full.search(/ваш\\s+отклик/i);
  if (start < 0) {
    return { ok: false, error: 'block_missing', description: '', price: null, delivery_days: null };
  }
  let chunk = full.slice(start);
  const cutRe = /\\n\\s*(?:чат|другие\\s+заказы|статистика\\s+откликов)/i;
  const cut = chunk.search(cutRe);
  if (cut > 40) chunk = chunk.slice(0, cut);

  let price = null;
  let delivery_days = null;
  const dm = chunk.match(/срок\\s*:\\s*(\\d+)\\s*дн/i);
  if (dm) delivery_days = parseInt(dm[1], 10);
  const pm = chunk.match(/стоимость\\s+работ\\s*:\\s*([\\d\\s\\u00a0]+)/i);
  if (pm) {
    const digits = pm[1].replace(/[^\\d]/g, '');
    if (digits) price = digits;
  }

  const lines = chunk.split(/\\n/);
  const body = [];
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i].trim();
    if (!raw) continue;
    const t = norm(raw);
    if (isHeading(t) || isEdit(t) || isRefuse(t) || isDate(t) || isTerm(t) || isPrice(t)) continue;
    if (isTail(t)) break;
    let j = i + 1;
    while (j < lines.length && !lines[j].trim()) j++;
    if (t.length < 60 && !/[.!?…]$/.test(t) && j < lines.length && isDate(norm(lines[j]))) {
      continue;
    }
    body.push(raw);
  }
  const description = body.join('\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
  if (description.length < 20) {
    return { ok: false, error: 'empty_body', description: '', price, delivery_days };
  }
  return { ok: true, error: null, description, price, delivery_days, via: 'text' };
}
"""


_SUBMITTED_OFFER_TAIL_RE = re.compile(
    r"\n\s*(?:"
    r"чат|"
    r"другие\s+заказы|"
    r"статистика\s+откликов"
    r")",
    re.I,
)
_SUBMITTED_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}\s+в\s+\d{1,2}:\d{2}")
_SUBMITTED_DATE_INLINE_RE = re.compile(
    r"^.+?\d{2}\.\d{2}\.\d{4}\s+в\s+\d{1,2}:\d{2}\s+"
)
_SUBMITTED_TERM_RE = re.compile(r"срок\s*:\s*(\d+)\s*дн", re.I)
_SUBMITTED_PRICE_RE = re.compile(r"стоимость\s+работ\s*:\s*([\d\s\u00a0]+)", re.I)
_SUBMITTED_CHROME_LINE_RE = re.compile(
    r"^(?:"
    r"ваш\s+отклик|"
    r"редактировать(?:\s+отказаться.*)?"
    r"|отказаться.*"
    r")$",
    re.I,
)
_SUBMITTED_META_CUT_RE = re.compile(
    r"\s*(?:срок\s*:\s*\d+\s*дн\w*|стоимость\s+работ\s*:|чат)\b.*$",
    re.I | re.S,
)


def _clean_submitted_description(body: str) -> str:
    """Strip FL.ru chrome that DOM/text extractors sometimes leave in."""
    text = (body or "").strip()
    if not text:
        return text
    # Drop mashed leading chrome / author+date prefix on first line(s).
    lines: list[str] = []
    for i, line in enumerate(text.splitlines()):
        raw = line.strip()
        if not raw:
            continue
        if _SUBMITTED_CHROME_LINE_RE.match(raw):
            continue
        if _SUBMITTED_DATE_RE.match(raw):
            continue
        if i < 3:
            raw2 = _SUBMITTED_DATE_INLINE_RE.sub("", raw).strip()
            if raw2 != raw:
                raw = raw2
            if re.match(r"^редактировать\b", raw, flags=re.I):
                continue
        if re.match(r"^срок\s*:", raw, flags=re.I):
            break
        if re.match(r"^стоимость\s+работ\s*:", raw, flags=re.I):
            break
        if re.match(r"^чат$", raw, flags=re.I):
            break
        lines.append(raw)
    cleaned = "\n".join(lines).strip()
    cleaned = _SUBMITTED_META_CUT_RE.sub("", cleaned).strip()
    return cleaned


def parse_submitted_offer_from_text(text: str) -> FlruSubmittedOffer:
    """Parse «Ваш отклик» from FL.ru page text; stop at Чат / Другие заказы."""
    full = text or ""
    start = re.search(r"ваш\s+отклик", full, flags=re.I)
    if not start:
        return FlruSubmittedOffer(ok=False, error="block_missing")
    chunk = full[start.start() :]
    cut = _SUBMITTED_OFFER_TAIL_RE.search(chunk)
    if cut and cut.start() > 40:
        chunk = chunk[: cut.start()]

    delivery_days: int | None = None
    dm = _SUBMITTED_TERM_RE.search(chunk)
    if dm:
        try:
            delivery_days = int(dm.group(1))
        except ValueError:
            delivery_days = None

    price: str | None = None
    pm = _SUBMITTED_PRICE_RE.search(chunk)
    if pm:
        digits = re.sub(r"[^\d]", "", pm.group(1))
        if digits:
            price = digits

    body_lines: list[str] = []
    lines = chunk.splitlines()
    for i, line in enumerate(lines):
        raw = line.strip()
        if not raw:
            continue
        if _SUBMITTED_CHROME_LINE_RE.match(raw):
            continue
        if _SUBMITTED_DATE_RE.match(raw):
            continue
        if re.match(r"^срок\s*:", raw, flags=re.I):
            continue
        if re.match(r"^стоимость\s+работ\s*:", raw, flags=re.I):
            continue
        if re.match(r"^чат$", raw, flags=re.I):
            break
        if re.match(r"^другие\s+заказы", raw, flags=re.I):
            break
        if re.match(r"^статистика\s+откликов", raw, flags=re.I):
            break
        if len(raw) < 60 and not re.search(r"[.!?…]$", raw):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _SUBMITTED_DATE_RE.match(lines[j].strip()):
                continue
        # Author+date mashed into first body line.
        raw = _SUBMITTED_DATE_INLINE_RE.sub("", raw).strip()
        if not raw or _SUBMITTED_CHROME_LINE_RE.match(raw):
            continue
        body_lines.append(raw)

    body = _clean_submitted_description(
        re.sub(r"\n{3,}", "\n\n", "\n".join(body_lines)).strip()
    )
    if len(body) < 20:
        return FlruSubmittedOffer(
            ok=False, error="empty_body", price=price, delivery_days=delivery_days
        )
    return FlruSubmittedOffer(
        description=body, price=price, delivery_days=delivery_days, ok=True
    )


def _submitted_looks_dirty(description: str) -> bool:
    low = (description or "").lower()
    return bool(
        re.search(r"редактировать|отказаться от заказа|стоимость работ\s*:", low)
        or re.search(r"\bчат\b", low)
    )


def read_submitted_offer_text(
    browser: BrowserClient, project_id: str
) -> FlruSubmittedOffer:
    """Open /projects/{id}/ and read the submitted «Ваш отклик» block."""
    pid = str(project_id or "").strip()
    if not _PROJECT_ID_RE.fullmatch(pid):
        return FlruSubmittedOffer(ok=False, error="bad_project_id")
    url = flru_project_url(pid)
    try:
        browser.navigate(url)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(3500)
    except Exception as exc:
        return FlruSubmittedOffer(ok=False, error=f"navigate_failed: {exc}")

    # Prefer line-oriented innerText — DOM walk often swallows chrome into parents.
    inner = ""
    try:
        inner = str(browser.evaluate("() => document.body?.innerText || ''") or "")
    except Exception:
        inner = ""
    parsed = parse_submitted_offer_from_text(inner)
    if parsed.ok and not _submitted_looks_dirty(parsed.description):
        return parsed

    try:
        raw = browser.evaluate(SUBMITTED_OFFER_EXTRACTOR_JS)
    except Exception as exc:
        logger.warning("flru_submitted_js_failed project_id=%s err=%s", pid, exc)
        raw = None

    if isinstance(raw, dict) and raw.get("ok") and str(raw.get("description") or "").strip():
        days = raw.get("delivery_days")
        try:
            delivery_days = int(days) if days is not None else None
        except (TypeError, ValueError):
            delivery_days = None
        desc = _clean_submitted_description(str(raw["description"]))
        if desc and not _submitted_looks_dirty(desc):
            return FlruSubmittedOffer(
                description=desc,
                price=(str(raw["price"]) if raw.get("price") else None),
                delivery_days=delivery_days,
                ok=True,
            )
        # Dirty DOM → re-parse cleaned blob / innerText.
        reparsed = parse_submitted_offer_from_text(
            "Ваш отклик\n" + str(raw.get("description") or "")
        )
        if reparsed.ok and not _submitted_looks_dirty(reparsed.description):
            if not reparsed.price and raw.get("price"):
                reparsed = FlruSubmittedOffer(
                    description=reparsed.description,
                    price=str(raw["price"]),
                    delivery_days=reparsed.delivery_days or delivery_days,
                    ok=True,
                )
            elif reparsed.delivery_days is None and delivery_days is not None:
                reparsed = FlruSubmittedOffer(
                    description=reparsed.description,
                    price=reparsed.price,
                    delivery_days=delivery_days,
                    ok=True,
                )
            return reparsed

    if parsed.ok:
        cleaned = _clean_submitted_description(parsed.description)
        if cleaned:
            return FlruSubmittedOffer(
                description=cleaned,
                price=parsed.price,
                delivery_days=parsed.delivery_days,
                ok=True,
            )

    snap = ""
    try:
        snap = browser.snapshot() or ""
    except Exception:
        snap = ""
    if snap:
        html_parsed = parse_submitted_offer_from_text(snap)
        if html_parsed.ok:
            return html_parsed

    err = None
    if isinstance(raw, dict):
        err = raw.get("error")
    return FlruSubmittedOffer(
        ok=False,
        error=str(err or parsed.error or "read_failed"),
        price=parsed.price,
        delivery_days=parsed.delivery_days,
    )
