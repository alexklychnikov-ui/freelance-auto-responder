"""FL.ru adapter — scan + read only (manual reply MVP, like Yandex Uslugi)."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from src.adapters.flru_urls import FLRU_ORIGIN, flru_project_url
from src.browser.factory import close_browser_client, get_browser_client
from src.config import Settings
from src.models import ProjectFull, ProjectPreview, ReplyEvent, SubmitResult

logger = logging.getLogger(__name__)

_PROJECT_ID_RE = re.compile(r"^\d{5,9}$")

LISTING_EXTRACTOR_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
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
    if (low.includes('исполнитель определ')) continue;
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
    const respMatch = cardText.match(/(\\d+)\\s+ответ/i);
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

  const closed = /исполнитель определ/i.test(document.body?.innerText || '');
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
  const offM = bodyText.match(/(\\d+)\\s+ответ/i);
  if (offM) offers_count = parseInt(offM[1], 10);

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
        if skip_closed and "исполнитель определ" in low:
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
        rm = re.search(r"(\d+)\s+ответ", window, flags=re.I)
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

    offers = None
    om = re.search(r"(\d+)\s+ответ", html, flags=re.I)
    if om:
        offers = int(om.group(1))

    closed = bool(re.search(r"исполнитель определ", html, flags=re.I))

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
        _ = browser
        self.source_key = source_key
        self.listing_url = listing_url
        self.settings = settings
        self.filters = filters or {}
        self._browser: Any | None = None
        self._owns_browser = True

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

    def scan_new(self) -> list[ProjectPreview]:
        browser = self._get_browser()
        skip_closed = bool(self.filters.get("skip_closed", True))
        try:
            browser.navigate(self.listing_url)
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(2500)
            self._ensure_logged_in(browser)
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
            raise RuntimeError("flru_project_closed: исполнитель уже определён")

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
