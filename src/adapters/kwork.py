from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from src.adapters.kwork_auth import (
    KworkAuthError,
    KworkCredentials,
    ensure_logged_in,
    is_logged_in,
)
from src.browser.base import BrowserClient
from src.models import ProjectFull, ProjectPreview, ReplyEvent, SubmitResult

LISTING_EXTRACTOR_JS = """
(() => {
  const cards = [];
  const seen = new Set();
  let roots = [
    ...document.querySelectorAll('.want-card, article.project-card, [data-project-id]'),
  ];
  if (!roots.length) {
    roots = [...document.querySelectorAll('a[href*="/projects/"]')];
  }

  for (const root of roots) {
    const link = root.matches('a[href*="/projects/"]')
      ? root
      : root.querySelector('a[href*="/projects/"]');
    if (!link) continue;
    const href = link.getAttribute('href') || '';
    const m = href.match(/\\/projects\\/(\\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);

    const card = root.matches('a')
      ? link.closest('.want-card, article, [data-project-id]') || root.parentElement
      : root;
    const titleEl =
      card?.querySelector('.wants-card__header-title a, .project-card__title, h1 a, h2 a, h3 a') ||
      link;
    const budgetEl = card?.querySelector('.wants-card__price, .project-card__budget, [data-budget]');
    const respEl = card?.querySelector(
      '.wants-card__review-count, .project-card__responses, [data-responses]'
    );
    const timeEl = card?.querySelector('time');

    let responses = null;
    if (respEl) {
      const n = parseInt(
        respEl.getAttribute('data-responses') || respEl.textContent.replace(/\\D/g, ''),
        10
      );
      responses = Number.isFinite(n) ? n : null;
    }

    cards.push({
      project_id: m[1],
      url: link.href.startsWith('http') ? link.href : 'https://kwork.ru' + href,
      title: (titleEl?.textContent || '').trim(),
      budget_text:
        budgetEl?.textContent?.replace(/\\s+/g, ' ').trim() ||
        budgetEl?.getAttribute('data-budget') ||
        null,
      responses_count: responses,
      published_at: timeEl?.getAttribute('datetime') || null,
    });
  }
  return cards;
})()
"""

PROJECT_EXTRACTOR_JS = """
(() => {
  const root = document.querySelector('main.project-page, [data-project-id]') || document;
  const projectId = root.getAttribute?.('data-project-id')
    || (location.pathname.match(/\\/projects\\/(\\d+)/) || [])[1]
    || null;
  const titleEl = root.querySelector('.project-title, h1');
  const descEl = root.querySelector('.project-description, [data-description]');
  const expandBtn = root.querySelector('.show-full-description, [data-expand-description]');
  if (expandBtn && !expandBtn.dataset.clicked) {
    expandBtn.click();
    expandBtn.dataset.clicked = '1';
  }
  const tags = [...root.querySelectorAll('.tags .tag, .tag-list .tag')]
    .map(el => el.textContent.trim())
    .filter(Boolean);

  const pageText = (root.textContent || '').replace(/\\s+/g, ' ');
  const findBudget = (pattern) => {
    const re = new RegExp(pattern + '[^₽]{0,40}([\\d\\s]+)\\s*₽', 'i');
    const m = pageText.match(re);
    return m ? m[1].replace(/\\s/g, ' ').trim() + ' ₽' : null;
  };

  const offersRaw = root.querySelector('.offers-count, [data-offers]');
  let offers = null;
  if (offersRaw) {
    const n = parseInt(offersRaw.getAttribute('data-offers') || offersRaw.textContent.replace(/\\D/g, ''), 10);
    offers = Number.isFinite(n) ? n : null;
  }

  return {
    project_id: projectId,
    url: location.href,
    title: (titleEl?.textContent || '').trim(),
    full_description: (descEl?.textContent || descEl?.getAttribute('data-description') || '').trim(),
    desired_budget:
      root.querySelector('.desired-budget, [data-desired-budget]')?.textContent?.trim()
      || findBudget('желаем')
      || null,
    max_budget:
      root.querySelector('.max-budget, [data-max-budget]')?.textContent?.trim()
      || findBudget('допустим')
      || findBudget('до')
      || null,
    offers_count: offers,
    buyer: root.querySelector('.buyer-link, [data-buyer]')?.textContent?.trim() || null,
    buyer_hire_rate: root.querySelector('.buyer-hire-rate, [data-buyer-hire-rate]')?.textContent?.trim() || null,
    time_left: root.querySelector('.time-left, [data-time-left]')?.textContent?.trim() || null,
    tags,
  };
})()
"""

OFFER_BUTTON_SELECTOR = ".offer-button, [data-action='offer']"
OFFER_TEXT_SELECTOR = ".offer-description, [data-field='description'], textarea[name='description']"
OFFER_PRICE_SELECTOR = ".offer-price, [data-field='price'], input[name='price']"
OFFER_SUBMIT_SELECTOR = ".offer-submit, [data-action='submit']"


OFFER_OPEN_JS = """
(() => {
  const norm = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('a, button, span')];
  const btn =
    nodes.find((el) => /^предложить услугу$/i.test(norm(el))) ||
    nodes.find((el) => /^оставить отклик$/i.test(norm(el))) ||
    nodes.find((el) => /^откликнуться$/i.test(norm(el))) ||
    nodes.find((el) =>
      /предложить услугу|оставить отклик|откликнуться/i.test(norm(el))
    );
  if (!btn) return { ok: false, reason: 'offer_button_not_found' };
  (btn.closest('.kw-button') || btn).click();
  return { ok: true, text: norm(btn).slice(0, 80) };
})()
"""


def _build_offer_fill_js(text: str, price: str, delivery_days: int) -> str:
    payload = json.dumps(
        {"text": text, "price": str(price), "days": delivery_days},
        ensure_ascii=False,
    )
    return f"""
(() => {{
  const payload = {payload};

  function setValue(el, value) {{
    if (!el) return;
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }}

  const roots = [
    document.querySelector('.modal'),
    document.querySelector('.popup'),
    document.querySelector('[class*="offer"]'),
    document.querySelector('form'),
    document,
  ].filter(Boolean);

  let desc = null;
  let priceInput = null;
  for (const root of roots) {{
    if (!desc) {{
      desc =
        root.querySelector('textarea[name="description"]') ||
        root.querySelector('textarea.v-textarea') ||
        root.querySelector('.wants-offer__description textarea') ||
        root.querySelector('textarea');
    }}
    if (!priceInput) {{
      priceInput =
        root.querySelector('#offer-custom-price') ||
        root.querySelector('input[name="price"]') ||
        root.querySelector('.wants-offer__price input') ||
        root.querySelector('[data-field="price"] input') ||
        root.querySelector('.stages__stage-price-input') ||
        root.querySelector('input[type="number"]') ||
        root.querySelector('input[type="tel"].wMax');
    }}
  }}

  setValue(desc, payload.text);
  setValue(priceInput, payload.price);

  const payCard = [...document.querySelectorAll('label, div, span, button')].find((el) =>
    /целиком.*заказ выполнен/i.test((el.textContent || '').replace(/\\s+/g, ' '))
  );
  if (payCard) payCard.click();

  function dayLabel(days) {{
    if (days % 10 === 1 && days % 100 !== 11) return days + ' день';
    if (days % 10 >= 2 && days % 10 <= 4 && (days % 100 < 10 || days % 100 >= 20)) return days + ' дня';
    return days + ' дней';
  }}

  function pickDeadlineOption(days) {{
    const patterns = [
      days + ' дн',
      dayLabel(days),
      String(days),
    ];
    const nodes = [
      ...document.querySelectorAll('select option'),
      ...document.querySelectorAll('.multiselect__option, .v-list-item, li[role="option"], [class*="option"]'),
    ];
    let best = null;
    let bestDiff = Infinity;
    for (const node of nodes) {{
      const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
      if (!text) continue;
      if (patterns.some((p) => text.includes(p))) return node;
      const m = text.match(/(\\d{{1,2}})\\s*дн/i);
      if (m) {{
        const diff = Math.abs(parseInt(m[1], 10) - days);
        if (diff < bestDiff) {{
          bestDiff = diff;
          best = node;
        }}
      }}
    }}
    return best;
  }}

  function setDeadline(days) {{
    for (const sel of document.querySelectorAll('select')) {{
      const opt = pickDeadlineOption(days);
      if (opt && opt.tagName === 'OPTION') {{
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return true;
      }}
    }}

    const triggers = [
      ...document.querySelectorAll('.multiselect, .multiselect__select, [class*="deadline"], [class*="duration"]'),
      ...[...document.querySelectorAll('label, .field-name, .form-field__name')].filter((el) =>
        /срок выполнения/i.test(el.textContent || '')
      ).map((el) => el.closest('.form-field, .field, .form-item, div')?.querySelector('.multiselect, input, button, [role="combobox"]'))
        .filter(Boolean),
    ];

    for (const trigger of triggers) {{
      try {{ trigger.click(); }} catch (e) {{}}
      const opt = pickDeadlineOption(days);
      if (opt) {{
        try {{ opt.click(); }} catch (e) {{}}
        return true;
      }}
    }}
    return false;
  }}

  const daysSet = setDeadline(payload.days);

  const submitBtn = [...document.querySelectorAll('button, input[type="submit"]')].find((el) =>
    /^предложить$/i.test((el.textContent || el.value || '').trim())
  );

  return {{
    ok: Boolean(desc && priceInput),
    hasDesc: Boolean(desc),
    hasPrice: Boolean(priceInput),
    daysSet,
    submitFound: Boolean(submitBtn),
  }};
}})()
"""


def kwork_offer_form_url(project_id: str) -> str:
    return f"https://kwork.ru/new_offer?project={project_id}"


def _parse_listing_block(block: str) -> dict[str, Any] | None:
    pid = _first_group(r'data-project-id="(\d+)"', block)
    if not pid:
        pid = _first_group(r'href="(?:https?://[^"]*)?/projects/(\d+)"', block)
    if not pid:
        pid = _first_group(r"/projects/(\d+)", block)
    if not pid:
        return None

    href = _first_group(r'href="([^"]+/projects/\d+)"', block) or f"https://kwork.ru/projects/{pid}"
    title = (
        _first_group(r'class="wants-card__header-title[^"]*"[^>]*>\s*<a[^>]*>([^<]+)', block)
        or _first_group(r'class="project-card__title"[^>]*>([^<]+)', block)
        or _first_group(r'href="[^"]*/projects/\d+"[^>]*>([^<]+)', block)
        or ""
    )
    budget = _first_group(r'class="wants-card__price"[^>]*>(.*?)</div>', block)
    if budget:
        budget = re.sub(r"<[^>]+>", " ", budget)
        budget = re.sub(r"\s+", " ", budget).strip()
    if not budget:
        budget = _first_group(r'class="project-card__budget"[^>]*>([^<]+)', block)
    responses_raw = _first_group(r'data-responses="(\d+)"', block)
    if not responses_raw:
        responses_raw = _first_group(r'class="project-card__responses"[^>]*>(\d+)', block)
    published = _first_group(r'datetime="([^"]+)"', block)

    return {
        "project_id": pid,
        "url": href if href.startswith("http") else f"https://kwork.ru/projects/{pid}",
        "title": title.strip(),
        "budget_text": budget.strip() if budget else None,
        "responses_count": int(responses_raw) if responses_raw else None,
        "published_at": published,
    }


def parse_listing_from_html(html: str) -> list[dict[str, Any]]:
    blocks = re.findall(
        r'<article[^>]*class="project-card"[^>]*>.*?</article>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not blocks:
        blocks = re.findall(
            r'<div[^>]*class="[^"]*\bwant-card\b[^"]*"[^>]*>.*?(?=<div[^>]*class="[^"]*\bwant-card\b|\Z)',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    if not blocks:
        blocks = re.findall(
            r'<[^>]+data-project-id="\d+"[^>]*>.*?(?=<(?:article|div[^>]*want-card|/body)|\Z)',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        item = _parse_listing_block(block)
        if not item or item["project_id"] in seen:
            continue
        seen.add(item["project_id"])
        results.append(item)
    return results


def parse_project_from_html(html: str, project_id: str | None = None) -> dict[str, Any]:
    root = html
    pid = project_id or _first_group(r'data-project-id="(\d+)"', root)
    if not pid:
        pid = _first_group(r'/projects/(\d+)', root) or ""

    title = _first_group(r'class="project-title"[^>]*>([^<]+)', root) or ""
    description = _first_group(r'class="project-description"[^>]*>([^<]+)', root)
    if not description:
        description = _first_group(r'data-description="([^"]+)"', root) or ""

    tags = re.findall(r'class="tag"[^>]*>([^<]+)', root, flags=re.IGNORECASE)
    offers_raw = _first_group(r'data-offers="(\d+)"', root)
    if not offers_raw:
        offers_raw = _first_group(r'class="offers-count"[^>]*>(\d+)', root)

    return {
        "project_id": pid,
        "url": f"https://kwork.ru/projects/{pid}" if pid else "",
        "title": title.strip(),
        "full_description": description.strip(),
        "desired_budget": _optional_text(r'class="desired-budget"[^>]*>([^<]+)', root),
        "max_budget": _optional_text(r'class="max-budget"[^>]*>([^<]+)', root),
        "offers_count": int(offers_raw) if offers_raw else None,
        "buyer": _optional_text(r'class="buyer-link"[^>]*>([^<]+)', root),
        "buyer_hire_rate": _optional_text(r'class="buyer-hire-rate"[^>]*>([^<]+)', root),
        "time_left": _optional_text(r'class="time-left"[^>]*>([^<]+)', root),
        "tags": [t.strip() for t in tags if t.strip()],
    }


def _first_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else None


def _optional_text(pattern: str, text: str) -> str | None:
    value = _first_group(pattern, text)
    return value.strip() if value else None


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class KworkAdapter:
    platform_id = "kwork"

    def __init__(
        self,
        *,
        source_key: str,
        listing_url: str,
        browser: BrowserClient,
        dry_run_submit: bool | None = None,
        kwork_credentials: KworkCredentials | None = None,
        auto_login: bool = True,
    ) -> None:
        self.source_key = source_key
        self.listing_url = listing_url
        self.browser = browser
        self.kwork_credentials = kwork_credentials
        self.auto_login = auto_login
        if dry_run_submit is None:
            self.dry_run_submit = os.environ.get("DRY_RUN_SUBMIT", "").lower() in (
                "1",
                "true",
                "yes",
            )
        else:
            self.dry_run_submit = dry_run_submit

    def _ensure_auth(self) -> None:
        if not self.auto_login:
            return
        ensure_logged_in(self.browser, self.kwork_credentials)

    def scan_new(self) -> list[ProjectPreview]:
        self._ensure_auth()
        self.browser.navigate(self.listing_url)
        raw_cards = self.browser.evaluate(LISTING_EXTRACTOR_JS)
        if not isinstance(raw_cards, list):
            snapshot = self.browser.snapshot()
            raw_cards = parse_listing_from_html(snapshot)

        previews: list[ProjectPreview] = []
        for item in raw_cards:
            previews.append(
                ProjectPreview(
                    platform=self.platform_id,
                    source_key=self.source_key,
                    project_id=str(item["project_id"]),
                    url=str(item["url"]),
                    title=str(item.get("title") or ""),
                    budget_text=item.get("budget_text"),
                    published_at=_parse_published(item.get("published_at")),
                    responses_count=item.get("responses_count"),
                )
            )
        return previews

    def read_full(self, project_id: str) -> ProjectFull:
        self._ensure_auth()
        url = f"https://kwork.ru/projects/{project_id}"
        self.browser.navigate(url)
        raw = self.browser.evaluate(PROJECT_EXTRACTOR_JS)
        if not isinstance(raw, dict):
            snapshot = self.browser.snapshot()
            raw = parse_project_from_html(snapshot, project_id=project_id)

        return ProjectFull(
            platform=self.platform_id,
            source_key=self.source_key,
            project_id=str(raw.get("project_id") or project_id),
            url=str(raw.get("url") or url),
            title=str(raw.get("title") or ""),
            full_description=str(raw.get("full_description") or ""),
            desired_budget=raw.get("desired_budget"),
            max_budget=raw.get("max_budget"),
            offers_count=raw.get("offers_count"),
            buyer=raw.get("buyer"),
            buyer_hire_rate=raw.get("buyer_hire_rate"),
            time_left=raw.get("time_left"),
            tags=list(raw.get("tags") or []),
        )

    def _ensure_session(self) -> None:
        if not self.kwork_credentials:
            return
        self.browser.navigate("https://kwork.ru/")
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(1000)
        if not is_logged_in(self.browser):
            ensure_logged_in(self.browser, self.kwork_credentials)

    def prepare_response(
        self,
        project_id: str,
        text: str,
        price: str,
        *,
        delivery_days: int = 14,
    ) -> SubmitResult:
        if self.kwork_credentials:
            self._ensure_session()
        elif not self.auto_login:
            self.browser.navigate("https://kwork.ru/")
            if hasattr(self.browser, "wait_ms"):
                self.browser.wait_ms(1000)
            if not is_logged_in(self.browser):
                return SubmitResult(
                    success=False,
                    project_id=project_id,
                    message="not_logged_in: нужен логин Kwork на VPS",
                )
        else:
            self._ensure_auth()

        url = f"https://kwork.ru/projects/{project_id}/view"
        self.browser.navigate(url)
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(2000)

        if not is_logged_in(self.browser):
            return SubmitResult(
                success=False,
                project_id=project_id,
                message="not_logged_in: нужен логин Kwork на VPS",
            )

        opened = self.browser.evaluate(OFFER_OPEN_JS)
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(5000)

        if isinstance(opened, dict) and not opened.get("ok"):
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"offer_button_not_found: {opened}",
            )

        fill_result = self.browser.evaluate(
            _build_offer_fill_js(text, price, delivery_days)
        )
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(1000)

        if not isinstance(fill_result, dict) or not fill_result.get("ok"):
            details = fill_result if isinstance(fill_result, dict) else {}
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_failed: {details}",
            )

        return SubmitResult(
            success=True,
            project_id=project_id,
            message="prepared: form filled, submit not clicked",
        )

    def submit_response(
        self, project_id: str, text: str, price: str | None
    ) -> SubmitResult:
        self._ensure_auth()
        url = f"https://kwork.ru/projects/{project_id}"
        self.browser.navigate(url)
        self.browser.click(OFFER_BUTTON_SELECTOR)
        self.browser.fill(OFFER_TEXT_SELECTOR, text)
        if price:
            self.browser.fill(OFFER_PRICE_SELECTOR, price)
        if self.dry_run_submit:
            return SubmitResult(
                success=True,
                project_id=project_id,
                message="dry_run: submit skipped",
            )
        self.browser.click(OFFER_SUBMIT_SELECTOR)
        return SubmitResult(
            success=True,
            project_id=project_id,
            message="submitted",
        )

    def monitor_replies(self) -> list[ReplyEvent]:
        raise NotImplementedError("KworkAdapter.monitor_replies: planned for PL-8")
