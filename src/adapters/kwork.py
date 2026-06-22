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

DESC_SELECTORS = (
    'textarea[name="description"]',
    "textarea.v-textarea",
    ".wants-offer__description textarea",
    "textarea",
)
PRICE_SELECTORS = (
    "#offer-custom-price",
    'input[name="price"]',
    ".wants-offer__price input",
    '[data-field="price"] input',
    ".stages__stage-price-input",
    'input[type="number"]',
)
TITLE_SELECTORS = (
    'input[placeholder*="Название заказа"]',
    'input[placeholder*="название заказа"]',
)

EXTRACT_ORDER_TITLE_ON_PAGE_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

  const fromDetails = () => {
    const headers = [...document.querySelectorAll('h2, h3, h4, div, span, p')].filter((el) =>
      /^детали проекта$/i.test(norm(el.textContent))
    );
    for (const hdr of headers) {
      const block =
        hdr.closest('aside, section, [class*="detail"], [class*="info"], [class*="sidebar"]')
        || hdr.parentElement?.parentElement;
      if (!block) continue;
      const link = block.querySelector('a[href*="/projects/"]');
      if (link) {
        const t = norm(link.textContent);
        if (t.length >= 3 && t.length <= 70) return t;
      }
      const lines = norm(block.textContent)
        .split(/\\n|(?<=\\. )/)
        .map(norm)
        .filter((line) => line.length >= 5 && line.length <= 70);
      for (const line of lines) {
        if (/детали проекта|бюджет|допустим|желаем|разработка и it|скрипты/i.test(line)) {
          continue;
        }
        if (!/^\\d/.test(line)) return line;
      }
    }
    return '';
  };

  const fromSelectors = () => {
    const sels = [
      '.wants-card__header-title a',
      '.wants-card__header-title',
      '.order-details a[href*="/projects/"]',
      '[data-project-title]',
    ];
    for (const sel of sels) {
      const el = document.querySelector(sel);
      const t = norm(el?.textContent || el?.getAttribute('data-project-title') || '');
      if (t.length >= 3 && t.length <= 70) return t;
    }
    return '';
  };

  return fromDetails() || fromSelectors();
}
"""

READ_OFFER_FORM_JS = """
() => {
  const desc =
    document.querySelector('textarea[name="description"]') ||
    document.querySelector('textarea.v-textarea') ||
    document.querySelector('.wants-offer__description textarea') ||
    document.querySelector('textarea');
  const priceInput =
    document.querySelector('#offer-custom-price') ||
    document.querySelector('input[name="price"]') ||
    document.querySelector('.wants-offer__price input') ||
    document.querySelector('input[type="number"]');
  const titleInput = [...document.querySelectorAll('input[type="text"], input:not([type])')].find((inp) =>
    /название заказа/i.test(inp.getAttribute('placeholder') || '')
  ) || (() => {
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const label = [...document.querySelectorAll('label, .form-field__name, .field-name, span')].find(
      (el) => /^название заказа$/i.test(norm(el.textContent))
    );
    if (!label) return null;
    const root = label.closest('.form-field, .form-item, .field, div') || label.parentElement;
    return root?.querySelector('input[type="text"], input:not([type="hidden"])') || null;
  })();
  return {
    url: location.href,
    descLen: (desc?.value || '').length,
    descPreview: (desc?.value || '').slice(0, 100),
    price: (priceInput?.value || '').replace(/\\s/g, ''),
    title: titleInput?.value || '',
  };
}
"""

TRIGGER_OFFER_AUTOSAVE_JS = """
() => {
  const fields = [
    ...document.querySelectorAll('textarea, input[type="text"], input[type="number"], input[type="tel"]'),
  ];
  for (const el of fields) {
    try {
      el.focus();
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.blur();
    } catch (e) {}
  }
  document.body.click();
  return true;
}
"""


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


def _build_offer_fill_js(text: str, price: str, *, order_title: str = "") -> str:
    payload = json.dumps(
        {"text": text, "price": str(price), "title": order_title},
        ensure_ascii=False,
    )
    return f"""
(() => {{
  const payload = {payload};

  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

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
  let titleInput = null;
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
    if (!titleInput) {{
      const label = [...document.querySelectorAll('label, .form-field__name, .field-name, span, div')].find(
        (el) => /^название заказа$/i.test(norm(el.textContent))
      );
      if (label) {{
        const root =
          label.closest('.form-field, .form-item, .field, .offer-form__row, div') ||
          label.parentElement;
        titleInput = root?.querySelector('input[type="text"], input:not([type="hidden"])') || null;
      }}
    }}
    if (!titleInput) {{
      titleInput = [...root.querySelectorAll('input[type="text"], input:not([type])')].find((inp) =>
        /название заказа/i.test(inp.getAttribute('placeholder') || '')
      ) || null;
    }}
  }}

  setValue(desc, payload.text);
  setValue(priceInput, payload.price);
  if (titleInput && payload.title) setValue(titleInput, payload.title.slice(0, 70));

  const payCard = [...document.querySelectorAll('label, div, span, button')].find((el) =>
    /целиком.*заказ выполнен/i.test((el.textContent || '').replace(/\\s+/g, ' '))
  );
  if (payCard) payCard.click();

  const submitBtn = [...document.querySelectorAll('button, input[type="submit"]')].find((el) =>
    /^предложить$/i.test((el.textContent || el.value || '').trim())
  );

  return {{
    ok: Boolean(desc && priceInput),
    hasDesc: Boolean(desc),
    hasPrice: Boolean(priceInput),
    hasTitle: Boolean(titleInput && payload.title),
    submitFound: Boolean(submitBtn),
  }};
}})()
"""


DEADLINE_OPEN_JS = """
(() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

  function findDeadlineField() {
    const label = [...document.querySelectorAll('label, .form-field__name, .field-name, span, p, div')]
      .find((el) => /^срок выполнения$/i.test(norm(el.textContent)));
    if (label) {
      const root = label.closest(
        '.form-field, .form-item, .field, .offer-form__row, .wants-offer__field, .offer-field, div'
      );
      const ms = root?.querySelector('.multiselect');
      if (ms) return ms;
    }
    return [...document.querySelectorAll('.multiselect')].find((ms) =>
      /срок выполнения/i.test(norm(ms.textContent))
    ) || null;
  }

  const ms = findDeadlineField();
  if (!ms) {
    return { ok: false, reason: 'multiselect_not_found' };
  }

  const openers = [
    ms.querySelector('.multiselect__select'),
    ms.querySelector('.multiselect__tags'),
    ms.querySelector('.multiselect-single'),
    ms,
  ].filter(Boolean);

  for (const opener of openers) {
    try {
      opener.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      opener.click();
    } catch (e) {}
  }

  return {
    ok: true,
    active: ms.classList.contains('multiselect--active'),
    cls: String(ms.className || '').slice(0, 120),
  };
})()
"""


def _build_deadline_pick_js(delivery_days: int) -> str:
    return f"""
(() => {{
  const targetDays = {delivery_days};
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

  function dayVariants(days) {{
    const out = [
      days + ' день',
      days + ' дня',
      days + ' дней',
      days + ' дн.',
      days + ' дн',
      String(days),
    ];
    if (days >= 7 && days % 7 === 0) {{
      const weeks = days / 7;
      out.push(weeks + ' неделя', weeks + ' недели', weeks + ' недель', weeks + ' нед');
    }}
    return out;
  }}

  function scoreOption(text, days) {{
    const m = text.match(/(\\d{{1,3}})/);
    if (!m) return Infinity;
    const n = parseInt(m[1], 10);
    if (/нед/i.test(text)) return Math.abs(n * 7 - days);
    return Math.abs(n - days);
  }}

  function collectOptions() {{
    const nodes = [
      ...document.querySelectorAll('.multiselect__content .multiselect__option'),
      ...document.querySelectorAll('.multiselect__content-wrapper .multiselect__option'),
      ...document.querySelectorAll('.multiselect--active .multiselect__option'),
      ...document.querySelectorAll('.multiselect__option'),
      ...document.querySelectorAll('[role="option"]'),
      ...document.querySelectorAll('select option'),
    ];
    const out = [];
    const seen = new Set();
    for (const node of nodes) {{
      const text = norm(node.textContent);
      if (!text || text.length > 50 || !/\\d/.test(text)) continue;
      if (!/дн|нед|day/i.test(text)) continue;
      if (seen.has(text)) continue;
      seen.add(text);
      out.push({{ node, text }});
    }}
    return out;
  }}

  function pickFromSelect() {{
    for (const sel of document.querySelectorAll('select')) {{
      let best = null;
      let bestScore = Infinity;
      for (const opt of sel.options) {{
        const text = norm(opt.textContent);
        if (!/\\d/.test(text)) continue;
        const score = scoreOption(text, targetDays);
        if (score < bestScore) {{
          bestScore = score;
          best = opt;
        }}
      }}
      if (best && bestScore <= 7) {{
        sel.value = best.value;
        sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
        sel.dispatchEvent(new Event('input', {{ bubbles: true }}));
        return {{ ok: true, picked: norm(best.textContent), method: 'native_select' }};
      }}
    }}
    return null;
  }}

  const variants = dayVariants(targetDays);
  const options = collectOptions();
  for (const {{ node, text }} of options) {{
    if (variants.some((v) => text.toLowerCase().includes(v.toLowerCase()))) {{
      node.click();
      return {{ ok: true, picked: text, method: 'exact', optionsCount: options.length }};
    }}
  }}

  let best = null;
  let bestScore = Infinity;
  for (const item of options) {{
    const score = scoreOption(item.text, targetDays);
    if (score < bestScore) {{
      bestScore = score;
      best = item;
    }}
  }}
  if (best && bestScore <= 7) {{
    best.node.click();
    return {{
      ok: true,
      picked: best.text,
      method: 'nearest',
      diff: bestScore,
      optionsCount: options.length,
    }};
  }}

  const native = pickFromSelect();
  if (native) return native;

  return {{
    ok: false,
    reason: 'option_not_found',
    optionsCount: options.length,
    sample: options.slice(0, 15).map((o) => o.text),
  }};
}})()
"""


def _fill_deadline(browser: BrowserClient, delivery_days: int) -> dict[str, Any]:
    opened = browser.evaluate(DEADLINE_OPEN_JS)
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(1500)
    picked = browser.evaluate(_build_deadline_pick_js(delivery_days))
    if isinstance(picked, dict) and not picked.get("ok") and hasattr(browser, "wait_ms"):
        browser.wait_ms(800)
        browser.evaluate(DEADLINE_OPEN_JS)
        browser.wait_ms(1200)
        picked = browser.evaluate(_build_deadline_pick_js(delivery_days))
    if not isinstance(picked, dict):
        return {"ok": False, "reason": "invalid_pick_result", "opened": opened}
    picked["opened"] = opened
    return picked


def kwork_offer_form_url(project_id: str) -> str:
    return f"https://kwork.ru/new_offer?project={project_id}"


def _fill_first(browser: BrowserClient, selectors: tuple[str, ...], value: str) -> bool:
    if not value:
        return False
    for selector in selectors:
        try:
            browser.fill(selector, value)
            return True
        except Exception:
            continue
    return False


def _read_offer_form(browser: BrowserClient) -> dict[str, Any]:
    data = browser.evaluate(READ_OFFER_FORM_JS)
    return data if isinstance(data, dict) else {}


def _resolve_order_title(
    browser: BrowserClient, project_id: str, fallback: str
) -> str:
    try:
        scraped = browser.evaluate(EXTRACT_ORDER_TITLE_ON_PAGE_JS)
        if isinstance(scraped, str) and len(scraped.strip()) >= 3:
            return scraped.strip()[:70]
    except Exception:
        pass
    try:
        view_url = f"https://kwork.ru/projects/{project_id}/view"
        browser.navigate(view_url)
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(2500)
        raw = browser.evaluate(PROJECT_EXTRACTOR_JS)
        title = str(raw.get("title") or "").strip() if isinstance(raw, dict) else ""
        if title:
            browser.navigate(kwork_offer_form_url(project_id))
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(3000)
            return title[:70]
    except Exception:
        pass
    return (fallback or "").strip()[:70]


def _autosave_wait(browser: BrowserClient, *, wait_ms: int = 8000) -> None:
    try:
        browser.evaluate(TRIGGER_OFFER_AUTOSAVE_JS)
    except Exception:
        pass
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(wait_ms)
    if hasattr(browser, "save_storage_state"):
        browser.save_storage_state()


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
        url = f"https://kwork.ru/projects/{project_id}/view"
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
        order_title: str = "",
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

        offer_url = kwork_offer_form_url(project_id)
        self.browser.navigate(offer_url)
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(4000)

        if not is_logged_in(self.browser):
            return SubmitResult(
                success=False,
                project_id=project_id,
                message="not_logged_in: нужен логин Kwork на VPS",
            )

        order_title = _resolve_order_title(
            self.browser, project_id, order_title
        )

        desc_ok = _fill_first(self.browser, DESC_SELECTORS, text)
        price_ok = _fill_first(self.browser, PRICE_SELECTORS, str(price))
        title_ok = False
        if order_title:
            title_ok = _fill_first(self.browser, TITLE_SELECTORS, order_title[:70])

        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(500)

        try:
            self.browser.evaluate(
                """
                () => {
                  const payCard = [...document.querySelectorAll('label, div, span, button')].find((el) =>
                    /целиком.*заказ выполнен/i.test((el.textContent || '').replace(/\\s+/g, ' '))
                  );
                  if (payCard) payCard.click();
                  return Boolean(payCard);
                }
                """
            )
        except Exception:
            pass

        fill_result = self.browser.evaluate(
            _build_offer_fill_js(text, price, order_title=order_title)
        )
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(800)

        deadline_result = _fill_deadline(self.browser, delivery_days)
        days_set = bool(isinstance(deadline_result, dict) and deadline_result.get("ok"))

        _autosave_wait(self.browser, wait_ms=8000)
        readback = _read_offer_form(self.browser)
        desc_len = int(readback.get("descLen") or 0)
        read_price = str(readback.get("price") or "").replace(" ", "")
        read_title = str(readback.get("title") or "").strip()
        min_desc = min(150, max(50, len(text.strip()) // 3))

        if not isinstance(fill_result, dict):
            fill_result = {}
        fill_result["daysSet"] = days_set
        fill_result["deadline"] = deadline_result
        fill_result["readback"] = readback
        fill_result["playwrightDesc"] = desc_ok
        fill_result["playwrightPrice"] = price_ok
        fill_result["playwrightTitle"] = title_ok

        if desc_len < min_desc or not read_price:
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_verify_failed: {fill_result}",
            )

        message = f"prepared: verified desc={desc_len} price={read_price} title={read_title!r}"
        if not days_set:
            message += f"; deadline_not_set: {deadline_result}"

        return SubmitResult(
            success=True,
            project_id=project_id,
            message=message,
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
