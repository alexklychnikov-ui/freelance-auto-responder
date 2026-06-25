from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from src.adapters.kwork_delivery import (
    KWORK_DELIVERY_DAY_OPTIONS,
    snap_delivery_days,
)
from src.adapters.kwork_pricing import clamp_price_to_budget, parse_form_price_bounds
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
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const root = document.querySelector('main.project-page, [data-project-id], .want-view, main') || document;
  const projectId = root.getAttribute?.('data-project-id')
    || (location.pathname.match(/\\/projects\\/(\\d+)/) || [])[1]
    || null;
  const titleEl =
    root.querySelector('.project-title, .wants-card__header-title, .want-card__header a, [data-project-title]') ||
    root.querySelector('h1');
  const descEl =
    root.querySelector('.project-description, .want-card__description-text, [data-description], .breakwords');
  const expandBtn = root.querySelector('.show-full-description, [data-expand-description], .kw-link-dashed');
  if (expandBtn && !expandBtn.dataset.clicked) {
    try { expandBtn.click(); expandBtn.dataset.clicked = '1'; } catch (e) {}
  }
  const tags = [...root.querySelectorAll('.tags .tag, .tag-list .tag, .kw-tag')]
    .map(el => norm(el.textContent))
    .filter(Boolean);

  const pageText = norm(document.body?.innerText || root.textContent || '');
  const budgetAfter = (labelRe) => {
    const m = pageText.match(new RegExp(labelRe + '[^\\d]{0,30}([\\d\\s]+)\\s*₽', 'i'));
    return m ? norm(m[1]) + ' ₽' : null;
  };

  const offersRaw = root.querySelector('.offers-count, [data-offers]');
  let offers = null;
  if (offersRaw) {
    const n = parseInt(offersRaw.getAttribute('data-offers') || offersRaw.textContent.replace(/\\D/g, ''), 10);
    offers = Number.isFinite(n) ? n : null;
  }
  if (offers == null) {
    const m = pageText.match(/предложени[яй][^\\d]{0,12}(\\d+)/i);
    if (m) offers = parseInt(m[1], 10);
  }

  const buyerEl =
    root.querySelector('.buyer-link, [data-buyer], .want-card__user-name a, .user-name a');
  let buyer = buyerEl ? norm(buyerEl.textContent) : null;
  if (!buyer) {
    const buyerBlock = [...root.querySelectorAll('div, section, aside')].find((el) =>
      /покупатель/i.test(el.textContent || '')
    );
    const link = buyerBlock?.querySelector('a[href*="/user/"]');
    if (link) buyer = norm(link.textContent);
  }

  let buyerHire =
    root.querySelector('.buyer-hire-rate, [data-buyer-hire-rate]')?.textContent?.trim() || null;
  if (!buyerHire) {
    const m = pageText.match(/(\\d{1,3})\\s*%\\s*(?:найм|выбор)/i)
      || pageText.match(/найм[а-яё\\s]{0,20}(\\d{1,3})\\s*%/i);
    if (m) buyerHire = m[1] + '%';
  }

  let timeLeft =
    root.querySelector('.time-left, [data-time-left], [class*="timer"], [class*="countdown"]')
      ?.textContent?.trim() || null;
  if (!timeLeft) {
    const m = pageText.match(/(\\d+\\s*(?:д\\.?|дн|дней|ч\\.?|мин\\.?)[\\s\\d\\.чмин]{0,25})/i);
    if (m) timeLeft = norm(m[1]);
  }

  return {
    project_id: projectId,
    url: location.href,
    title: norm(titleEl?.textContent || ''),
    full_description: norm(descEl?.textContent || descEl?.getAttribute('data-description') || ''),
    desired_budget:
      norm(root.querySelector('.desired-budget, [data-desired-budget]')?.textContent || '')
      || budgetAfter('желаем(?:ый|ого)?\\s+бюджет')
      || null,
    max_budget:
      norm(root.querySelector('.max-budget, [data-max-budget]')?.textContent || '')
      || budgetAfter('допустим(?:ый|ого)?')
      || budgetAfter('до')
      || null,
    offers_count: offers,
    buyer,
    buyer_hire_rate: buyerHire ? norm(buyerHire) : null,
    time_left: timeLeft ? norm(timeLeft) : null,
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
)
TITLE_SELECTORS = (
    'input[placeholder*="Название заказа"]',
    'input[placeholder*="название заказа"]',
    'input[name="name"]',
    'input[name="order_name"]',
    '.wants-offer__title input',
    '.wants-offer input[type="text"]',
)

FIND_TITLE_INPUT_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const byPlaceholder = [...document.querySelectorAll('input[type="text"], input:not([type])')].find((inp) =>
    /название заказа/i.test(inp.getAttribute('placeholder') || '')
  );
  if (byPlaceholder) return byPlaceholder;

  const label = [...document.querySelectorAll('label, .form-field__name, .field-name, span, div, p')].find(
    (el) => /^название заказа$/i.test(norm(el.textContent))
  );
  if (label) {
    const root =
      label.closest('.form-field, .form-item, .field, .offer-form__row, .wants-offer__field, div')
      || label.parentElement;
    const inp = root?.querySelector('input[type="text"], input:not([type="hidden"])');
    if (inp) return inp;
    let sib = label.nextElementSibling;
    while (sib) {
      const found = sib.querySelector?.('input[type="text"], input:not([type="hidden"])') || null;
      if (found) return found;
      sib = sib.nextElementSibling;
    }
  }

  const counter = [...document.querySelectorAll('span, div, p')].find((el) =>
    /из\\s*70\\s*символ/i.test(el.textContent || '')
  );
  if (counter) {
    const root = counter.closest('.form-field, .field, .wants-offer__field, div') || counter.parentElement;
    const inp = root?.querySelector('input[type="text"], input:not([type="hidden"])');
    if (inp) return inp;
  }

  return null;
}
"""

FIND_PRICE_INPUT_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const label = [...document.querySelectorAll('label, .form-field__name, .field-name, span, div, p')].find(
    (el) => /^стоимость$/i.test(norm(el.textContent))
  );
  if (label) {
    const root =
      label.closest('.form-field, .form-item, .field, .offer-form__row, .wants-offer__field, div')
      || label.parentElement;
    const inp =
      root?.querySelector('#offer-custom-price, input[name="price"], input[type="number"], input[type="tel"]')
      || null;
    if (inp) return inp;
  }
  return (
    document.querySelector('#offer-custom-price') ||
    document.querySelector('input[name="price"]') ||
    document.querySelector('.wants-offer__price input') ||
    document.querySelector('[data-field="price"] input') ||
    null
  );
}
"""

EXTRACT_FORM_BOUNDS_JS = """
() => {
  const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
  const m = text.match(/от\\s*([\\d\\s]+)\\s*руб\\W*\\s*до\\s*([\\d\\s]+)\\s*руб/i);
  if (!m) return null;
  const parse = (s) => parseInt(s.replace(/\\s/g, ''), 10);
  return { min: parse(m[1]), max: parse(m[2]) };
}
"""

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
    const h1 = document.querySelector('h1');
    if (h1) {
      const t = norm(h1.textContent);
      if (t.length >= 3 && t.length <= 70 && !/отклик|предлож|услуг/i.test(t)) return t;
    }
    return '';
  };

  return fromDetails() || fromSelectors();
}
"""

READ_OFFER_FORM_JS = f"""
() => {{
  const desc =
    document.querySelector('textarea[name="description"]') ||
    document.querySelector('textarea.v-textarea') ||
    document.querySelector('.wants-offer__description textarea') ||
    document.querySelector('textarea');
  const priceInput = (() => {{
    const findPrice = {FIND_PRICE_INPUT_JS};
    return findPrice();
  }})();
  const titleInput = (() => {{
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const byPh = [...document.querySelectorAll('input[type="text"], input:not([type])')].find((inp) =>
      /название заказа/i.test(inp.getAttribute('placeholder') || '')
    );
    if (byPh) return byPh;
    const label = [...document.querySelectorAll('label, span, div, p')].find(
      (el) => /^название заказа$/i.test(norm(el.textContent))
    );
    if (label) {{
      const root = label.closest('.form-field, .field, div') || label.parentElement;
      return root?.querySelector('input[type="text"], input:not([type="hidden"])') || null;
    }}
  const counter = [...document.querySelectorAll('span, div')].find((el) =>
      /из\\s*70\\s*символ/i.test(el.textContent || '')
    );
    if (counter) {{
      const root = counter.closest('.form-field, .field, div') || counter.parentElement;
      return root?.querySelector('input[type="text"], input:not([type="hidden"])') || null;
    }}
    return null;
  }})();
  return {{
    url: location.href,
    descLen: (desc?.value || '').length,
    descPreview: (desc?.value || '').slice(0, 100),
    price: (priceInput?.value || '').replace(/\\s/g, ''),
    title: titleInput?.value || '',
  }};
}}
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
      const findPrice = {FIND_PRICE_INPUT_JS};
      priceInput = findPrice();
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

  if (!titleInput) {{
    const findTitle = {FIND_TITLE_INPUT_JS};
    titleInput = findTitle();
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
    snapped = snap_delivery_days(delivery_days)
    allowed = list(KWORK_DELIVERY_DAY_OPTIONS)
    return f"""
(() => {{
  const targetDays = {snapped};
  const allowed = {json.dumps(allowed)};
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

  function extractDays(text) {{
    const m = text.match(/(\\d{{1,3}})/);
    if (!m) return null;
    let n = parseInt(m[1], 10);
    if (/нед/i.test(text)) n *= 7;
    return n;
  }}

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
      const days = extractDays(text);
      if (days == null || !allowed.includes(days)) continue;
      out.push({{ node, text, days }});
    }}
    return out;
  }}

  function pickFromSelect() {{
    for (const sel of document.querySelectorAll('select')) {{
      for (const opt of sel.options) {{
        const text = norm(opt.textContent);
        const days = extractDays(text);
        if (days === targetDays) {{
          sel.value = opt.value;
          sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
          sel.dispatchEvent(new Event('input', {{ bubbles: true }}));
          return {{ ok: true, picked: text, method: 'native_select', targetDays }};
        }}
      }}
    }}
    return null;
  }}

  const native = pickFromSelect();
  if (native) return native;

  const variants = dayVariants(targetDays);
  const options = collectOptions();
  for (const {{ node, text, days }} of options) {{
    if (days === targetDays && variants.some((v) => text.toLowerCase().includes(v.toLowerCase()))) {{
      node.click();
      return {{ ok: true, picked: text, method: 'exact', targetDays, optionsCount: options.length }};
    }}
  }}
  for (const {{ node, text, days }} of options) {{
    if (days === targetDays) {{
      node.click();
      return {{ ok: true, picked: text, method: 'allowed', targetDays, optionsCount: options.length }};
    }}
  }}

  return {{ ok: false, reason: 'no_allowed_match', targetDays, optionsCount: options.length }};
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
    fb = (fallback or "").strip()[:70]
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
    return fb


def _fill_price(browser: BrowserClient, value: str) -> bool:
    if not value:
        return False
    if _fill_first(browser, PRICE_SELECTORS, value):
        return True
    try:
        ok = browser.evaluate(
            f"""
            () => {{
              const val = {json.dumps(str(value))};
              const findPrice = {FIND_PRICE_INPUT_JS};
              const inp = findPrice();
              if (!inp) return false;
              const proto = HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
              if (setter) setter.call(inp, val);
              else inp.value = val;
              inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
              inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
              inp.focus();
              inp.blur();
              return true;
            }}
            """
        )
        return bool(ok)
    except Exception:
        return False


def _read_form_price_bounds(browser: BrowserClient) -> tuple[int | None, int | None]:
    try:
        raw = browser.evaluate(EXTRACT_FORM_BOUNDS_JS)
        if isinstance(raw, dict) and raw.get("min") and raw.get("max"):
            return int(raw["min"]), int(raw["max"])
    except Exception:
        pass
    try:
        snap = browser.snapshot()
        return parse_form_price_bounds(snap)
    except Exception:
        return None, None


def _normalize_offer_price(
    price: str,
    project: ProjectFull | None,
    *,
    form_min: int | None = None,
    form_max: int | None = None,
) -> str:
    if project is None:
        project = ProjectFull(
            platform="kwork",
            source_key="kwork",
            project_id="0",
            url="",
            title="",
            full_description="",
        )
    digits = re.sub(r"\D", "", str(price) or "")
    value = int(digits) if digits else 0
    if value < 500:
        from src.adapters.kwork_pricing import suggest_offer_price

        value = int(suggest_offer_price(project, form_min=form_min, form_max=form_max))
    value = clamp_price_to_budget(
        value, project, form_min=form_min, form_max=form_max
    )
    return str(value)


def _fill_order_title(browser: BrowserClient, title: str) -> bool:
    value = (title or "").strip()[:70]
    if not value:
        return False
    if _fill_first(browser, TITLE_SELECTORS, value):
        return True
    try:
        ok = browser.evaluate(
            f"""
            () => {{
              const value = {json.dumps(value)};
              const findTitle = {FIND_TITLE_INPUT_JS};
              const inp = findTitle();
              if (!inp) return false;
              const proto = HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
              if (setter) setter.call(inp, value);
              else inp.value = value;
              inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
              inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
              inp.focus();
              inp.blur();
              return true;
            }}
            """
        )
        return bool(ok)
    except Exception:
        return False


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

    title = (
        _first_group(r'class="project-title"[^>]*>([^<]+)', root)
        or _first_group(r'class="wants-card__header-title[^"]*"[^>]*>\s*<a[^>]*>([^<]+)', root)
        or _first_group(r"<h1[^>]*>([^<]+)", root)
        or ""
    )
    description = _first_group(r'class="project-description"[^>]*>([^<]+)', root)
    if not description:
        description = _first_group(r'data-description="([^"]+)"', root) or ""
    if not description:
        description = _first_group(
            r'class="want-card__description-text"[^>]*>([^<]+)', root
        ) or ""

    tags = re.findall(r'class="tag"[^>]*>([^<]+)', root, flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", " ", root)
    plain = re.sub(r"\s+", " ", plain)

    offers_raw = _first_group(r'data-offers="(\d+)"', root)
    if not offers_raw:
        offers_raw = _first_group(r'class="offers-count"[^>]*>(\d+)', root)
    if not offers_raw:
        m = re.search(r"предложени[яй][^\d]{0,12}(\d+)", plain, re.I)
        offers_raw = m.group(1) if m else None

    desired = _optional_text(r'class="desired-budget"[^>]*>([^<]+)', root)
    if not desired:
        m = re.search(r"желаем(?:ый|ого)?\s+бюджет[^₽\d]{0,30}([\d\s]+)\s*₽", plain, re.I)
        desired = f"до {m.group(1).strip()} ₽" if m else None

    max_b = _optional_text(r'class="max-budget"[^>]*>([^<]+)', root)
    if not max_b:
        m = re.search(r"допустим(?:ый|ого)?[^₽\d]{0,30}([\d\s]+)\s*₽", plain, re.I)
        max_b = f"до {m.group(1).strip()} ₽" if m else None

    buyer = _optional_text(r'class="buyer-link"[^>]*>([^<]+)', root)
    if not buyer:
        buyer = _optional_text(r'href="/user/[^"]+"[^>]*>([^<]+)', root)

    hire = _optional_text(r'class="buyer-hire-rate"[^>]*>([^<]+)', root)
    if not hire:
        m = re.search(r"(\d{1,3})\s*%\s*(?:найм|выбор)", plain, re.I)
        hire = f"{m.group(1)}%" if m else None

    time_left = _optional_text(r'class="time-left"[^>]*>([^<]+)', root)
    if not time_left:
        m = re.search(r"(\d+\s*(?:д\.?|дн|дней|ч\.?|мин\.?)[\s\d\.чмин]{0,25})", plain, re.I)
        time_left = m.group(1).strip() if m else None

    return {
        "project_id": pid,
        "url": f"https://kwork.ru/projects/{pid}/view" if pid else "",
        "title": title.strip(),
        "full_description": description.strip(),
        "desired_budget": desired,
        "max_budget": max_b,
        "offers_count": int(offers_raw) if offers_raw else None,
        "buyer": buyer,
        "buyer_hire_rate": hire,
        "time_left": time_left,
        "tags": [t.strip() for t in tags if t.strip()],
    }


def _merge_project_raw(
    primary: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(fallback)
    merged.update({k: v for k, v in primary.items() if v not in (None, "", [])})
    for key in (
        "desired_budget",
        "max_budget",
        "offers_count",
        "buyer",
        "buyer_hire_rate",
        "time_left",
        "title",
        "full_description",
    ):
        if not merged.get(key) and fallback.get(key):
            merged[key] = fallback[key]
    return merged


def merge_preview_into_full(
    full: ProjectFull, preview: ProjectPreview | None
) -> ProjectFull:
    if preview is None:
        return full
    data = full.model_dump()
    if preview.title and not data.get("title"):
        data["title"] = preview.title
    if preview.responses_count is not None and not data.get("offers_count"):
        data["offers_count"] = preview.responses_count
    if preview.budget_text:
        bt = preview.budget_text.strip()
        if not data.get("desired_budget"):
            data["desired_budget"] = bt if "₽" in bt else f"{bt} ₽"
    return ProjectFull.model_validate(data)


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
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(2000)
        raw = self.browser.evaluate(PROJECT_EXTRACTOR_JS)
        if not isinstance(raw, dict):
            raw = {}
        snapshot = self.browser.snapshot()
        parsed = parse_project_from_html(snapshot, project_id=project_id)
        raw = _merge_project_raw(raw, parsed)

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
        project: ProjectFull | None = None,
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

        delivery_days = snap_delivery_days(delivery_days)

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

        form_min, form_max = _read_form_price_bounds(self.browser)
        price = _normalize_offer_price(
            price,
            project,
            form_min=form_min,
            form_max=form_max,
        )

        desc_ok = _fill_first(self.browser, DESC_SELECTORS, text)
        price_ok = _fill_price(self.browser, str(price))
        title_ok = _fill_order_title(self.browser, order_title)

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
        if order_title:
            title_ok = _fill_order_title(self.browser, order_title) or title_ok
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(800)

        deadline_result = _fill_deadline(self.browser, delivery_days)
        days_set = bool(isinstance(deadline_result, dict) and deadline_result.get("ok"))
        price_ok = _fill_price(self.browser, str(price)) or price_ok
        if order_title:
            title_ok = _fill_order_title(self.browser, order_title) or title_ok

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

        read_price_int = int(re.sub(r"\D", "", read_price) or 0)
        if form_min and read_price_int < form_min:
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_price_below_min: {read_price_int} < {form_min} {fill_result}",
            )
        if order_title and not read_title:
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_title_empty: {fill_result}",
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
