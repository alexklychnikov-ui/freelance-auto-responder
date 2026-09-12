from __future__ import annotations

import json
import re
from pathlib import Path

from deploy._fill_kwork_landing import (
    delete_empty_extras,
    dump_hidden,
    fill_contentedit,
    fill_desc_or_inst,
    fill_size_volume,
    set_hidden,
    sync_contentedit,
)
from deploy._fill_kwork_new_draft import buyer_to_select_value, fill_extras_playwright
from deploy._fill_kwork_price_monitor import (
    apply_faqs_app,
    disable_template_inputs,
    extras_from_post,
    fill_work_time,
    hook_save_form,
    invoke_de,
    pin_extra_inputs,
)
from deploy._fix_kwork_faq4 import apply_faqs_vue
from deploy.kwork_listing_fullcrm import CRM, buyer_copy_problems
from deploy.kwork_listing_skel import assemble, validate
from src.adapters.kwork_auth import is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

OUR_DRAFT = "54655872"
NEW_URL = f"https://kwork.ru/new?draft_id={OUR_DRAFT}"
TYPE_RADIO = "#attribute_item_5016"
KIND_LANDING = "#attribute_item_10"
CMS_RADIO = "#attribute_item_730"
JS_YES = "attribute_item_10088"
CSS_YES = "attribute_item_10647"
DB_YES = "attribute_item_11153"
PY_ID = "attribute_item_9699"
FORBIDDEN_IDS = ("51315832", "52958975", "54298174", "54261579")
WANTED_EXTRAS = [
    "Лента сообщений на карточках",
    "Сводка воронки и денег",
    "ИИ-подсказки по сделкам",
    "Контейнеры и выкладка на сервер",
    "Ещё один пользователь в кабинете",
    "Срочный запуск в приоритете",
]
LOG_DIR = Path("logs")
STORAGE = Path("data/kwork_storage.json")


def session_looks_live() -> bool:
    if not STORAGE.exists():
        return False
    try:
        data = json.loads(STORAGE.read_text(encoding="utf-8"))
    except Exception:
        return False
    cookies = data.get("cookies") or []
    return any(str(c.get("name") or "") and "kwork" in str(c.get("domain") or "") for c in cookies)


def href_forbidden(href: str) -> bool:
    return any(x in (href or "") for x in FORBIDDEN_IDS)


def href_ok(href: str) -> bool:
    if href_forbidden(href):
        return False
    return "/new" in (href or "") or "/edit?id=" in (href or "")


def dump_attrs(page) -> dict:
    return page.evaluate(
        """() => {
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const radios = [...document.querySelectorAll('input[id^="attribute_item_"]')].map((el) => {
            const lab = document.querySelector('label[for="' + el.id + '"]');
            return {
              id: el.id,
              name: el.name,
              value: el.value,
              type: el.type,
              checked: !!el.checked,
              label: n((lab && lab.innerText) || ''),
            };
          });
          const priceSels = [...document.querySelectorAll('select')].filter((el) => {
            const blob = (el.name || '') + (el.id || '') + (el.className || '');
            return /price|цен/i.test(blob) && !/extra/i.test(blob);
          }).map((el) => ({
            name: el.name,
            id: el.id,
            value: el.value,
            opts: [...el.options].slice(0, 12).map((o) => ({ v: o.value, t: n(o.textContent) })),
          }));
          return {
            url: location.href,
            category_id: (document.querySelector('[name="category_id"]') || {}).value || '',
            parent: (document.querySelector('.js-category_parent') || {}).value || '',
            typeOn: !!(document.querySelector('#attribute_item_5016') || {}).checked,
            kindLandingOn: !!(document.querySelector('#attribute_item_10') || {}).checked,
            kindServiceOn: !!(document.querySelector('#attribute_item_50') || {}).checked,
            radios,
            priceSels,
          };
        }"""
    )


def pick_category(page) -> dict:
    parent = page.evaluate(
        """() => {
          const el = document.querySelector('.js-category_parent');
          if (!el) return { ok: false, reason: 'no_parent' };
          const $ = window.jQuery;
          const opts = [...el.options].map((o) => ({ v: o.value, t: (o.textContent || '').trim() }));
          const hit = opts.find((o) => /Разработка и IT/i.test(o.t));
          if (!hit) return { ok: false, reason: 'no_parent_opt', opts };
          if ($ && $(el).length) $(el).val(hit.v).trigger('chosen:updated').trigger('change');
          else {
            el.value = hit.v;
            el.dispatchEvent(new Event('change', { bubbles: true }));
          }
          return { ok: true, value: el.value, text: hit.t };
        }"""
    )
    page.wait_for_timeout(1800)
    sub = {"ok": False}
    for _ in range(8):
        sub = page.evaluate(
            """() => {
              const el = document.querySelector('.js-category_sub, [name="category_id"]');
              if (!el) return { ok: false, reason: 'no_sub' };
              const $ = window.jQuery;
              const opts = [...el.options].map((o) => ({ v: o.value, t: (o.textContent || '').trim() }));
              const hit = opts.find((o) => o.v === '37' || /Создание сайта/i.test(o.t));
              if (!hit) return { ok: false, reason: 'no_sub_opt', opts: opts.slice(0, 30) };
              if ($ && $(el).length) $(el).val(hit.v).trigger('chosen:updated').trigger('change');
              else {
                el.value = hit.v;
                el.dispatchEvent(new Event('change', { bubbles: true }));
              }
              return { ok: true, value: el.value, text: hit.t };
            }"""
        )
        if sub.get("ok"):
            break
        page.wait_for_timeout(400)
    page.wait_for_timeout(2000)
    return {"parent": parent, "sub": sub}


def set_cms_custom(page) -> dict:
    clicked: bool | str = False
    loc = page.locator("label[for='attribute_item_730']")
    if loc.count():
        try:
            loc.first.click(force=True, timeout=5000)
            clicked = True
        except Exception as exc:
            clicked = str(exc)[:160]
    page.wait_for_timeout(200)
    pinned = page.evaluate(
        """() => {
          const el = document.querySelector('#attribute_item_730');
          if (!el) return { ok: false, reason: 'missing' };
          el.disabled = false;
          el.checked = true;
          const $ = window.jQuery;
          if ($ && $(el).length) $(el).prop('checked', true).prop('disabled', false).trigger('change');
          else el.dispatchEvent(new Event('change', { bubbles: true }));
          const lab = document.querySelector('label[for="attribute_item_730"]');
          return {
            ok: !!el.checked,
            id: el.id,
            name: el.name,
            value: el.value,
            type: el.type,
            label: ((lab && lab.innerText) || '').replace(/\\s+/g, ' ').trim(),
          };
        }"""
    )
    pinned["labelClick"] = clicked
    return pinned


def check_attr_id(page, attr_id: str) -> dict:
    loc = page.locator(f"label[for='{attr_id}']")
    clicked: bool | str = False
    if loc.count():
        try:
            loc.first.click(force=True, timeout=5000)
            clicked = True
        except Exception as exc:
            clicked = str(exc)[:160]
    page.wait_for_timeout(200)
    pinned = page.evaluate(
        """(id) => {
          const el = document.querySelector('#' + id);
          if (!el) return { ok: false, reason: 'missing', id };
          el.disabled = false;
          el.checked = true;
          const $ = window.jQuery;
          if ($ && $(el).length) $(el).prop('checked', true).prop('disabled', false).trigger('change');
          else el.dispatchEvent(new Event('change', { bubbles: true }));
          return { ok: !!el.checked, id, name: el.name, type: el.type, value: el.value };
        }""",
        attr_id,
    )
    pinned["labelClick"] = clicked
    return pinned


def wait_label(page, text: str, *, tries: int = 12) -> bool:
    for _ in range(tries):
        found = page.evaluate(
            """(text) => {
              const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
              return [...document.querySelectorAll('label')].some((lab) => n(lab.innerText) === text);
            }""",
            text,
        )
        if found:
            return True
        page.wait_for_timeout(400)
    return False


def check_box_label(page, text: str) -> dict:
    return page.evaluate(
        """(text) => {
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const labs = [...document.querySelectorAll('label')].filter((lab) => n(lab.innerText) === text);
          for (const lab of labs) {
            const el = (lab.htmlFor && document.getElementById(lab.htmlFor)) || lab.querySelector('input');
            if (!el || el.type === 'radio') continue;
            el.disabled = false;
            el.checked = true;
            const $ = window.jQuery;
            if ($ && $(el).length) $(el).prop('checked', true).prop('disabled', false).trigger('change');
            else el.dispatchEvent(new Event('change', { bubbles: true }));
            return { ok: !!el.checked, id: el.id, name: el.name, type: el.type };
          }
          return { ok: false, reason: 'label_missing', text };
        }""",
        text,
    )


def _css_fw_field_js() -> str:
    return """
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const isCss = (t) => /^Фреймворк CSS/i.test(t) && t.length < 48;
          let field = null;
          [...document.querySelectorAll('.kwork-save-step__field, .js-field-block, [class*="classification"]')].forEach((wrap) => {
            const head = n(((wrap.querySelector('.kwork-save-step__field-label, .kwork-save-step__field-title, .field-label')) || {}).innerText || '');
            if (isCss(head)) field = wrap;
          });
          if (!field) {
            const node = [...document.querySelectorAll('div, span, label, h3, h4')].find((el) => {
              const t = n(el.childNodes && el.childNodes.length === 1 ? el.textContent : (el.innerText || ''));
              return isCss(t);
            });
            field = node && (node.closest('.kwork-save-step__field, .js-field-block, .card') || node.parentElement);
          }
    """


def add_css_tailwind(page) -> dict:
    clicked = page.evaluate(
        """() => {
          """ + _css_fw_field_js() + """
          if (!field) return { ok: false, reason: 'no_css_field' };
          const titles = [...field.querySelectorAll('input.js-custom-attribute, input[name*="custom_attribute_title"]')];
          const have = titles.find((el) => /tailwind/i.test(el.value || ''));
          if (have) return { ok: true, how: 'existing', value: have.value, name: have.name };
          const add = [...field.querySelectorAll('a, button, span')].find((el) => {
            const t = n(el.textContent);
            return /^добавить свой вариант$/i.test(t) && t.length < 40;
          });
          if (!add) {
            return { ok: false, reason: 'no_add', checks: field.querySelectorAll('input[type="checkbox"]').length };
          }
          add.click();
          return { ok: true, how: 'clicked_add', names: titles.map((el) => el.name) };
        }"""
    )
    page.wait_for_timeout(600)
    filled = page.evaluate(
        """(title) => {
          """ + _css_fw_field_js() + """
          if (!field) return { ok: false, reason: 'no_css_field_fill' };
          const inputs = [...field.querySelectorAll('input.js-custom-attribute, input[name*="custom_attribute_title"]')];
          let inp = inputs.find((el) => /tailwind/i.test(el.value || ''));
          if (!inp) inp = [...inputs].reverse().find((el) => !(el.value || '').trim()) || inputs[inputs.length - 1];
          if (!inp) {
            return {
              ok: false,
              reason: 'no_input',
              n: inputs.length,
              names: [...field.querySelectorAll('input')].map((el) => el.name).filter(Boolean).slice(0, 20),
            };
          }
          inp.disabled = false;
          const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
          if (proto && proto.set) proto.set.call(inp, title);
          else inp.value = title;
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          inp.dispatchEvent(new Event('change', { bubbles: true }));
          const $ = window.jQuery;
          if ($ && $(inp).length) $(inp).val(title).trigger('input').trigger('change');
          const m = String(inp.name || '').match(/custom_attribute_title\\[(\\d+)\\](?:\\[(\\d+)\\])?/);
          const parent = m ? m[1] : '10650';
          const idx = m && m[2] ? m[2] : '1';
          let cb = [...field.querySelectorAll('input[type="checkbox"]')].find((el) => {
            const nm = el.name || '';
            return nm === 'new_custom_attribute[' + parent + '][]'
              || nm === 'custom_attribute[' + parent + '][]'
              || (nm.indexOf('custom_attribute[' + parent + ']') >= 0);
          });
          if (!cb) {
            const row = inp.closest('div, li, label, .js-add-custom-attribute') || inp.parentElement;
            cb = row && row.querySelector('input[type="checkbox"]');
          }
          if (!cb) {
            cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.name = 'new_custom_attribute[' + parent + '][]';
            cb.value = idx;
            cb.checked = true;
            cb.className = 'js-multiple-attribute';
            inp.parentElement.insertBefore(cb, inp);
          }
          cb.disabled = false;
          cb.checked = true;
          if ($ && $(cb).length) $(cb).prop('checked', true).prop('disabled', false).trigger('change');
          else cb.dispatchEvent(new Event('change', { bubbles: true }));
          return {
            ok: !!(inp.value) && !!cb.checked,
            how: 'filled',
            value: inp.value,
            name: inp.name,
            cb: cb.name,
            cbVal: cb.value,
            names: inputs.map((el) => el.name),
          };
        }""",
        "TailwindCSS",
    )
    filled["clicked"] = clicked
    return filled


def set_js_css_db_stack(page) -> dict:
    out: dict = {}
    out["jsYes"] = check_attr_id(page, JS_YES)
    page.wait_for_timeout(600)
    out["reactWait"] = wait_label(page, "React")
    out["react"] = check_box_label(page, "React")
    out["cssYes"] = check_attr_id(page, CSS_YES)
    page.wait_for_timeout(600)
    wait_label(page, "Bootstrap", tries=8)
    out["cssFw"] = add_css_tailwind(page)
    out["dbOn"] = check_attr_id(page, DB_YES)
    page.wait_for_timeout(600)
    out["pgWait"] = wait_label(page, "PostgreSQL")
    out["pg"] = check_box_label(page, "PostgreSQL")
    out["python"] = check_attr_id(page, PY_ID)
    out["ok"] = bool(
        out["jsYes"].get("ok")
        and out["react"].get("ok")
        and out["cssYes"].get("ok")
        and out["cssFw"].get("ok")
        and out["dbOn"].get("ok")
        and out["pg"].get("ok")
        and out["python"].get("ok")
    )
    return out


def click_radio_label(page, label: str) -> dict:
    return page.evaluate(
        """(text) => {
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const radios = [...document.querySelectorAll('input[id^="attribute_item_"]')];
          for (const el of radios) {
            const lab = document.querySelector('label[for="' + el.id + '"]');
            if (n((lab && lab.innerText) || '') !== text) continue;
            if (el.id === 'attribute_item_10') return { ok: false, reason: 'landing_kind_blocked', id: el.id };
            el.disabled = false;
            const $ = window.jQuery;
            if ($ && $(el).length) $(el).prop('checked', true).trigger('click').trigger('change');
            else {
              el.checked = true;
              el.click();
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return { ok: !!el.checked, id: el.id, name: el.name, value: el.value };
          }
          return { ok: false, reason: 'label_missing', text };
        }""",
        label,
    )


def fill_price_buyer(page, buyer: int) -> dict:
    return page.evaluate(
        """(buyer) => {
          const $ = window.jQuery;
          const hits = [];
          const setSel = (el, v) => {
            if (!el) return;
            if ($ && $(el).length) $(el).val(String(v)).trigger('chosen:updated').trigger('change');
            else {
              el.value = String(v);
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }
          };
          [...document.querySelectorAll('select')].forEach((el) => {
            const blob = (el.name || '') + (el.id || '') + (el.className || '');
            if (/extra/i.test(blob)) return;
            if (!/price|цен|typical|min_volume/i.test(blob)) return;
            const opts = [...el.options].map((o) => ({ v: o.value, t: (o.textContent || '').trim() }));
            const hit = opts.find((o) =>
              o.v === String(buyer)
              || o.t.replace(/\\s/g, '') === String(buyer)
              || o.t.includes(String(buyer) + ' ')
              || o.t.includes(String(buyer) + '₽')
            );
            if (!hit) {
              hits.push({ name: el.name, id: el.id, value: el.value, skip: true, opts: opts.slice(0, 6) });
              return;
            }
            setSel(el, hit.v);
            hits.push({ name: el.name, id: el.id, value: el.value, wanted: hit.v });
          });
          return { ok: hits.some((x) => x.wanted), hits };
        }""",
        buyer,
    )


def fill_extras_js(page, extras: list[dict]) -> list[dict]:
    page.evaluate(
        """() => {
          document.querySelectorAll('a.js-extra-add-btn').forEach((a) => {
            a.addEventListener('click', (e) => e.preventDefault(), true);
          });
        }"""
    )
    revealed = page.evaluate(
        """() => {
          const live = () => [...document.querySelectorAll('.add-extra__item-name-input')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'));
          const ext = document.querySelector('.kwork-save-step__second-step_ext');
          if (ext) ext.style.setProperty('display', 'block', 'important');
          const $ = window.jQuery || window.$;
          const btn = document.querySelector('a.js-extra-add-btn');
          const before = live().length;
          if (before === 0 && $ && btn) $(btn).trigger('click');
          else if (before === 0 && btn) btn.click();
          return { before, after: live().length, usedJq: !!$ };
        }"""
    )
    print("extras_reveal", json.dumps(revealed, ensure_ascii=True))
    page.wait_for_timeout(800)
    results = []
    for i, extra in enumerate(extras):
        grown = page.evaluate(
            """(i) => {
              const live = () => [...document.querySelectorAll('.add-extra__item-name-input')]
                .filter((el) => !el.closest('.js-my-extras-templates__item'));
              const before = live().length;
              if (before > i) return { clicked: false, n: before };
              const btn = document.querySelector('a.js-extra-add-btn');
              if (!btn) return { clicked: false, n: before, err: 'no_btn' };
              const $ = window.jQuery || window.$;
              if ($) $(btn).trigger('click');
              else btn.click();
              return { clicked: true, n: live().length, before, href: location.href, usedJq: !!$ };
            }""",
            i,
        )
        page.wait_for_timeout(800)
        href = page.evaluate("() => location.href") or ""
        if not href_ok(href):
            print("FAIL extras navigated", href, grown)
            return results
        price = buyer_to_select_value(extra["price"])
        try:
            typed = page.evaluate(
                """({ i, name, hint, price, days }) => {
              const live = (sel) => [...document.querySelectorAll(sel)]
                .filter((el) => !el.closest('.js-my-extras-templates__item'));
              const put = (el, text, fieldName) => {
                if (!el) return false;
                el.focus();
                document.execCommand('selectAll', false, null);
                let ok = document.execCommand('insertText', false, text);
                if (!ok || !(el.innerText || '').trim()) {
                  el.textContent = text;
                  ok = true;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                const row = el.closest('.js-add-extra-row');
                const storage = (row && fieldName && row.querySelector('[name="' + fieldName + '"]'))
                  || (el.parentElement && el.parentElement.querySelector('.js-content-storage'));
                if (storage) {
                  storage.disabled = false;
                  storage.value = text;
                  storage.dispatchEvent(new Event('input', { bubbles: true }));
                  storage.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return { ok: !!ok, storage: storage ? storage.name : null };
              };
              const names = live('.add-extra__item-name-input');
              const hints = live('.add-extra__item-description-input');
              const prices = live('select.js-extra-price');
              const durs = live('select.js-extra-duration');
              const $ = window.jQuery;
              const setSel = (el, v) => {
                if (!el) return null;
                if ($ && $(el).length) $(el).val(String(v)).trigger('chosen:updated').trigger('change');
                else {
                  el.value = String(v);
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return el.value;
              };
              return {
                okN: put(names[i], name, 'my_extras_name[]'),
                okH: put(hints[i], hint, 'my_extras_description[]'),
                n: names.length,
                nameText: names[i] ? (names[i].innerText || '').trim() : '',
                price: setSel(prices[i], price),
                days: setSel(durs[i], days),
              };
            }""",
                {"i": i, "name": extra["name"], "hint": extra["hint"], "price": price, "days": extra["days"]},
            )
        except Exception as exc:
            print("typed_fail", i, str(exc)[:200])
            typed = {"ok": False, "err": str(exc)[:200]}
        results.append({"i": i, "grown": grown, "typed": typed, "name": extra["name"]})
    pin_extra_inputs(page, extras)
    return results


def persist_dump(page) -> dict:
    return page.evaluate(
        """() => {
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const strip = (s) => n((s || '').replace(/<[^>]+>/g, ' '));
          const live = (sel) => [...document.querySelectorAll(sel)]
            .filter((el) => !el.closest('.js-my-extras-templates__item'));
          let faqsJson = window.faqsJson || null;
          let faqsParsed = [];
          if (typeof faqsJson === 'string' && faqsJson.trim()) {
            try { faqsParsed = JSON.parse(faqsJson); } catch (e) { faqsParsed = []; }
          } else if (Array.isArray(faqsJson)) {
            faqsParsed = faqsJson;
          }
          const extrasMy = live('input[name="my_extras_name[]"]').map((el) => n(el.value)).filter(Boolean);
          const extrasEditors = live('.add-extra__item-name-input').map((el) => n(el.innerText)).filter(Boolean);
          const desc = document.querySelector('#step1-description');
          const inst = document.querySelector('#step1-instruction');
          const descVal = desc ? String(desc.value || '') : '';
          const instVal = inst ? String(inst.value || '') : '';
          const descTrum = document.querySelector("textarea[name='description']")
            && document.querySelector("textarea[name='description']").closest('.trumbowyg-box')
            && document.querySelector("textarea[name='description']").closest('.trumbowyg-box').querySelector('.trumbowyg-editor');
          const instTrum = document.querySelector("textarea[name='instruction']")
            && document.querySelector("textarea[name='instruction']").closest('.trumbowyg-box')
            && document.querySelector("textarea[name='instruction']").closest('.trumbowyg-box').querySelector('.trumbowyg-editor');
          const descText = descTrum ? n(descTrum.innerText) : strip(descVal);
          const instText = instTrum ? n(instTrum.innerText) : strip(instVal);
          return {
            url: location.href,
            title: n((document.querySelector('.js-kwork-title-editor') || {}).innerText || ''),
            titleHidden: n((document.querySelector('#step1-name') || {}).value || ''),
            extrasMy,
            extrasEditors,
            extras: extrasMy.length ? extrasMy : extrasEditors,
            faqsJson: faqsParsed.map((x) => n(x.question || x.q || '')).filter(Boolean),
            faqsJsonN: faqsParsed.length,
            descLen: Math.max(descText.length, strip(descVal).length),
            instLen: Math.max(instText.length, strip(instVal).length),
            descHead: strip(descVal).slice(0, 180),
            instHasPrishlite: /Пришлите/i.test(instVal + instText),
            typeOn: !!(document.querySelector('#attribute_item_5016') || {}).checked,
            kindLandingOn: !!(document.querySelector('#attribute_item_10') || {}).checked,
            kindServiceOn: !!(document.querySelector('#attribute_item_50') || {}).checked,
            cmsOn: !!(document.querySelector('#attribute_item_730') || {}).checked,
            jsYes: !!(document.querySelector('#attribute_item_10088') || {}).checked,
            cssYes: !!(document.querySelector('#attribute_item_10647') || {}).checked,
            dbOn: !!(document.querySelector('#attribute_item_11153') || {}).checked,
            pythonOn: !!(document.querySelector('#attribute_item_9699') || {}).checked,
            reactOn: [...document.querySelectorAll('label')].some((lab) => {
              if (n(lab.innerText) !== 'React') return false;
              const el = (lab.htmlFor && document.getElementById(lab.htmlFor)) || lab.querySelector('input');
              return !!(el && el.checked && el.type !== 'radio');
            }),
            pgOn: [...document.querySelectorAll('label')].some((lab) => {
              if (n(lab.innerText) !== 'PostgreSQL') return false;
              const el = (lab.htmlFor && document.getElementById(lab.htmlFor)) || lab.querySelector('input');
              return !!(el && el.checked && el.type !== 'radio');
            }),
            cssFwOn: [...document.querySelectorAll('input.js-custom-attribute, input[name*="custom_attribute_title"]')]
              .some((el) => /tailwind/i.test(el.value || ''))
              || [...document.querySelectorAll('label')].some((lab) => {
                const t = n(lab.innerText);
                if (!/tailwind|bootstrap|foundation|material/i.test(t)) return false;
                const el = (lab.htmlFor && document.getElementById(lab.htmlFor)) || lab.querySelector('input');
                return !!(el && el.checked && el.type !== 'radio');
              }),
            attrsOn: [...document.querySelectorAll('input[id^="attribute_item_"]:checked')].map((el) => el.id),
            attrsOnLabels: [...document.querySelectorAll('input[id^="attribute_item_"]:checked, input[name^="custom_attribute["]:checked')].map((el) => {
              const lab = document.querySelector('label[for="' + el.id + '"]');
              const title = el.name && String(el.name).indexOf('custom_attribute[') === 0
                ? ((document.querySelector('input[name="custom_attribute_title[' + el.value + ']"]') || {}).value || '')
                : '';
              return n((lab && lab.innerText) || title || el.id);
            }).filter(Boolean),
            customCss: [...document.querySelectorAll('input.js-custom-attribute, input[name*="custom_attribute_title"]')]
              .map((el) => n(el.value)).filter(Boolean),
            descHasBad: /канбан|fastapi|проде|github\\.com|alexklyvibe|testfullcrm/i.test(
              descVal + descText + instVal + instText
              + faqsParsed.map((x) => String((x && (x.answer || x.a || '')) || '')).join(' ')
            ),
            coverTouched: !!(document.querySelector('.js-save-portfolio.kw-button--loading, input[type="file"][name*="photo"]:focus')),
          };
        }"""
    )


def ids_from_href(href: str) -> dict:
    edit = re.search(r"edit\?id=(\d+)", href or "")
    draft = re.search(r"draft_id=(\d+)", href or "")
    kid = (edit.group(1) if edit else "") or (draft.group(1) if draft else "")
    return {
        "id": kid,
        "edit": f"https://kwork.ru/edit?id={kid}" if edit else "",
        "draft": f"https://kwork.ru/new?draft_id={kid}" if draft and not edit else "",
        "public": f"https://kwork.ru/{kid}" if edit else "",
    }


def save_current(page) -> dict:
    href = page.evaluate("() => location.href") or ""
    if href_forbidden(href):
        return {"ok": False, "err": "forbidden_live_kwork", "href": href}
    if "/edit?id=" in href:
        invoked = invoke_de(page)
        invoked["href"] = href
        return invoked
    if "/new" in href and f"draft_id={OUR_DRAFT}" not in href and f"/edit?id={OUR_DRAFT}" not in href:
        return {"ok": False, "err": "not_our_draft", "href": href}
    if "/new" in href:
        return page.evaluate(
            """() => {
              try {
                if (!window.KworkSaveModule || typeof KworkSaveModule.saveDraft !== 'function') {
                  return { ok: false, err: 'no_saveDraft' };
                }
                KworkSaveModule.saveDraft();
                return { ok: true, how: 'saveDraft' };
              } catch (e) {
                return { ok: false, err: String(e) };
              }
            }"""
        )
    return {"ok": False, "err": "unknown_url", "href": href}


def apply_faqs(page, items: list[dict]) -> dict:
    applied = apply_faqs_app(page, items)
    if not applied.get("ok"):
        applied = apply_faqs_vue(page, items)
    page.evaluate(
        """() => {
          const root = document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__;
          const vm = (window.appFaq && window.appFaq.$refs && window.appFaq.$refs.faq)
            || (root && (root.$children || []).find((c) => c.$options && c.$options.name === 'faq-list-editable'));
          if (!vm) return false;
          (vm.questions || []).forEach((q) => {
            if (q && q.isNew && q.id != null && typeof vm.saveQuestion === 'function') vm.saveQuestion(q.id);
          });
          if (typeof vm.change === 'function') vm.change();
          return true;
        }"""
    )
    return applied


def main() -> int:
    built = assemble(CRM)
    problems = validate(built) + buyer_copy_problems(built)
    if problems:
        print("FAIL validate", problems)
        return 1
    if [e["name"] for e in built["extras"]] != WANTED_EXTRAS:
        print("FAIL WANTED_EXTRAS mismatch")
        return 1
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    live = session_looks_live()
    headed = not live
    print("session_live", live, "headed", headed)
    browser = PlaywrightBrowserAdapter(
        storage_state_path="data/kwork_storage.json",
        headless=not headed,
    )
    posts: list[dict] = []
    captured = {"status": None, "text": "", "req": "", "url": ""}
    try:
        page = browser._ensure_page()
        browser.navigate(NEW_URL)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        try:
            page.wait_for_selector(
                "#step1-description, .js-kwork-title-editor, a[href='/login']",
                timeout=25000,
            )
        except Exception as exc:
            print("wait_sel", str(exc)[:200], page.evaluate("() => location.href"))
        page.wait_for_timeout(1500)
        href0 = page.evaluate("() => location.href") or ""
        if href_forbidden(href0):
            print("FAIL foreign draft/kwork", href0)
            return 1
        if "/new" in href0 and f"draft_id={OUR_DRAFT}" not in href0 and f"edit?id={OUR_DRAFT}" not in href0:
            print("WARN bare or other /new, returning to draft", href0)
            page.goto(NEW_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            href0 = page.evaluate("() => location.href") or ""
            if f"draft_id={OUR_DRAFT}" not in href0 and f"edit?id={OUR_DRAFT}" not in href0:
                print("FAIL not on our draft", href0)
                return 1
        logged = False
        for _ in range(4):
            try:
                logged = bool(is_logged_in(browser))
                break
            except Exception as exc:
                print("login_eval_retry", str(exc)[:160])
                page.wait_for_timeout(1500)
        if not logged:
            print("FAIL not logged in", href0)
            (LOG_DIR / "kwork_fullcrm_fill.png").write_bytes(browser.screenshot())
            return 1

        def on_req(req) -> None:
            if req.method != "POST":
                return
            url = req.url
            if "save_kwork" not in url and "draft_save" not in url:
                return
            raw = req.post_data or ""
            posts.append({"kind": "req", "url": url, "parsed": extras_from_post(raw)})
            captured["req"] = raw[:20000]
            captured["url"] = url

        def on_resp(resp) -> None:
            if "kwork.ru" not in resp.url or "yandex" in resp.url:
                return
            if resp.request.method != "POST":
                return
            body = ""
            try:
                body = resp.text()[:4000]
            except Exception:
                body = ""
            posts.append({"kind": "resp", "url": resp.url, "status": resp.status, "body": body})
            if "save_kwork" in resp.url or "draft_save" in resp.url:
                captured["text"] = body
                captured["status"] = resp.status
                captured["url"] = resp.url

        page.on("request", on_req)
        page.on("response", on_resp)
        page.on("dialog", lambda d: d.accept())
        print("landed", href0)
        try:
            page.wait_for_function("() => !!(window.jQuery || window.$ || window.De || window.KworkSaveModule)", timeout=30000)
        except Exception as exc:
            print("jq_wait", str(exc)[:200])
            print("FAIL: kwork app js not loaded")
            (LOG_DIR / "kwork_fullcrm_fill.png").write_bytes(browser.screenshot())
            return 1

        print("category", json.dumps(pick_category(page), ensure_ascii=True))
        try:
            page.wait_for_selector(TYPE_RADIO + ", label[for='attribute_item_5016']", timeout=15000)
        except Exception as exc:
            print("type_wait", str(exc)[:200])
        attrs0 = dump_attrs(page)
        (LOG_DIR / "kwork_fullcrm_attrs.json").write_text(
            json.dumps(attrs0, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("attrs0", json.dumps({
            "category_id": attrs0.get("category_id"),
            "typeOn": attrs0.get("typeOn"),
            "kindServiceOn": attrs0.get("kindServiceOn"),
            "kindLandingOn": attrs0.get("kindLandingOn"),
            "radioN": len(attrs0.get("radios") or []),
        }, ensure_ascii=True))
        print("type_radio", json.dumps(click_radio_label(page, "Новый сайт"), ensure_ascii=True))
        page.wait_for_timeout(800)
        kind = click_radio_label(page, "Интернет-сервис")
        if not kind.get("ok"):
            kind50 = page.evaluate(
                """() => {
                  const el = document.querySelector('#attribute_item_50');
                  if (!el) return { ok: false, reason: 'no_50' };
                  if (el.id === 'attribute_item_10') return { ok: false, reason: 'landing_kind' };
                  el.checked = true;
                  el.click();
                  const $ = window.jQuery;
                  if ($) $(el).prop('checked', true).trigger('change');
                  return { ok: !!el.checked, id: el.id };
                }"""
            )
            kind = kind50
        print("kind_radio", json.dumps(kind, ensure_ascii=True))
        if kind.get("id") == "attribute_item_10" or page.evaluate(f"() => !!(document.querySelector('{KIND_LANDING}')||{{}}).checked"):
            print("FAIL landing kind selected")
            return 1
        page.wait_for_timeout(800)
        print("cms", json.dumps(set_cms_custom(page), ensure_ascii=True))
        print("stack", json.dumps(set_js_css_db_stack(page), ensure_ascii=True))
        attrs1 = dump_attrs(page)
        (LOG_DIR / "kwork_fullcrm_attrs.json").write_text(
            json.dumps(attrs1, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("attrs1", json.dumps({
            "typeOn": attrs1.get("typeOn"),
            "kindServiceOn": attrs1.get("kindServiceOn"),
            "kindLandingOn": attrs1.get("kindLandingOn"),
            "radioN": len(attrs1.get("radios") or []),
            "priceN": len(attrs1.get("priceSels") or []),
        }, ensure_ascii=True))
        page.wait_for_timeout(400)

        print("title", json.dumps(fill_contentedit(page, ".js-kwork-title-editor", built["title"]), ensure_ascii=True))
        print("hid_title", json.dumps(set_hidden(page, "#step1-name", built["title"]), ensure_ascii=True))
        print("sync_title", json.dumps(sync_contentedit(page, ".js-kwork-title-editor"), ensure_ascii=True))
        print("price", json.dumps(fill_price_buyer(page, int(built["price_buyer"])), ensure_ascii=True))
        print("days", json.dumps(fill_work_time(page, built["days"]), ensure_ascii=True))
        print("desc", json.dumps(fill_desc_or_inst(page, "description", built["description"]), ensure_ascii=True))
        print("inst", json.dumps(fill_desc_or_inst(page, "instruction", built["instruction"]), ensure_ascii=True))
        print("size_fill", json.dumps(fill_size_volume(page, built["service_size"], built["volume"]), ensure_ascii=True)[:2000])
        print("hidden", json.dumps(dump_hidden(page), ensure_ascii=True)[:2000])

        extras = fill_extras_js(page, built["extras"])
        if not extras or not (extras[0].get("typed") or {}).get("nameText"):
            extras = fill_extras_playwright(page, built["extras"])
            pin_extra_inputs(page, built["extras"])
        print("extras", json.dumps(extras, ensure_ascii=True)[:2500])
        removed = delete_empty_extras(page)
        print("extra_removed", removed)
        print("tpl_disabled", disable_template_inputs(page))
        pin_extra_inputs(page, built["extras"])
        applied = apply_faqs(page, built["faqs"])
        qs = [x.get("question") for x in (applied.get("getData") or [])] or (applied.get("qs") or [])
        print("faq", json.dumps({"ok": applied.get("ok"), "qs": qs, "reason": applied.get("reason")}, ensure_ascii=True))
        if len(set(qs)) != 7:
            print("FAIL faq count", qs)
            (LOG_DIR / "kwork_fullcrm_fill.png").write_bytes(browser.screenshot())
            return 1
        page.wait_for_timeout(400)
        removed2 = delete_empty_extras(page)
        print("extra_removed2", removed2)
        pin_extra_inputs(page, built["extras"])
        print("desc2", json.dumps(fill_desc_or_inst(page, "description", built["description"]), ensure_ascii=True))
        pin_extra_inputs(page, built["extras"])
        print("cms2", json.dumps(set_cms_custom(page), ensure_ascii=True))
        print("stack2", json.dumps(set_js_css_db_stack(page), ensure_ascii=True))

        href = page.evaluate("() => location.href") or ""
        if href_forbidden(href):
            print("FAIL foreign url before save", href)
            return 1
        if not href_ok(href):
            print("FAIL wrong url before save", href)
            return 1
        filled = persist_dump(page)
        if int(filled.get("descLen") or 0) < 100 or not (filled.get("title") or filled.get("titleHidden")):
            print("FAIL empty form, skip save", json.dumps(filled, ensure_ascii=True)[:1500])
            return 1
        hook_save_form(page)
        page.evaluate(
            """() => {
              if (window.axios && axios.interceptors && !window.__axHooked) {
                window.__axHooked = true;
                axios.interceptors.response.use((r) => {
                  const u = String((r.config && r.config.url) || '');
                  if (u.indexOf('draft_save') >= 0 || u.indexOf('save_kwork') >= 0) {
                    window.__saveResp = r.data;
                    try { window.__saveRespRaw = JSON.stringify(r.data || ''); } catch (e) {}
                  }
                  return r;
                });
              }
              const btn = document.querySelector('.js-wrap-save-btn-kwork .js-save-kwork') || document.querySelector('.js-save-kwork');
              if (!btn) return;
              btn.classList.remove('disabled', 'js-uploader-button-disable');
              btn.disabled = false;
              const $ = window.jQuery || window.$;
              if ($) $(btn).prop('disabled', false).removeClass('disabled js-uploader-button-disable').data('loading', false);
            }"""
        )
        captured["status"] = None
        captured["text"] = ""
        captured["req"] = ""
        captured["url"] = ""
        page.wait_for_timeout(3000)
        saved = {"ok": False}
        invoked = {"ok": False}
        try:
            with page.expect_response(lambda r: "save_kwork" in r.url or "draft_save" in r.url, timeout=25000) as resp_info:
                invoked = save_current(page)
                print("save_invoke", json.dumps(invoked, ensure_ascii=True))
                if not invoked.get("ok"):
                    raise RuntimeError(invoked.get("err") or "save_invoke_fail")
            resp = resp_info.value
            saved = {"ok": True, "how": invoked.get("how"), "status": resp.status, "url": resp.url}
        except Exception as exc:
            page.wait_for_timeout(2500)
            invoked = save_current(page)
            print("save_retry", json.dumps(invoked, ensure_ascii=True))
            page.wait_for_timeout(3000)
            if captured.get("status") == 200 and ("draft_save" in (captured.get("url") or "") or "save_kwork" in (captured.get("url") or "")):
                saved = {"ok": True, "how": invoked.get("how") or "saveDraft", "status": captured["status"], "url": captured["url"]}
            else:
                saved = {"ok": False, "how": "expect_fail", "err": str(exc)[:400]}
        page.wait_for_timeout(2500)
        href_after = page.evaluate("() => location.href") or ""
        if "/edit?id=" in href_after and saved.get("how") == "saveDraft":
            print("WARN landed edit after saveDraft, not calling saveDraft again")
        if href_forbidden(href_after):
            print("FAIL foreign url after save", href_after)
            return 1
        if "/edit?id=" in href_after and "draft_save" in (saved.get("url") or "") and saved.get("how") != "De(false)":
            print("WARN draft_save while on edit, skip second save")
        ajax_resp = page.evaluate(
            """() => ({
              data: window.__saveResp,
              raw: String(window.__saveRespRaw || '').slice(0, 4000),
              gfdExtras: window.__gfdExtras || null,
              href: location.href,
            })"""
        )
        print("save", json.dumps(saved, ensure_ascii=True))
        print("ajax_resp", json.dumps(ajax_resp, ensure_ascii=True)[:2500])
        req_extras = extras_from_post(captured.get("req") or "")
        js = None
        raw_text = captured.get("text") or ajax_resp.get("raw") or ""
        if str(raw_text).strip().startswith("{") or str(raw_text).strip().startswith("["):
            try:
                js = json.loads(raw_text)
            except Exception:
                js = None
        if js is None and isinstance(ajax_resp.get("data"), dict):
            js = ajax_resp.get("data")
        Path("logs/kwork_fullcrm_save.json").write_text(
            json.dumps(
                {
                    "save": saved,
                    "captured": {k: (v[:8000] if isinstance(v, str) else v) for k, v in captured.items()},
                    "post_extras": req_extras,
                    "ajax_resp": ajax_resp,
                    "js": js,
                    "href_after": href_after,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not saved.get("ok"):
            print("FAIL save how", saved)
            (LOG_DIR / "kwork_fullcrm_fill.png").write_bytes(browser.screenshot())
            return 1
        if "/edit?id=" in href_after and saved.get("how") == "saveDraft":
            pass
        elif "/new" in href_after and saved.get("how") != "saveDraft":
            print("FAIL expected saveDraft on /new", saved, href_after)
            return 1
        if "/edit?id=" in (saved.get("href") or href) and saved.get("how") not in {"De(false)", None} and "draft_save" in (saved.get("url") or ""):
            print("FAIL used draft_save on edit", saved)
            return 1

        ids = ids_from_href(href_after)
        reload_url = ids["edit"] or ids["draft"] or href_after
        if href_forbidden(reload_url):
            print("FAIL foreign reload", reload_url)
            return 1
        page.goto(reload_url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        page.wait_for_selector("#step1-description, .js-kwork-title-editor", timeout=25000)
        page.wait_for_timeout(4000)
        verify = persist_dump(page)
        print("verify", json.dumps(verify, ensure_ascii=True)[:5000])
        Path("logs/kwork_fullcrm_reload.json").write_text(
            json.dumps(verify, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (LOG_DIR / "kwork_fullcrm_verify.png").write_bytes(browser.screenshot())
        extras_my = list(verify.get("extras") or [])
        faq_qs = list(verify.get("faqsJson") or [])
        print("extras.my", json.dumps(extras_my, ensure_ascii=True))
        print("faqsJson", json.dumps(faq_qs, ensure_ascii=True))
        ids = ids_from_href(str(verify.get("url") or reload_url))
        print("edit_url", ids["edit"] or verify.get("url"))
        print("public_url", ids["public"] or "")
        print("id", ids["id"])
        desc_ok = 100 <= int(verify.get("descLen") or 0) <= 1200 and not verify.get("descHasBad")
        extras_ok = extras_my == WANTED_EXTRAS
        faq_ok = len(set(faq_qs)) == 7 and any("пример" in x.lower() for x in faq_qs)
        kind_ok = bool(verify.get("kindServiceOn")) and not verify.get("kindLandingOn")
        cms_ok = bool(verify.get("cmsOn")) and "attribute_item_730" in (verify.get("attrsOn") or [])
        cover_ok = not verify.get("coverTouched")
        stack_ok = bool(
            verify.get("reactOn")
            and verify.get("cssFwOn")
            and verify.get("pgOn")
            and verify.get("jsYes")
            and verify.get("cssYes")
            and verify.get("dbOn")
        )
        ok = desc_ok and extras_ok and faq_ok and kind_ok and cms_ok and cover_ok and stack_ok
        print("attrsOn", json.dumps(verify.get("attrsOn"), ensure_ascii=True))
        print("attrsOnLabels", json.dumps(verify.get("attrsOnLabels"), ensure_ascii=True))
        print("customCss", json.dumps(verify.get("customCss"), ensure_ascii=True))
        print("accept", json.dumps({
            "desc_ok": desc_ok,
            "extras_ok": extras_ok,
            "faq_ok": faq_ok,
            "kind_ok": kind_ok,
            "cms_ok": cms_ok,
            "cover_ok": cover_ok,
            "stack_ok": stack_ok,
            "jsYes": verify.get("jsYes"),
            "reactOn": verify.get("reactOn"),
            "cssYes": verify.get("cssYes"),
            "cssFwOn": verify.get("cssFwOn"),
            "dbOn": verify.get("dbOn"),
            "pgOn": verify.get("pgOn"),
            "pythonOn": verify.get("pythonOn"),
            "descHasBad": verify.get("descHasBad"),
            "save_how": saved.get("how"),
            "id": ids["id"],
            "edit": ids["edit"] or verify.get("url"),
            "public": ids["public"],
        }, ensure_ascii=True))
        return 0 if ok else 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
