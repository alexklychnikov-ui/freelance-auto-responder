"""Inspect deadline field via direct new_offer URL."""
from __future__ import annotations

import json

from src.adapters.kwork import _build_offer_fill_js
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

PROJECT_ID = "3202581"

INSPECT_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const allText = [...document.querySelectorAll('label, .form-field__name, .field-name, span, div')]
    .filter((el) => /срок|название заказа/i.test(norm(el.textContent)))
    .slice(0, 20)
    .map((el) => ({ tag: el.tagName, text: norm(el.textContent).slice(0, 60), cls: String(el.className || '').slice(0, 80) }));

  const inputs = [...document.querySelectorAll('input, textarea, select, [role="combobox"]')].map((el) => ({
    tag: el.tagName,
    type: el.type || el.getAttribute('role'),
    name: el.name,
    id: el.id,
    placeholder: el.getAttribute('placeholder'),
    cls: String(el.className || '').slice(0, 100),
    ro: el.readOnly,
  }));

  const selects = [...document.querySelectorAll('select')].map((sel) => ({
    id: sel.id,
    name: sel.name,
    options: [...sel.options].map((o) => norm(o.textContent)).slice(0, 20),
  }));

  return { url: location.href, allText, inputs: inputs.slice(0, 30), selects };
}
"""

OPEN_DROPDOWN_JS = """
(days) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const blocks = [...document.querySelectorAll('.form-item, .offer-form__item, .field, div')];
  const block = blocks.find((el) => {
    const t = norm(el.textContent);
    return t.includes('Срок выполнения') && t.length < 120;
  });
  const triggers = block
    ? [...block.querySelectorAll('*')].filter((el) => {
        const t = norm(el.textContent);
        return t === 'Срок выполнения' || el.getAttribute('placeholder') === 'Срок выполнения';
      })
    : [];
  const clickTarget =
    block?.querySelector('.multiselect, .multiselect__select, .v-input, input, [class*="select"]') ||
    triggers[0]?.closest('div') ||
    [...document.querySelectorAll('.multiselect, [class*="select"]')].find((el) =>
      /срок выполнения/i.test(norm(el.textContent) + norm(el.getAttribute('placeholder')))
    );
  if (clickTarget) clickTarget.click();

  const opts = [...document.querySelectorAll(
    '.multiselect__option, .multiselect__element, .v-list-item, [role="option"], li'
  )].map((el) => norm(el.textContent)).filter((t) => t && /\\d/.test(t));

  return {
    blockText: block ? norm(block.textContent).slice(0, 120) : null,
    clicked: Boolean(clickTarget),
    clickCls: String(clickTarget?.className || '').slice(0, 120),
    opts: [...new Set(opts)].slice(0, 40),
  };
}
"""


def main() -> None:
    browser = PlaywrightBrowserAdapter()
    try:
        for url in [
            f"https://kwork.ru/new_offer?project={PROJECT_ID}",
            f"https://kwork.ru/projects/{PROJECT_ID}/view",
        ]:
            print("===", url)
            browser.navigate(url)
            browser.wait_ms(4000)
            print(json.dumps(browser.evaluate(INSPECT_JS), ensure_ascii=False, indent=2))
            print("dropdown:", json.dumps(browser.evaluate(OPEN_DROPDOWN_JS), ensure_ascii=False, indent=2))
            browser.wait_ms(1500)
            print("fill:", browser.evaluate(_build_offer_fill_js("t", "60000", order_title="test")))
    finally:
        browser.close()


if __name__ == "__main__":
    main()
