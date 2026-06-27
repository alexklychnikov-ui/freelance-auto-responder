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
from src.adapters.kwork_stages import plan_offer_stages
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
    'textarea[name="name"]',
    'input[placeholder*="Название заказа"]',
    'input[placeholder*="название заказа"]',
    'input[name="name"]',
    'input[name="order_name"]',
    '.wants-offer__title input',
    '.wants-offer input[type="text"]',
)

TRUMBOWYG_SET_JS = """
(name, plainText) => {
  const raw = String(plainText || '');
  const limit = (name === 'name' || String(name).startsWith('stageTitle')) ? 70 : 20000;
  const text = raw.slice(0, limit);
  const ta = document.querySelector(`textarea[name="${name}"]`);
  if (!ta) return false;
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const singleLine = (name === 'name' || String(name).startsWith('stageTitle'));
  let html;
  let lines = [];
  if (singleLine) {
    const flat = text.replace(/\\s+/g, ' ').trim();
    html = flat ? `<p>${esc(flat)}</p>` : '<div><br></div>';
  } else {
    lines = text.split(/\\n+/).map((l) => l.trim()).filter(Boolean);
    html = lines.length
      ? lines.map((l) => `<p>${esc(l)}</p>`).join('')
      : '<div><br></div>';
  }
  const $ = window.jQuery || window.$;
  if ($ && typeof $(ta).trumbowyg === 'function') {
    $(ta).trumbowyg('html', html);
    $(ta).trigger('tbwchange');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.dispatchEvent(new Event('change', { bubbles: true }));
    if (String(name).startsWith('stageTitle')) {
      const hidden = ta.closest('.stages__stage-name')?.querySelector('.stages__stage-text');
      const flat = singleLine ? text.replace(/\\s+/g, ' ').trim() : lines.join(' ');
      if (hidden) hidden.textContent = flat;
    }
    return true;
  }
  const box = ta.closest('.trumbowyg-box') || ta.parentElement;
  const editor = box?.querySelector('.trumbowyg-editor');
  if (editor) {
    editor.innerHTML = html;
    editor.classList.remove('is-placeholder-mobile', 'force-placeholder');
    editor.focus();
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    editor.dispatchEvent(new Event('blur', { bubbles: true }));
  }
  ta.value = html;
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}
"""

FIND_TITLE_INPUT_JS = """
() => {
  const ta = document.querySelector('textarea[name="name"]');
  if (ta) return ta;
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const byPlaceholder = [...document.querySelectorAll('input[type="text"], textarea')].find((inp) =>
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
    const inp = root?.querySelector('textarea[name="name"], input[type="text"]:not([type="hidden"])');
    if (inp && inp.id !== 'offer-custom-price' && inp.type !== 'tel') return inp;
  }

  const counter = [...document.querySelectorAll('span, div, p')].find((el) =>
    /из\\s*70\\s*символ/i.test(el.textContent || '')
  );
  if (counter) {
    const root = counter.closest('.form-field, .field, .wants-offer__field, div') || counter.parentElement;
    const inp = root?.querySelector('textarea[name="name"], input[type="text"]:not([type="hidden"])');
    if (inp && inp.id !== 'offer-custom-price' && inp.type !== 'tel') return inp;
  }

  return null;
}
"""

FIND_DEADLINE_INPUT_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const durationSelected = document.querySelector('.duration-select__selected-option');
  if (durationSelected) return durationSelected;
  const durationSearch = document.querySelector('.duration-select input.vs__search');
  if (durationSearch) return durationSearch;
  const byPh = document.querySelector('input.vs__search[placeholder="Срок выполнения"]');
  if (byPh) return byPh;
  const label = [...document.querySelectorAll('label, span, div, p')].find(
    (el) => /^Срок выполнения$/i.test(norm(el.textContent))
  );
  if (label) {
    const root =
      label.closest('.duration-select, .modal-individual-message__column, .form-field, .field, .form-item, div')
      || label.parentElement;
    const inp = root?.querySelector('input.duration-select__selected-option, input.vs__search');
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
  const titleTa = document.querySelector('textarea[name="name"]');
  const titlePlain = titleTa
    ? (titleTa.value || '').replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim()
    : '';
  return {{
    url: location.href,
    descLen: (desc?.value || '').length,
    descPreview: (desc?.value || '').slice(0, 100),
    price: (priceInput?.value || '').replace(/\\s/g, ''),
    title: titlePlain,
    deadline: (() => {{
      const selected = document.querySelector('.duration-select__selected-option');
      if (selected?.value) return selected.value.trim();
      const findDeadline = {FIND_DEADLINE_INPUT_JS};
      const el = findDeadline();
      return (el?.value || '').trim();
    }})(),
    deadlineLabel: (document.querySelector('.duration-select .vs__selected, .duration-select__selected-option')?.textContent
      || document.querySelector('.duration-select .vs__selected-options')?.textContent
      || document.querySelector('.duration-select .vs__selected-options, .duration-select .vs__selected')?.textContent
      || '')
      .replace(/\\s+/g, ' ').trim(),
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

  const payCard = [...document.querySelectorAll('.offer-payment-type__item')].find((el) =>
    /по мере выполнения задач/i.test((el.textContent || '').replace(/\\s+/g, ' '))
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
    const duration = document.querySelector('.duration-select');
    if (duration) return duration;
    const label = [...document.querySelectorAll('label, .form-field__name, .field-name, span, p, div')]
      .find((el) => /^срок выполнения$/i.test(norm(el.textContent)));
    if (label) {
      const root = label.closest(
        '.form-field, .form-item, .field, .offer-form__row, .wants-offer__field, .offer-field, div'
      );
      const ms = root?.querySelector('.multiselect, .duration-select');
      if (ms) return ms;
    }
    return [...document.querySelectorAll('.multiselect, .duration-select')].find((ms) =>
      /срок выполнения/i.test(norm(ms.textContent))
    ) || null;
  }

  const ms = findDeadlineField();
  if (!ms) {
    return { ok: false, reason: 'multiselect_not_found' };
  }

  const openers = [
    ms.querySelector('.vs__dropdown-toggle'),
    ms.querySelector('.multiselect__select'),
    ms.querySelector('.multiselect__tags'),
    ms.querySelector('.multiselect-single'),
    ms.querySelector('input.duration-select__selected-option'),
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


def _normalize_editor_plaintext(text: str, *, single_line: bool = False) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    if single_line:
        return re.sub(r"\s+", " ", body)
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in re.split(r"\n+", body) if ln.strip()]
    return "\n\n".join(lines)


def _set_trumbowyg(browser: BrowserClient, field_name: str, text: str) -> bool:
    single_line = field_name == "name" or field_name.startswith("stageTitle")
    value = _normalize_editor_plaintext(text, single_line=single_line)
    if not value and field_name != "name":
        return False
    try:
        return bool(
            browser.evaluate(
                f"({TRUMBOWYG_SET_JS})({json.dumps(field_name)}, {json.dumps(value)})"
            )
        )
    except Exception:
        return False


def _fill_description(browser: BrowserClient, text: str) -> bool:
    if _set_trumbowyg(browser, "description", text):
        return True
    return _fill_first(browser, DESC_SELECTORS, text)


def _fill_price(browser: BrowserClient, value: str) -> bool:
    if not value:
        return False
    if hasattr(browser, "fill_sequential"):
        try:
            browser.fill_sequential("#offer-custom-price", str(value))
            return True
        except Exception:
            pass
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


def _wait_duration_ready(browser: BrowserClient, *, attempts: int = 24) -> bool:
    for _ in range(attempts):
        try:
            ready = browser.evaluate(
                """() => {
                  const root = document.querySelector('.duration-select');
                  if (!root) return false;
                  const txt = (root.textContent || '').replace(/\\s+/g, ' ');
                  return !/loading/i.test(txt);
                }"""
            )
            if ready:
                return True
        except Exception:
            pass
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(500)
    return False


def _read_description_len(browser: BrowserClient) -> int:
    try:
        raw = browser.evaluate(
            """
            () => {
              const ta = document.querySelector('textarea[name="description"]');
              const ed = ta?.closest('.trumbowyg-box')?.querySelector('.trumbowyg-editor');
              const plain = (ed?.innerText || ta?.value || '')
                .replace(/<[^>]+>/g, ' ')
                .replace(/\\s+/g, ' ')
                .trim();
              return plain.length;
            }
            """
        )
        return int(raw or 0)
    except Exception:
        return 0


def _fill_deadline_duration_select(
    browser: BrowserClient, delivery_days: int
) -> dict[str, Any]:
    days = snap_delivery_days(delivery_days)
    _wait_duration_ready(browser)

    try:
        vue_set = browser.evaluate(
            f"""
            () => {{
              const root = document.querySelector('.duration-select');
              const vm = root?.__vue__;
              const offer = document.querySelector('.offer-custom')?.__vue__;
              const targets = [vm, offer].filter(Boolean);
              for (const t of targets) {{
                if ('duration' in t) t.duration = {days};
                if ('durationDays' in t) t.durationDays = {days};
                if ('durationLocal' in t) t.durationLocal = {days};
                if (typeof t.onChangeDuration === 'function') t.onChangeDuration({days});
                if (typeof t.setDuration === 'function') t.setDuration({days});
              }}
              const inp = document.querySelector('.duration-select__selected-option');
              if (inp) {{
                inp.focus();
                inp.value = String({days});
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                inp.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }}));
                inp.blur();
              }}
              const picked = (document.querySelector('.duration-select__selected-option')?.value || '').trim();
              if (picked && /\\d/.test(picked)) {{
                return {{ ok: true, method: 'duration_vue', picked, targetDays: {days} }};
              }}
              return {{ ok: false, reason: 'duration_vue_empty', targetDays: {days} }};
            }}
            """
        )
        if isinstance(vue_set, dict) and vue_set.get("ok"):
            return vue_set
    except Exception as exc:
        vue_set = {"ok": False, "reason": f"duration_vue_error:{exc}", "targetDays": days}

    if hasattr(browser, "_ensure_page"):
        page = browser._ensure_page()
        try:
            root = page.locator(".duration-select").first
            root.scroll_into_view_if_needed(timeout=8000)
            root.locator(".vs__dropdown-toggle").click(force=True, timeout=5000)
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(800)
            for sel in (
                "input.duration-select__selected-option",
                "input.vs__search.js-only-integer",
                "input.vs__search",
            ):
                loc = root.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.click(force=True, timeout=3000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                loc.press_sequentially(str(days), delay=40)
                if hasattr(browser, "wait_ms"):
                    browser.wait_ms(700)
                opt = page.locator(
                    ".duration-select__dropdown .vs__dropdown-option, .vs__dropdown-option"
                ).filter(has_text=str(days)).first
                if opt.count() > 0:
                    opt.click(force=True)
                else:
                    page.keyboard.press("Enter")
                page.keyboard.press("Tab")
                break
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(500)
            read = browser.evaluate(
                """() => (document.querySelector('.duration-select__selected-option')?.value || '').trim()"""
            )
            if str(read).strip() and re.search(r"\d", str(read)):
                return {
                    "ok": True,
                    "method": "duration_playwright",
                    "picked": read,
                    "targetDays": days,
                    "vue_attempt": vue_set,
                }
        except Exception as exc:
            return {
                "ok": False,
                "reason": f"duration_pw:{exc}",
                "targetDays": days,
                "vue_attempt": vue_set,
            }

    if isinstance(vue_set, dict):
        return vue_set
    return {"ok": False, "reason": "duration_select_failed", "targetDays": days}


def _fill_deadline_vueselect(browser: BrowserClient, delivery_days: int) -> dict[str, Any]:
    days = snap_delivery_days(delivery_days)
    if hasattr(browser, "fill_sequential") and hasattr(browser, "_ensure_page"):
        page = browser._ensure_page()
        try:
            found = browser.evaluate(
                f"""
                () => {{
                  const findDeadline = {FIND_DEADLINE_INPUT_JS};
                  const el = findDeadline();
                  if (!el) return null;
                  el.setAttribute('data-fr-deadline', '1');
                  return true;
                }}
                """
            )
            if not found:
                return {"ok": False, "reason": "deadline_input_not_found", "targetDays": days}
            selector = "input.vs__search[data-fr-deadline='1']"
            loc = page.locator(selector).first
            loc.click(force=True)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            loc.press_sequentially(str(days), delay=60)
            page.wait_for_timeout(600)
            option = page.locator(".vs__dropdown-option").filter(has_text=str(days)).first
            if option.count() > 0:
                option.click(force=True)
            else:
                page.keyboard.press("Enter")
            page.keyboard.press("Tab")
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(800)
            read = browser.evaluate(
                f"""
                () => {{
                  const findDeadline = {FIND_DEADLINE_INPUT_JS};
                  const el = findDeadline();
                  return (el?.value || '').trim();
                }}
                """
            )
            if str(read).strip():
                return {
                    "ok": True,
                    "method": "vueselect_playwright",
                    "targetDays": days,
                    "picked": read,
                }
        except Exception as exc:
            return {"ok": False, "reason": f"vueselect_error:{exc}", "targetDays": days}
    try:
        picked = browser.evaluate(
            f"""
            () => {{
              const days = {days};
              const el = document.querySelector('input.vs__search[placeholder="Срок выполнения"]');
              if (!el) return {{ ok: false, reason: 'deadline_input_not_found' }};
              el.focus();
              el.click();
              el.value = String(days);
              el.dispatchEvent(new Event('input', {{ bubbles: true }}));
              el.dispatchEvent(new Event('change', {{ bubbles: true }}));
              el.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }}));
              el.blur();
              return {{ ok: true, method: 'vueselect_js', picked: el.value, targetDays: days }};
            }}
            """
        )
        if isinstance(picked, dict):
            return picked
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "targetDays": days}
    return {"ok": False, "reason": "deadline_fill_failed", "targetDays": days}


def _fill_deadline(browser: BrowserClient, delivery_days: int) -> dict[str, Any]:
    duration = _fill_deadline_duration_select(browser, delivery_days)
    if duration.get("ok"):
        return duration
    vue = _fill_deadline_vueselect(browser, delivery_days)
    if vue.get("ok"):
        return vue
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
        return {"ok": False, "reason": "invalid_pick_result", "opened": opened, "vue": vue}
    picked["opened"] = opened
    picked["vue_attempt"] = vue
    return picked


def kwork_offer_form_url(project_id: str) -> str:
    return f"https://kwork.ru/new_offer?project={project_id}"


def _read_offer_form_state(browser: BrowserClient) -> dict[str, Any]:
    try:
        raw = browser.evaluate(
            """
            () => {
              const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
              const path = location.pathname || '';
              return {
                url: location.href,
                hasForm: Boolean(
                  document.querySelector(
                    '.custom-kwork-offer__wrapper, .offer-custom, textarea[name="description"]'
                  )
                ),
                listingRedirect: path === '/projects' || path.endsWith('/projects'),
                projectClosed: /оставить отзыв|опубликовать похожий проект/i.test(text),
              };
            }
            """
        )
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _project_on_my_offers(browser: BrowserClient, project_id: str) -> bool:
    try:
        browser.navigate("https://kwork.ru/offers")
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(3000)
        return bool(
            browser.evaluate(
                f"""
                () => [...document.querySelectorAll('a[href*="/projects/{project_id}"]')].length > 0
                """
            )
        )
    except Exception:
        return False


def _check_offer_form_available(
    browser: BrowserClient, project_id: str
) -> SubmitResult | None:
    state = _read_offer_form_state(browser)
    if state.get("hasForm"):
        return None

    on_offers = _project_on_my_offers(browser, project_id)
    if on_offers or state.get("projectClosed"):
        return SubmitResult(
            success=False,
            project_id=project_id,
            message=(
                "offer_already_submitted: отклик уже есть на Kwork "
                f"(project_id={project_id}, url={state.get('url')})"
            ),
        )

    return SubmitResult(
        success=False,
        project_id=project_id,
        message=f"offer_form_unavailable: {state}",
    )


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


def _read_order_title(browser: BrowserClient) -> str:
    try:
        raw = browser.evaluate(
            """
            () => {
              const ta = document.querySelector('textarea[name="name"]');
              const ed = ta?.closest('.trumbowyg-box')?.querySelector('.trumbowyg-editor');
              const plain = (ed?.innerText || ed?.textContent || ta?.value || '')
                .replace(/<[^>]+>/g, ' ')
                .replace(/\\s+/g, ' ')
                .trim();
              return plain;
            }
            """
        )
        return str(raw or "").strip()
    except Exception:
        return ""


def _stages_section_visible(browser: BrowserClient, *, min_rows: int = 2) -> bool:
    try:
        count = browser.evaluate(
            f"""
            () => {{
              const prices = [...document.querySelectorAll('.stages__stage-price-input')]
                .filter((i) => i.offsetParent);
              const rows = [...document.querySelectorAll('.stages__list .stages__stage')]
                .filter((r) => r.offsetParent);
              const titles = [...document.querySelectorAll('textarea[name^="stageTitle-"]')]
                .filter((t) => t.offsetParent);
              return Math.max(prices.length, rows.length, titles.length);
            }}
            """
        )
        return int(count or 0) >= min_rows
    except Exception:
        return False


def _wait_stages_ready(
    browser: BrowserClient, *, min_rows: int = 2, attempts: int = 16
) -> bool:
    for _ in range(attempts):
        if _stages_section_visible(browser, min_rows=min_rows):
            return True
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(500)
    return False


def _payment_items_count(browser: BrowserClient) -> int:
    try:
        count = browser.evaluate(
            """
            () => document.querySelectorAll('.offer-payment-type__item').length
            """
        )
        return int(count or 0)
    except Exception:
        return 0


def _wait_payment_block_ready(
    browser: BrowserClient, *, attempts: int = 24
) -> bool:
    for _ in range(attempts):
        if _payment_items_count(browser) >= 2:
            return True
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(500)
    return False


def _prime_payment_ui(browser: BrowserClient, price: str) -> bool:
    """Kwork renders payment cards only after a price is entered."""
    if _payment_items_count(browser) >= 2:
        return True
    if not _fill_price(browser, str(price)):
        return False
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(800)
    return _wait_payment_block_ready(browser, attempts=20)


def _is_milestone_card_selected(browser: BrowserClient) -> bool:
    try:
        return bool(
            browser.evaluate(
                """
                () => {
                  const items = [...document.querySelectorAll('.offer-payment-type__item')];
                  const milestone = items.find((el) =>
                    /по мере выполнения задач/i.test((el.textContent || '').replace(/\\s+/g, ' '))
                  );
                  if (!milestone) return false;
                  if (milestone.classList.contains('active')
                    || milestone.classList.contains('selected')) {
                    return true;
                  }
                  const input = milestone.querySelector('input[type="radio"], input[type="checkbox"]');
                  return Boolean(input?.checked);
                }
                """
            )
        )
    except Exception:
        return False


def _read_payment_diag(browser: BrowserClient) -> dict[str, Any]:
    try:
        raw = browser.evaluate(
            """
            () => {
              const items = [...document.querySelectorAll('.offer-payment-type__item')];
              const milestone = items.find((el) =>
                /по мере выполнения задач/i.test((el.textContent || '').replace(/\\s+/g, ' '))
              );
              const prices = [...document.querySelectorAll('.stages__stage-price-input')]
                .filter((i) => i.offsetParent);
              return {
                paymentItems: items.length,
                hasMilestoneCard: Boolean(milestone),
                milestoneActive: Boolean(
                  milestone?.classList.contains('active')
                  || milestone?.classList.contains('selected')
                ),
                stagePriceInputs: prices.length,
                url: location.href,
              };
            }
            """
        )
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _click_milestone_payment_card(browser: BrowserClient) -> bool:
    clicked = False
    try:
        if hasattr(browser, "_ensure_page"):
            page = browser._ensure_page()
            item = page.locator(".offer-payment-type__item").filter(
                has_text=re.compile(r"По мере выполнения задач", re.I)
            ).first
            item.wait_for(state="visible", timeout=12000)
            item.scroll_into_view_if_needed(timeout=8000)
            item.click(force=True, timeout=5000)
            clicked = True
            radio = item.locator('input[type="radio"]').first
            if radio.count() > 0:
                radio.click(force=True)
    except Exception:
        pass
    try:
        clicked = bool(
            browser.evaluate(
                """
                () => {
                  const item = [...document.querySelectorAll('.offer-payment-type__item')].find((el) =>
                    /по мере выполнения задач/i.test((el.textContent || '').replace(/\\s+/g, ' '))
                  );
                  if (!item) return false;
                  item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                  item.click();
                  const input = item.querySelector('input[type="radio"], input[type="checkbox"]');
                  if (input) {
                    input.checked = true;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.click();
                  }
                  const offerVm = document.querySelector('.offer-custom')?.__vue__;
                  const payStages = offerVm?.offerPaymentStages || 'stages';
                  if (offerVm && typeof offerVm.updatePaymentType === 'function') {
                    try { offerVm.updatePaymentType(payStages); } catch (e) {}
                  }
                  return item.classList.contains('active')
                    || item.classList.contains('selected')
                    || Boolean(input?.checked);
                }
                """
            )
        ) or clicked
    except Exception:
        pass
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(1200)
    return clicked or _is_milestone_card_selected(browser)


def _is_milestone_payment_selected(browser: BrowserClient) -> bool:
    if not _is_milestone_card_selected(browser):
        return False
    try:
        return bool(
            browser.evaluate(
                """
                () => {
                  const prices = [...document.querySelectorAll('.stages__stage-price-input')]
                    .filter((i) => i.offsetParent);
                  const parent = document.querySelector('.stages')?.__vue__;
                  const local = (parent?.localStages || []).filter(
                    (s) => s && (s.title || s.payer_price)
                  );
                  const stagesReady = prices.length >= 2
                    || local.filter((s) => Number(s.payer_price || 0) > 0).length >= 2;
                  return stagesReady;
                }
                """
            )
        )
    except Exception:
        return False


def _select_milestone_payment(browser: BrowserClient) -> bool:
    if _is_milestone_card_selected(browser) and _stages_section_visible(browser, min_rows=2):
        return True
    for _ in range(5):
        if not _wait_payment_block_ready(browser, attempts=6):
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(500)
            continue
        if not _is_milestone_card_selected(browser):
            _click_milestone_payment_card(browser)
        if _wait_stages_ready(browser, attempts=20):
            return True
    return _is_milestone_card_selected(browser) and _wait_stages_ready(browser, attempts=12)


def _reassert_milestone_payment(
    browser: BrowserClient, stages: list[tuple[str, int]]
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    """Re-select milestone payment and restore stages after other field edits."""
    stages_read = _read_stages_from_dom(browser)
    if _is_milestone_payment_selected(browser) and _stages_dom_ok(stages, stages_read):
        total = _stage_total_from_read(stages_read)
        return True, stages_read, {"ok": True, "actualTotal": total}

    if not _is_milestone_card_selected(browser):
        _select_milestone_payment(browser)
        _wait_stages_ready(browser)

    stages_result = _fill_offer_stages(browser, stages)
    stages_read = _read_stages_from_dom(browser)
    stages_result["read"] = stages_read
    stages_result["ok"] = _stages_dom_ok(stages, stages_read)
    _sync_stages_draft(browser)
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(800)
    return _is_milestone_payment_selected(browser), stages_read, stages_result


def _order_title_required(browser: BrowserClient) -> bool:
    return not _is_milestone_payment_selected(browser)


def _order_title_visible(browser: BrowserClient) -> bool:
    try:
        return bool(
            browser.evaluate(
                """
                () => {
                  const ta = document.querySelector('textarea[name="name"]');
                  if (ta) {
                    const box = ta.closest('.trumbowyg-box, .form-field, .field, div');
                    if (box && box.offsetParent !== null) return true;
                  }
                  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                  const label = [...document.querySelectorAll('label, span, div, p')].find(
                    (el) => /^название заказа$/i.test(norm(el.textContent))
                  );
                  if (!label) return false;
                  const root = label.closest('.form-field, .field, div') || label.parentElement;
                  return Boolean(root && root.offsetParent !== null);
                }
                """
            )
        )
    except Exception:
        return False


def _fill_order_title(browser: BrowserClient, title: str) -> bool:
    value = (title or "").strip()[:70]
    if not value:
        return False
    if hasattr(browser, "_ensure_page"):
        try:
            page = browser._ensure_page()
            editor = page.locator(
                'div:has(textarea[name="name"]) .trumbowyg-editor'
            ).first
            if editor.count() > 0:
                editor.scroll_into_view_if_needed(timeout=8000)
                editor.click(force=True, timeout=5000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                editor.press_sequentially(value, delay=20)
                page.keyboard.press("Tab")
                if hasattr(browser, "wait_ms"):
                    browser.wait_ms(200)
                read = _read_order_title(browser)
                if read and read.lower() != "undefined":
                    return True
        except Exception:
            pass
    if _set_trumbowyg(browser, "name", value):
        read = _read_order_title(browser)
        if read:
            return True
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


def _count_visible_stage_slots(browser: BrowserClient) -> int:
    try:
        raw = browser.evaluate(
            """
            () => {
              const stages = [...document.querySelectorAll('.stages__stage')]
                .filter((row) => row.offsetParent).length;
              const prices = [...document.querySelectorAll('.stages__stage-price-input')]
                .filter((i) => i.offsetParent).length;
              return Math.max(stages, prices);
            }
            """
        )
        return int(raw) if isinstance(raw, int) else 0
    except Exception:
        return 0


def _click_add_stage_row(browser: BrowserClient) -> bool:
    try:
        if hasattr(browser, "_ensure_page"):
            page = browser._ensure_page()
            btn = page.locator(".stages span").filter(
                has_text=re.compile(r"^Добавить задачу$", re.I)
            )
            if btn.count() > 0:
                btn.first.scroll_into_view_if_needed(timeout=5000)
                btn.first.click(force=True, timeout=5000)
                return True
    except Exception:
        pass
    try:
        return bool(
            browser.evaluate(
                """
                () => {
                  const btn = [...document.querySelectorAll('.stages span')].find((el) =>
                    (el.textContent || '').trim().toLowerCase() === 'добавить задачу'
                  );
                  if (!btn) return false;
                  btn.click();
                  return true;
                }
                """
            )
        )
    except Exception:
        return False


def _ensure_stage_rows(browser: BrowserClient, needed: int) -> None:
    if not _stages_section_visible(browser, min_rows=1):
        _select_milestone_payment(browser)
        _wait_stages_ready(browser, min_rows=min(2, needed))
    for _ in range(10):
        visible = _count_visible_stage_slots(browser)
        if visible >= needed:
            return
        before = visible
        if not _click_add_stage_row(browser):
            return
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(700)
        after = _count_visible_stage_slots(browser)
        if after <= before:
            return


STAGE_VUE_SET_JS = """
(idx, plainText, amount) => {
  const text = plainText == null ? null : String(plainText || '').slice(0, 70);
  const price = amount == null ? null : Number(amount);
  const rows = [...document.querySelectorAll('.stages__list .stages__stage')];
  const row = rows[idx - 1];
  if (!row) return false;
  const vm = row.__vue__;
  const parent = document.querySelector('.stages')?.__vue__;
  if (!vm || !parent) return false;

  const stage = parent.localStages?.[idx - 1];
  if (text != null && text !== '') {
    vm.titleLocal = text;
    if (stage) stage.title = text;
    const ta = row.querySelector('textarea[name^="stageTitle-"]');
    const $ = window.jQuery || window.$;
    if (ta && $ && typeof $(ta).trumbowyg === 'function') {
      const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
      $(ta).trumbowyg('html', `<p>${esc(text)}</p>`);
      $(ta).trigger('tbwchange');
    }
    const hidden = row.querySelector('.stages__stage-text');
    if (hidden) hidden.textContent = text;
    if (typeof vm.onChangeTitle === 'function') vm.onChangeTitle(text);
    vm.$emit?.('change-title', text);
  }

  if (price != null && !Number.isNaN(price)) {
    vm.priceLocal = price;
    if (stage) stage.payer_price = price;
    const priceInput = row.querySelector('.stages__stage-price-input');
    if (priceInput) {
      priceInput.focus();
      priceInput.value = String(price);
      priceInput.dispatchEvent(new Event('input', { bubbles: true }));
      priceInput.dispatchEvent(new Event('change', { bubbles: true }));
      priceInput.blur();
    }
    if (typeof vm.onChangePrice === 'function') vm.onChangePrice(price);
  }

  if (typeof parent.recalcTotalPrice === 'function') parent.recalcTotalPrice();
  if (typeof parent.onChangeStages === 'function') parent.onChangeStages();
  parent.$emit?.('change-stages', parent.localStages);

  const offerVm = document.querySelector('.offer-custom')?.__vue__;
  const wrapVm = document.querySelector('.custom-kwork-offer__wrapper')?.__vue__;
  if (offerVm && parent.localStages?.length) {
    offerVm.stages = parent.localStages.map((s) => ({
      id: s.id || 0,
      number: s.number,
      title: s.title || '',
      payer_price: Number(s.payer_price || 0),
    }));
    offerVm.stagesCount = offerVm.stages.length;
    offerVm.priceStages = parent.localStages.reduce(
      (sum, s) => sum + Number(s.payer_price || 0),
      0,
    );
  }
  if (typeof offerVm?.updatePaymentType === 'function') {
    offerVm.updatePaymentType(offerVm.offerPaymentStages || 'stages');
  }
  if (typeof offerVm?.updateDataStages === 'function') offerVm.updateDataStages();
  if (typeof offerVm?.changeDraftContent === 'function') offerVm.changeDraftContent();

  const readTitle = String(stage?.title || vm.titleLocal || '').trim();
  const priceInput = row.querySelector('.stages__stage-price-input');
  const domPrice = Number(String(priceInput?.value || '').replace(/\\s/g, ''));
  const readPrice = Number(stage?.payer_price ?? vm.priceLocal ?? domPrice);
  const titleOk = text == null || !text || readTitle === text;
  const priceOk = price == null || Number.isNaN(price) || readPrice === price || domPrice === price;
  return titleOk && priceOk;
}
"""


def _set_stage_vue(
    browser: BrowserClient,
    idx: int,
    title: str | None,
    amount: int | None = None,
) -> bool:
    try:
        return bool(
            browser.evaluate(
                f"({STAGE_VUE_SET_JS})({idx}, {json.dumps(title)}, {json.dumps(amount)})"
            )
        )
    except Exception:
        return False


def _fill_stage_title(browser: BrowserClient, idx: int, title: str) -> bool:
    value = (title or "").strip()[:70]
    if not value:
        return False
    if hasattr(browser, "_ensure_page"):
        try:
            page = browser._ensure_page()
            editor = page.locator(
                f'div:has(textarea[name="stageTitle-{idx}"]) .trumbowyg-editor'
            ).first
            editor.scroll_into_view_if_needed(timeout=5000)
            editor.click(force=True, timeout=5000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            editor.press_sequentially(value, delay=20)
            page.keyboard.press("Tab")
            if hasattr(browser, "wait_ms"):
                browser.wait_ms(150)
        except Exception:
            pass
    try:
        return bool(
            browser.evaluate(
                f"""
                () => {{
                  const ta = document.querySelector('textarea[name="stageTitle-{idx}"]');
                  const ed = ta?.closest('.trumbowyg-box')?.querySelector('.trumbowyg-editor');
                  const text = (ed?.textContent || '').replace(/\\s+/g, ' ').trim();
                  const expected = {json.dumps(value)};
                  return Boolean(
                    text
                    && text.toLowerCase() !== 'undefined'
                    && (text === expected || text.toLowerCase() === expected.toLowerCase())
                  );
                }}
                """
            )
        )
    except Exception:
        return False


def _read_stage_prices(browser: BrowserClient) -> list[str]:
    raw = browser.evaluate(
        """
        () => [...document.querySelectorAll('.stages__stage-price-input')]
          .filter((i) => i.offsetParent)
          .map((inp) => (inp.value || '').replace(/\\s/g, ''))
        """
    )
    return [str(p) for p in raw] if isinstance(raw, list) else []


def _fill_stage_price(browser: BrowserClient, idx: int, amount: int) -> bool:
    if _set_stage_vue(browser, idx, None, amount):
        return True
    if not hasattr(browser, "_ensure_page"):
        return False
    try:
        page = browser._ensure_page()
        row = page.locator(".stages__list .stages__stage").nth(idx - 1)
        loc = row.locator(".stages__stage-price-input")
        loc.scroll_into_view_if_needed(timeout=5000)
        loc.click(force=True, timeout=5000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        loc.press_sequentially(str(amount), delay=40)
        page.keyboard.press("Tab")
        return True
    except Exception:
        return False


def _read_stages_from_dom(browser: BrowserClient) -> dict[str, Any]:
    raw = browser.evaluate(
        """
        () => {
          const rows = [...document.querySelectorAll('.stages__list .stages__stage')].map((row) => {
            const ta = row.querySelector('textarea[name^="stageTitle-"]');
            const ed = ta?.closest('.trumbowyg-box')?.querySelector('.trumbowyg-editor');
            const hidden = row.querySelector('.stages__stage-text');
            const title = (ed?.textContent || hidden?.textContent || ta?.value || '')
              .replace(/<[^>]+>/g, ' ')
              .replace(/\\s+/g, ' ')
              .trim()
              .slice(0, 70);
            const price = (row.querySelector('.stages__stage-price-input')?.value || '')
              .replace(/\\s/g, '');
            return { name: ta?.name || null, title, price };
          });
          return {
            rows: rows.map(({ name, title }) => ({ name, title })),
            prices: rows.map((r) => r.price).filter(Boolean),
            vueStages: (document.querySelector('.stages')?.__vue__?.localStages || []).map((s) => ({
              title: s.title,
              price: s.payer_price,
            })),
          };
        }
        """
    )
    return raw if isinstance(raw, dict) else {}


def _stage_title_matches(expected: str, actual: str) -> bool:
    expected_norm = (expected or "").strip().lower()
    actual_norm = (actual or "").strip().lower()
    if not expected_norm or not actual_norm or actual_norm == "undefined":
        return False
    return (
        actual_norm == expected_norm
        or expected_norm in actual_norm
        or actual_norm in expected_norm
    )


def _stages_dom_ok(stages: list[tuple[str, int]], read: dict[str, Any]) -> bool:
    expected_total = sum(amount for _, amount in stages)
    prices = [str(p) for p in (read.get("prices") or []) if p]
    sum_final = sum(int(p) for p in prices if p.isdigit())
    if len(prices) < len(stages) or sum_final != expected_total:
        return False
    dom_titles = [
        (r.get("title") or "").strip()
        for r in (read.get("rows") or [])[: len(stages)]
    ]
    vue_titles = [
        str(t.get("title") or "").strip()
        for t in (read.get("vueStages") or [])[: len(stages)]
        if isinstance(t, dict)
    ]
    for i, (exp_title, exp_amount) in enumerate(stages):
        if prices[i] != str(exp_amount):
            return False
        dom_ok = i < len(dom_titles) and _stage_title_matches(exp_title, dom_titles[i])
        vue_ok = i < len(vue_titles) and _stage_title_matches(exp_title, vue_titles[i])
        if not (dom_ok or vue_ok):
            return False
    return True


def _finalize_offer_form(
    browser: BrowserClient,
    *,
    text: str,
    price: str,
    order_title: str,
    delivery_days: int,
    stages: list[tuple[str, int]],
) -> dict[str, Any]:
    milestone = _select_milestone_payment(browser)
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(1000)

    stages_result = _fill_offer_stages(browser, stages)
    if not stages_result.get("ok"):
        stages_result = _fill_offer_stages(browser, stages)

    desc_ok = _fill_description(browser, text)
    price_ok = _fill_price(browser, str(price))
    title_required = len(stages) < 2
    title_ok = True
    read_title = ""
    if title_required and order_title:
        title_ok = _fill_order_title(browser, order_title)
        read_title = _read_order_title(browser)
        if not read_title:
            title_ok = _fill_order_title(browser, order_title) or title_ok
            read_title = _read_order_title(browser)
    deadline_result = _fill_deadline(browser, delivery_days)
    if not deadline_result.get("ok") and hasattr(browser, "wait_ms"):
        browser.wait_ms(1500)
        deadline_result = _fill_deadline(browser, delivery_days)

    milestone_selected, stages_read, reassert_stages = _reassert_milestone_payment(
        browser, stages
    )
    if reassert_stages.get("filled"):
        stages_result = reassert_stages
    elif not _stages_dom_ok(stages, stages_read):
        stages_result = _fill_offer_stages(browser, stages)
        stages_read = _read_stages_from_dom(browser)
        stages_result["read"] = stages_read
        stages_result["ok"] = _stages_dom_ok(stages, stages_read)

    if not desc_ok or _read_description_len(browser) < 150:
        desc_ok = _fill_description(browser, text)

    if milestone_selected:
        title_ok = True

    return {
        "milestone": milestone,
        "milestoneSelected": milestone_selected,
        "titleRequired": title_required,
        "stages": stages_result,
        "stagesRead": stages_read,
        "desc_ok": desc_ok,
        "title_ok": title_ok,
        "read_title": read_title,
        "price_ok": price_ok,
        "deadline": deadline_result,
    }


def _sync_stages_draft(browser: BrowserClient) -> None:
    try:
        browser.evaluate(
            """
            () => {
              const parent = document.querySelector('.stages')?.__vue__;
              const offerVm = document.querySelector('.offer-custom')?.__vue__;
              const local = parent?.localStages || [];
              const payStages = offerVm?.offerPaymentStages || 'stages';
              if (offerVm && local.length) {
                offerVm.stages = local.map((s) => ({
                  id: s.id || 0,
                  number: s.number,
                  title: s.title || '',
                  payer_price: Number(s.payer_price || 0),
                }));
                offerVm.stagesCount = offerVm.stages.length;
                offerVm.priceStages = local.reduce(
                  (sum, s) => sum + Number(s.payer_price || 0),
                  0,
                );
              }
              if (typeof offerVm?.updatePaymentType === 'function') {
                offerVm.updatePaymentType(payStages);
              }
              if (typeof offerVm?.updateDataStages === 'function') {
                offerVm.updateDataStages();
              }
              if (typeof offerVm?.changeDraftContent === 'function') {
                offerVm.changeDraftContent();
              }
              return true;
            }
            """
        )
    except Exception:
        pass


def _fill_offer_stages(
    browser: BrowserClient, stages: list[tuple[str, int]]
) -> dict[str, Any]:
    if len(stages) < 2:
        return {"ok": False, "reason": "need_min_2_stages"}
    expected_total = sum(amount for _, amount in stages)
    selected = _select_milestone_payment(browser)
    if not selected:
        return {"ok": False, "reason": "milestone_not_selected", "selected": False}
    if not _wait_stages_ready(browser):
        _click_milestone_payment_card(browser)
        if not _wait_stages_ready(browser, attempts=24):
            return {
                "ok": False,
                "reason": "stages_not_visible",
                "selected": selected,
            }
    if hasattr(browser, "wait_ms"):
        browser.wait_ms(1200)
    _ensure_stage_rows(browser, len(stages))

    filled: list[dict[str, Any]] = []
    for idx, (title, amount) in enumerate(stages, start=1):
        _set_stage_vue(browser, idx, title, amount)
        title_ok = _fill_stage_title(browser, idx, title)
        price_ok = _fill_stage_price(browser, idx, amount)
        filled.append(
            {
                "idx": idx,
                "title": title,
                "amount": amount,
                "titleOk": title_ok,
                "priceOk": price_ok,
            }
        )
        if hasattr(browser, "wait_ms"):
            browser.wait_ms(300)

    if hasattr(browser, "wait_ms"):
        browser.wait_ms(500)

    _sync_stages_draft(browser)

    read = _read_stages_from_dom(browser)
    prices_final = [str(p) for p in (read.get("prices") or []) if p]
    sum_final = sum(int(p) for p in prices_final if p.isdigit())
    titles_ok = _stages_dom_ok(stages, read)
    milestone_ok = _is_milestone_payment_selected(browser)
    ok = titles_ok and milestone_ok
    return {
        "ok": ok,
        "selected": selected,
        "milestoneSelected": milestone_ok,
        "filled": filled,
        "read": read,
        "expectedTotal": expected_total,
        "actualTotal": sum_final,
    }


def _stage_total_from_read(stages_read: dict[str, Any]) -> int:
    prices = [str(p) for p in (stages_read.get("prices") or []) if p]
    return sum(int(re.sub(r"\D", "", p) or 0) for p in prices)


def _autosave_wait(browser: BrowserClient, *, wait_ms: int = 8000) -> None:
    _sync_stages_draft(browser)
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
            self.browser.wait_ms(6000)

        if not is_logged_in(self.browser):
            return SubmitResult(
                success=False,
                project_id=project_id,
                message="not_logged_in: нужен логин Kwork на VPS",
            )

        blocked = _check_offer_form_available(self.browser, project_id)
        if blocked is not None:
            return blocked

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

        if not _prime_payment_ui(self.browser, str(price)):
            diag = _read_payment_diag(self.browser)
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_payment_block_missing: {diag}",
            )

        if not _select_milestone_payment(self.browser):
            diag = _read_payment_diag(self.browser)
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_milestone_click_failed: {diag}",
            )
        if not _wait_stages_ready(self.browser, attempts=20):
            return SubmitResult(
                success=False,
                project_id=project_id,
                message="prepare_stages_not_visible",
            )

        price_ok = _fill_price(self.browser, str(price))
        if hasattr(self.browser, "wait_ms"):
            self.browser.wait_ms(400)
        desc_ok = _fill_description(self.browser, text)

        stages = plan_offer_stages(int(price), project)
        final = _finalize_offer_form(
            self.browser,
            text=text,
            price=str(price),
            order_title=order_title,
            delivery_days=delivery_days,
            stages=stages,
        )
        stages_result = final["stages"]
        stages_read = final["stagesRead"]
        title_ok = final["title_ok"]
        desc_ok = final["desc_ok"] or desc_ok
        price_ok = final["price_ok"] or price_ok
        deadline_result = final["deadline"]

        _autosave_wait(self.browser, wait_ms=5000)
        title_required = len(stages) < 2
        if not _stages_dom_ok(stages, stages_read) or (
            title_required and order_title and not final.get("read_title")
        ):
            final = _finalize_offer_form(
                self.browser,
                text=text,
                price=str(price),
                order_title=order_title,
                delivery_days=delivery_days,
                stages=stages,
            )
            stages_result = final["stages"]
            stages_read = final["stagesRead"]
            title_ok = final["title_ok"]
            desc_ok = final["desc_ok"] or desc_ok
            price_ok = final["price_ok"] or price_ok
            deadline_result = final["deadline"]
            _autosave_wait(self.browser, wait_ms=5000)

        fill_result: dict[str, Any] = {"finalize": final}

        _autosave_wait(self.browser, wait_ms=3000)
        milestone_selected, stages_read, stages_result = _reassert_milestone_payment(
            self.browser, stages
        )
        final["milestoneSelected"] = milestone_selected
        final["stages"] = stages_result
        final["stagesRead"] = stages_read
        fill_result["finalize"] = final
        _autosave_wait(self.browser, wait_ms=15000)
        if milestone_selected and _read_description_len(self.browser) < 150:
            desc_ok = _fill_description(self.browser, text)
            _autosave_wait(self.browser, wait_ms=2000)
        readback = _read_offer_form(self.browser)
        desc_len = _read_description_len(self.browser) or int(readback.get("descLen") or 0)
        read_price = str(readback.get("price") or "").replace(" ", "")
        read_title = _read_order_title(self.browser) or str(readback.get("title") or "").strip()
        stages_read = _read_stages_from_dom(self.browser)
        milestone_selected = _is_milestone_payment_selected(self.browser)
        title_required = len(stages) < 2
        read_deadline = str(
            readback.get("deadline") or readback.get("deadlineLabel") or ""
        ).strip()
        days_set = bool(
            isinstance(deadline_result, dict)
            and deadline_result.get("ok")
            and (read_deadline or re.search(r"\d", str(deadline_result.get("picked") or "")))
        )
        min_desc = min(150, max(50, len(text.strip()) // 3))

        if not isinstance(fill_result, dict):
            fill_result = {}
        fill_result["hasDesc"] = desc_ok
        fill_result["hasPrice"] = price_ok
        fill_result["hasTitle"] = title_ok if title_required else True
        fill_result["titleRequired"] = title_required
        fill_result["milestoneSelected"] = milestone_selected
        fill_result["daysSet"] = days_set
        fill_result["deadline"] = deadline_result
        fill_result["readback"] = readback
        fill_result["playwrightDesc"] = desc_ok
        fill_result["playwrightPrice"] = price_ok
        fill_result["playwrightTitle"] = title_ok
        fill_result["stages"] = stages_result
        fill_result["stagesPlan"] = [{"title": t, "amount": a} for t, a in stages]

        if desc_len < min_desc or not read_price:
            if desc_ok and read_price and desc_len < min_desc:
                desc_ok = _fill_description(self.browser, text)
                if hasattr(self.browser, "wait_ms"):
                    self.browser.wait_ms(800)
                desc_len = _read_description_len(self.browser) or desc_len
                fill_result["descRefilled"] = True
                fill_result["descLenAfterRefill"] = desc_len
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
        if title_required and order_title and not read_title:
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_title_empty: {fill_result}",
            )
        fill_result["stagesRead"] = stages_read
        if not final.get("milestoneSelected") and not fill_result["milestoneSelected"]:
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_milestone_not_selected: {fill_result}",
            )
        if not stages_result.get("ok") or not _stages_dom_ok(stages, stages_read):
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_stages_failed: {fill_result}",
            )
        stage_total = int(stages_result.get("actualTotal") or 0)
        if stage_total <= 0:
            stage_total = _stage_total_from_read(stages_read)
        if stage_total != int(price) or read_price_int != int(price):
            return SubmitResult(
                success=False,
                project_id=project_id,
                message=f"prepare_total_mismatch: expected={price} got={read_price_int} stages={stage_total} {fill_result}",
            )

        message = f"prepared: verified desc={desc_len} price={read_price}"
        if title_required:
            message += f" title={read_title!r}"
        message += f" stages={len(stages)} milestone={milestone_selected}"
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
