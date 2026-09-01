from __future__ import annotations

import json
import re
from pathlib import Path

from deploy._fill_kwork_new_draft import _pick_chosen, buyer_to_select_value, fill_extras_playwright
from deploy._fix_kwork_faq4 import apply_faqs_vue
from deploy.kwork_listing_landing import LANDING
from deploy.kwork_listing_skel import assemble, to_kwork_html, validate
from src.adapters.kwork_auth import is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

EDIT_URL = "https://kwork.ru/edit?id=51315832"
TYPE_RADIO = "#attribute_item_5016"
KIND_RADIO = "#attribute_item_10"
WANTED_EXTRAS = [
    "Второй язык: русский и английский",
    "Форма заявок на почту",
    "Бот обновления карточек проектов",
    "Серверная база и АПИ секций",
    "Контейнеры и выкладка на сервер",
    "Ещё одна страница на том же стеке",
    "Срочная вёрстка в приоритете",
]
LOG_DIR = Path("logs")


def _p_inner_texts(html: str) -> list[str]:
    return [m.strip() for m in re.findall(r"<p>(.*?)</p>", html, flags=re.I | re.S) if m.strip()]


def write_textarea(page, selector: str, text: str) -> dict:
    html = text if "<p>" in text else to_kwork_html(text)
    return page.evaluate(
        """({ sel, html }) => {
          const el = document.querySelector(sel);
          if (!el) return { ok: false, reason: 'missing', sel };
          el.disabled = false;
          el.removeAttribute('disabled');
          const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          const setVal = (v) => {
            if (desc && desc.set) desc.set.call(el, v);
            else el.value = v;
          };
          setVal(html);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          const $ = window.jQuery;
          if ($) $(el).prop('disabled', false).trigger('input').trigger('change');
          const box = el.closest('.trumbowyg-box');
          const editor = box && box.querySelector('.trumbowyg-editor');
          if (editor) {
            editor.innerHTML = html;
            editor.dispatchEvent(new Event('input', { bubbles: true }));
            setVal(html);
            el.dispatchEvent(new Event('input', { bubbles: true }));
          }
          const val = String(el.value || '');
          const p = (val.match(/<p[\\s>]/gi) || []).length;
          const textLen = editor
            ? String(editor.innerText || '').trim().length
            : val.replace(/<[^>]+>/g, '').trim().length;
          return {
            ok: p >= 1 && textLen >= 100 && val.length >= 100,
            sel,
            p,
            textLen,
            len: val.length,
            valIsHtml: /<p[\\s>]/i.test(val),
            head: val.slice(0, 80),
          };
        }""",
        {"sel": selector, "html": html},
    )


def fill_desc_or_inst(page, name: str, text: str) -> dict:
    html = text if "<p>" in text else to_kwork_html(text)
    ta = f"#step1-{name}" if name in {"description", "instruction"} else f"textarea[name='{name}']"
    min_p = 6 if name == "description" else 4
    written = write_textarea(page, ta, html)
    if int(written.get("p") or 0) >= min_p and written.get("valIsHtml") and int(written.get("textLen") or 0) >= 100:
        return {"how": "setter+innerHTML", **written}
    box = (
        page.locator(f"textarea[name='{name}']")
        .locator("xpath=ancestor::*[contains(@class,'trumbowyg-box')][1]")
        .locator(".trumbowyg-editor")
    )
    how = "setter+innerHTML"
    if box.count() and box.first.is_visible():
        try:
            box.first.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            blocks = _p_inner_texts(html)
            for i, block in enumerate(blocks):
                page.keyboard.insert_text(block)
                if i < len(blocks) - 1:
                    page.keyboard.press("Enter")
            page.wait_for_timeout(150)
            how = "paragraphs+setter"
        except Exception:
            how = "setter+innerHTML"
    written = write_textarea(page, ta, html)
    return {"how": how, **written}


def insert_text_field(page, selector: str, text: str) -> dict:
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=15000)
    loc.click()
    page.wait_for_timeout(150)
    inserted = page.evaluate(
        """({sel, text}) => {
          const el = document.querySelector(sel);
          if (!el) return { ok: false, reason: 'missing' };
          el.focus();
          if (typeof el.select === 'function') el.select();
          else document.execCommand('selectAll', false, null);
          const ok = document.execCommand('insertText', false, text);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          const $ = window.jQuery;
          if ($ && el) $(el).trigger('input').trigger('change');
          const v = el.value || '';
          return { ok: !!ok, len: v.length, html: /<[a-z][\\s/>]/i.test(v), head: v.slice(0, 60) };
        }""",
        {"sel": selector, "text": text},
    )
    if not inserted.get("ok") or (inserted.get("len") or 0) < min(40, len(text)):
        loc.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(text)
        inserted = page.evaluate(
            """(sel) => {
              const el = document.querySelector(sel);
              const v = (el && el.value) || '';
              return { ok: true, how: 'keyboard.insert_text', len: v.length, html: /<[a-z][\\s/>]/i.test(v), head: v.slice(0, 60) };
            }""",
            selector,
        )
    return inserted


def fill_contentedit(page, selector: str, text: str) -> dict:
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=15000)
    loc.click()
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(text)
    page.wait_for_timeout(120)
    return page.evaluate(
        """(sel) => {
          const el = document.querySelector(sel);
          const t = ((el && (el.innerText || el.textContent)) || '').replace(/\\s+/g, ' ').trim();
          return { ok: !!t, text: t };
        }""",
        selector,
    )


def sync_contentedit(page, selector: str) -> dict:
    return page.evaluate(
        """(sel) => {
          const el = document.querySelector(sel);
          if (!el) return { ok: false };
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          el.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
          const $ = window.jQuery;
          if ($) $(el).trigger('input').trigger('change').trigger('blur');
          const wrap = el.parentElement;
          const storage = wrap && wrap.querySelector('.js-content-storage');
          return { ok: true, text: ((el.innerText || '')).trim().slice(0, 80), storage: storage ? String(storage.value || '').slice(0, 80) : null };
        }""",
        selector,
    )


def dump_hidden(page) -> dict:
    return page.evaluate(
        """() => {
          const extra = document.querySelector('.add-extra__item-name-input');
          const chain = [];
          let node = extra;
          while (node && node !== document.body && chain.length < 8) {
            const cs = getComputedStyle(node);
            chain.push({
              tag: node.tagName,
              cls: String(node.className || '').slice(0, 90),
              d: cs.display,
              v: cs.visibility,
              h: Math.round(node.getBoundingClientRect().height),
            });
            node = node.parentElement;
          }
          const titles = [...document.querySelectorAll('.kwork-save-step__title, h2, h3')]
            .map((el) => (el.innerText || '').replace(/\\s+/g, ' ').trim())
            .filter(Boolean)
            .slice(0, 24);
          const faqNodes = [...document.querySelectorAll('[id*="faq"], [class*="faq"]')].slice(0, 16).map((el) => ({
            id: el.id,
            cls: String(el.className || '').slice(0, 90),
          }));
          return {
            extraChain: chain,
            addBtn: !!(document.querySelector('a.js-extra-add-btn')),
            extraCount: document.querySelectorAll('.add-extra__item-name-input').length,
            faq: {
              el: !!document.querySelector('#app-faq'),
              appFaq: typeof window.appFaq,
              add: !!document.querySelector('.faq-list-editable__add'),
              nodes: faqNodes,
            },
            titles,
          };
        }"""
    )


def open_steps(page) -> list:
    clicks = page.evaluate(
        """() => {
          const out = [];
          [...document.querySelectorAll('.kwork-save-step')].forEach((step) => {
            const content = step.querySelector('.kwork-save-step__content');
            const hidden = content && (getComputedStyle(content).display === 'none' || content.offsetHeight < 20);
            const header = step.querySelector('.kwork-save-step__header');
            if (hidden && header) {
              header.click();
              out.push('header:' + ((header.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 40)));
            }
          });
          [...document.querySelectorAll('a, button, .kw-button, .js-kwork-save-next-step')].forEach((el) => {
            const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (/^продолжить$/i.test(t) || ( /продолжить/i.test(t) && t.length < 24)) {
              el.click();
              out.push('next:' + t);
            }
          });
          return out;
        }"""
    )
    page.wait_for_timeout(700)
    return clicks


def set_hidden(page, selector: str, value: str) -> dict:
    return page.evaluate(
        """({ sel, value }) => {
          const el = document.querySelector(sel);
          if (!el) return { ok: false };
          el.value = value;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          const $ = window.jQuery;
          if ($) $(el).trigger('input').trigger('change');
          return { ok: true, value: String(el.value || '').slice(0, 80) };
        }""",
        {"sel": selector, "value": value},
    )


def dump_size_dom(page) -> dict:
    return page.evaluate(
        """() => {
          const info = (el) => {
            if (!el) return null;
            const cs = getComputedStyle(el);
            const p = el.closest('.kwork-save-step__field, .js-field-block, tr, td, .kwork-save-step__content, [class*=\"service-size\"], [id*=\"service-size\"], [id*=\"volume\"]');
            return {
              tag: el.tagName,
              id: el.id,
              name: el.getAttribute('name'),
              cls: String(el.className || '').slice(0, 120),
              val: String(el.value || el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
              disabled: !!el.disabled,
              display: cs.display,
              vis: cs.visibility,
              h: Math.round(el.getBoundingClientRect().height),
              w: Math.round(el.getBoundingClientRect().width),
              parentHidden: p ? p.classList.contains('hidden') : null,
              parentDisp: p ? getComputedStyle(p).display : null,
            };
          };
          const labels = [...document.querySelectorAll('label, .kwork-save-step__field-label, .kwork-save-step__field-title')]
            .map((el) => (el.innerText || '').replace(/\\s+/g, ' ').trim())
            .filter((t) => /объем|услуг в 1|service.size|что входит/i.test(t))
            .slice(0, 8);
          return {
            editor: info(document.querySelector('.js-field-input-service-size-editor')),
            storage: info(document.querySelector('#step2-service-size, [name=\"service_size\"]')),
            volume: info(document.querySelector('#step2-volume, [name=\"volume\"]')),
            pkgVol: info(document.querySelector('[name=\"package_volume\"]')),
            bundleDesc: info(document.querySelector('[name=\"bundle_standard_description\"]')),
            block: info(document.querySelector('#service-size-block, #volume-block')),
            labels,
          };
        }"""
    )


def fill_size_volume(page, size_text: str, volume: str) -> dict:
    primed = page.evaluate(
        """({ text, vol }) => {
          const putEditor = (sel, value) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            el.disabled = false;
            el.focus();
            el.textContent = value;
            el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value }));
            el.dispatchEvent(new Event('keyup', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            const wrap = el.parentElement;
            const storage = wrap && wrap.querySelector('.js-content-storage, textarea[name], input[name]');
            if (storage) {
              storage.disabled = false;
              storage.value = value;
              storage.dispatchEvent(new Event('input', { bubbles: true }));
              storage.dispatchEvent(new Event('change', { bubbles: true }));
            }
            const r = el.getBoundingClientRect();
            return { sel, text: (el.innerText || '').trim(), h: Math.round(r.height), w: Math.round(r.width), storage: storage ? storage.name : null };
          };
          const setVal = (sel, value) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            el.disabled = false;
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            const $ = window.jQuery;
            if ($) $(el).prop('disabled', false).val(value).trigger('input').trigger('change');
            return { sel, value: String(el.value || '') };
          };
          return {
            bundle: putEditor('#editor-bundle-standard-description', text),
            bundleTa: setVal('#bundle-standard-description, [name=\"bundle_standard_description\"]', text),
            editor: putEditor('.js-field-input-service-size-editor', text),
            storage: setVal('#step2-service-size, [name=\"service_size\"]', text),
            volume: setVal('#step2-volume, [name=\"volume\"]', vol),
            pkgVol: setVal('[name=\"package_volume\"]', vol),
          };
        }""",
        {"text": size_text, "vol": volume},
    )
    bundle = page.locator("#editor-bundle-standard-description").first
    if bundle.count():
        try:
            bundle.click(timeout=4000)
            page.keyboard.press("Control+A")
            page.keyboard.insert_text(size_text)
            page.wait_for_timeout(150)
            primed["bundle_click"] = "ok"
        except Exception as exc:
            primed["bundle_click"] = str(exc)[:160]
    page.evaluate(
        """({ text, vol }) => {
          const $ = window.jQuery;
          const ta = document.querySelector('#bundle-standard-description') || document.querySelector('[name=\"bundle_standard_description\"]');
          if (ta) { ta.disabled = false; ta.value = text; if ($) $(ta).val(text).trigger('input').trigger('change'); }
          const storage = document.querySelector('#step2-service-size');
          if (storage) { storage.disabled = false; storage.value = text; }
          const volumeEl = document.querySelector('#step2-volume');
          if (volumeEl) { volumeEl.disabled = false; volumeEl.value = vol; }
          const pkg = document.querySelector('[name=\"package_volume\"]');
          if (pkg) { pkg.disabled = false; pkg.value = vol; if ($) $(pkg).val(vol).trigger('input').trigger('change'); }
        }""",
        {"text": size_text, "vol": volume},
    )
    primed["after"] = dump_size_dom(page)
    primed["bundleText"] = page.evaluate(
        "() => ((document.querySelector('#editor-bundle-standard-description') || {}).innerText || '').trim()"
    )
    return primed


def expand_faq_step(page) -> dict:
    return page.evaluate(
        """() => {
          const step = document.querySelector('.js-step-faq');
          if (!step) return { ok: false, reason: 'no_step' };
          const content = step.querySelector('.kwork-save-step__content');
          const hidden = content && (getComputedStyle(content).display === 'none' || content.offsetHeight < 20);
          const header = step.querySelector('.kwork-save-step__header');
          if (hidden && header) header.click();
          const app = document.querySelector('#app-faq');
          return {
            ok: true,
            clicked: !!hidden,
            htmlLen: app ? (app.innerHTML || '').length : 0,
            vue: !!(app && app.__vue__),
            appFaq: typeof window.appFaq,
          };
        }"""
    )


def fill_extras_js(page, extras: list[dict]) -> list[dict]:
    print("extras_probe", json.dumps(page.evaluate(
        """() => {
          const btns = [...document.querySelectorAll('a.js-extra-add-btn')].map((el) => ({
            href: el.getAttribute('href'),
            cls: el.className,
            t: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 50),
            html: el.outerHTML.slice(0, 240),
          }));
          const extrasRoot = document.querySelector('.js-my-extras');
          const children = extrasRoot ? [...extrasRoot.children].map((el) => ({
            tag: el.tagName,
            cls: String(el.className || '').slice(0, 80),
            d: getComputedStyle(el).display,
            html: el.outerHTML.slice(0, 160),
          })) : [];
          const $ = window.jQuery || window.$;
          return {
            btns,
            children,
            jq: typeof window.jQuery,
            dollar: typeof window.$,
            hasDollarFn: typeof $ === 'function',
            extDisplay: (document.querySelector('.kwork-save-step__second-step_ext') && getComputedStyle(document.querySelector('.kwork-save-step__second-step_ext')).display),
          };
        }"""
    ), ensure_ascii=True)[:2500])
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
          return {
            before,
            after: live().length,
            extDisplay: ext ? getComputedStyle(ext).display : null,
            usedJq: !!$,
          };
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
        href = ""
        try:
            href = page.evaluate("() => location.href")
        except Exception as exc:
            print("href_retry", str(exc)[:160])
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(800)
            href = page.evaluate("() => location.href")
        if "edit?id=51315832" not in (href or ""):
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
                const storage = (row && fieldName && row.querySelector('[name=\"' + fieldName + '\"]'))
                  || (el.parentElement && el.parentElement.querySelector('.js-content-storage'));
                if (storage) {
                  storage.disabled = false;
                  storage.value = text;
                  storage.dispatchEvent(new Event('input', { bubbles: true }));
                  storage.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return { ok: !!ok, storage: storage ? storage.name : null, storageVal: storage ? String(storage.value || '').slice(0, 40) : null };
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
                postedName: (names[i] && names[i].closest('.js-add-extra-row') && (names[i].closest('.js-add-extra-row').querySelector('[name=\"my_extras_name[]\"]') || {}).value) || '',
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
    page.evaluate(
        """(items) => {
          const live = (sel) => [...document.querySelectorAll(sel)]
            .filter((el) => !el.closest('.js-my-extras-templates__item'));
          const names = live('input[name=\"my_extras_name[]\"]');
          const hints = live('input[name=\"my_extras_description[]\"], textarea[name=\"my_extras_description[]\"]');
          const prices = live('select.js-extra-price');
          const durs = live('select.js-extra-duration');
          const $ = window.jQuery;
          items.forEach((ex, i) => {
            if (names[i]) { names[i].disabled = false; names[i].value = ex.name; }
            if (hints[i]) { hints[i].disabled = false; hints[i].value = ex.hint; }
            if (prices[i]) {
              const v = String(ex.priceSelect);
              if ($) $(prices[i]).val(v).trigger('chosen:updated').trigger('change');
              else prices[i].value = v;
            }
            if (durs[i]) {
              if ($) $(durs[i]).val(String(ex.days)).trigger('chosen:updated').trigger('change');
              else durs[i].value = String(ex.days);
            }
          });
        }""",
        [
            {
                "name": e["name"],
                "hint": e["hint"],
                "priceSelect": buyer_to_select_value(e["price"]),
                "days": e["days"],
            }
            for e in extras
        ],
    )
    return results


def delete_empty_extras(page) -> int:
    removed = 0
    for _ in range(8):
        idx = page.evaluate(
            """() => {
              const names = [...document.querySelectorAll('.add-extra__item-name-input')]
                .filter((el) => !el.closest('.js-my-extras-templates__item'));
              for (let i = names.length - 1; i >= 0; i--) {
                if (!(names[i].innerText || '').trim()) return i;
              }
              return -1;
            }"""
        )
        if idx < 0:
            break
        ok = page.evaluate(
            """(i) => {
              const dels = [...document.querySelectorAll('.js-add-extra-delete')]
                .filter((el) => !el.closest('.js-my-extras-templates__item'));
              const del = dels[i];
              if (!del) return false;
              del.click();
              return true;
            }""",
            idx,
        )
        if not ok:
            print("extra_del_skip", idx)
            break
        page.wait_for_timeout(400)
        removed += 1
    return removed


def apply_faqs(page, items: list[dict]) -> dict:
    print("faq_step", json.dumps(expand_faq_step(page), ensure_ascii=True))
    for _ in range(8):
        state = page.evaluate(
            """() => ({
              vue: !!(document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__),
              appFaq: typeof window.appFaq,
              html: ((document.querySelector('#app-faq') || {}).innerHTML || '').length,
            })"""
        )
        print("faq_wait", json.dumps(state, ensure_ascii=True))
        if state.get("vue") or state.get("appFaq") == "object" or (state.get("html") or 0) > 20:
            break
        page.wait_for_timeout(500)
    applied = apply_faqs_vue(page, items)
    if not applied.get("ok"):
        page.evaluate(
            """() => {
              const nodes = [...document.querySelectorAll('a, button, span, div')].filter((el) => {
                const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                return /добавить вопрос/i.test(t) && t.length < 40;
              });
              if (nodes[0]) nodes[0].click();
              const add = document.querySelector('.faq-list-editable__add, .faq-list-editable__add-question');
              if (add) add.click();
            }"""
        )
        page.wait_for_timeout(1000)
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


def save_edit(page) -> dict:
    page.wait_for_timeout(500)
    return page.evaluate(
        """() => {
          try {
            const $ = window.jQuery || window.$;
            const i = $ && $('.js-save-kwork');
            if (i && i.length) i.prop('disabled', false).removeClass('disabled');
            if (typeof De === 'function') {
              De(false);
              return { ok: true, how: 'De(false)' };
            }
            const btn = document.querySelector('.js-wrap-save-btn-kwork .js-save-kwork, button.js-save-kwork, .js-save-kwork');
            if (btn) {
              btn.classList.remove('disabled');
              btn.disabled = false;
            }
            if (i && i.length) {
              i.trigger('click');
              return { ok: true, how: 'jq_green_save' };
            }
            if (btn) {
              btn.click();
              return { ok: true, how: 'dom_green_save' };
            }
            return { ok: false, err: 'no_De_no_btn', keys: Object.keys(window).filter((k) => /save|Save|De/i.test(k)).slice(0, 20) };
          } catch (e) {
            return { ok: false, err: String(e) };
          }
        }"""
    )


def dump_form(page) -> dict:
    return page.evaluate(
        """() => {
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const strip = (s) => n((s || '').replace(/<[^>]+>/g, ' '));
          const faqs = [...document.querySelectorAll('.faq-list-editable__question-text-formatted')].map((el) => n(el.textContent));
          const extras = [...document.querySelectorAll('.add-extra__item-name-input')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'))
            .map((el) => n(el.textContent));
          let faqAs = [];
          try {
            const root = document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__;
            const vm = (window.appFaq && window.appFaq.$refs && window.appFaq.$refs.faq)
              || (root && (root.$children || []).find((c) => c.$options && c.$options.name === 'faq-list-editable'));
            if (vm && vm.getData) faqAs = vm.getData().map((x) => n(x.answer || ''));
          } catch (e) {}
          const desc = document.querySelector('#step1-description');
          const inst = document.querySelector('#step1-instruction');
          const descTrum = document.querySelector("textarea[name='description']")
            && document.querySelector("textarea[name='description']").closest('.trumbowyg-box')
            && document.querySelector("textarea[name='description']").closest('.trumbowyg-box').querySelector('.trumbowyg-editor');
          const instTrum = document.querySelector("textarea[name='instruction']")
            && document.querySelector("textarea[name='instruction']").closest('.trumbowyg-box')
            && document.querySelector("textarea[name='instruction']").closest('.trumbowyg-box').querySelector('.trumbowyg-editor');
          const typeEl = document.querySelector('#attribute_item_5016');
          const kindEl = document.querySelector('#attribute_item_10');
          const prices = [...document.querySelectorAll('select.js-extra-price')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'))
            .map((el) => el.value);
          const days = [...document.querySelectorAll('select.js-extra-duration')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'))
            .map((el) => el.value);
          const extraPosted = [...document.querySelectorAll('input[name=\"my_extras_name[]\"]')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'))
            .map((el) => n(el.value))
            .filter(Boolean);
          const descVal = desc ? String(desc.value || '') : '';
          const instVal = inst ? String(inst.value || '') : '';
          const descText = descTrum ? n(descTrum.innerText) : strip(descVal);
          const instText = instTrum ? n(instTrum.innerText) : strip(instVal);
          return {
            url: location.href,
            title: n((document.querySelector('.js-kwork-title-editor') || {}).innerText || ''),
            titleHidden: n((document.querySelector('#step1-name') || {}).value || ''),
            sizeHidden: n((document.querySelector('#step2-service-size, [name="service_size"]') || {}).value || ''),
            bundleSize: n((document.querySelector('#editor-bundle-standard-description') || {}).innerText || '')
              || n((document.querySelector('[name="bundle_standard_description"]') || {}).value || ''),
            typeOn: !!(typeEl && typeEl.checked),
            kindOn: !!(kindEl && kindEl.checked),
            descLen: Math.max(descText.length, strip(descVal).length),
            instLen: Math.max(instText.length, strip(instVal).length),
            descValHead: strip(descVal).slice(0, 180),
            instValHead: strip(instVal).slice(0, 180),
            descHasNext: /Next\\.js/i.test(descVal + descText),
            descHasPromo: /запустить акцию|поменять блок/i.test(descVal + descText),
            instHasPrishlite: /Пришлите/i.test(instVal + instText),
            instHasOptions: /опции/i.test(instVal + instText),
            size: n((document.querySelector('.js-field-input-service-size-editor') || {}).innerText || ''),
            volume: ((document.querySelector('#step2-volume, [name="volume"]') || {}).value) || '',
            extras,
            extraFilled: extras.filter(Boolean),
            extraPosted,
            faqs,
            faqAs,
            prices,
            extraDays: days,
          };
        }"""
    )


def main() -> int:
    built = assemble(LANDING)
    problems = validate(built)
    if problems:
        print("FAIL validate", problems)
        return 1
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    browser = PlaywrightBrowserAdapter(
        storage_state_path="data/kwork_storage.json",
        headless=False,
    )
    posts: list[dict] = []
    try:
        page = browser._ensure_page()
        browser.navigate(EDIT_URL)
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
        logged = False
        for _ in range(4):
            try:
                logged = bool(is_logged_in(browser))
                break
            except Exception as exc:
                print("login_eval_retry", str(exc)[:160])
                page.wait_for_timeout(1500)
        if not logged:
            print("FAIL: not logged in", page.evaluate("() => location.href"))
            (LOG_DIR / "kwork_landing_fill.png").write_bytes(browser.screenshot())
            return 1

        def on_req(req) -> None:
            if req.method != "POST" or "save_kwork" not in req.url:
                return
            raw = req.post_data or ""
            keys = []
            size_val = vol_val = ""
            for part in raw.split("&"):
                if "=" not in part:
                    continue
                k, _, v = part.partition("=")
                keys.append(k)
                if k == "service_size":
                    size_val = v[:200]
                elif k == "volume":
                    vol_val = v[:40]
            posts.append({
                "kind": "req",
                "url": req.url,
                "keys": [k for k in keys if "size" in k or "volume" in k or k in {"title", "work_time"}],
                "service_size": size_val,
                "volume": vol_val,
                "nkeys": len(keys),
            })

        def on_resp(resp) -> None:
            if "kwork.ru" not in resp.url or "yandex" in resp.url:
                return
            if resp.request.method != "POST":
                return
            body = ""
            try:
                body = resp.text()[:500]
            except Exception:
                body = ""
            posts.append({"kind": "resp", "url": resp.url, "status": resp.status, "body": body})

        page.on("request", on_req)
        page.on("response", on_resp)
        page.on("dialog", lambda d: d.accept())
        print("landed", page.evaluate("() => location.href"))
        print("attrs", page.evaluate(
            """() => ({
              type: !!(document.querySelector('#attribute_item_5016') || {}).checked,
              kind: !!(document.querySelector('#attribute_item_10') || {}).checked,
              trum: document.querySelectorAll('.trumbowyg-editor').length,
              descId: !!(document.querySelector('#step1-description')),
            })"""
        ))

        print("globals", json.dumps(page.evaluate(
            """() => ({
              jq: typeof window.jQuery,
              dollar: typeof window.$,
              De: typeof window.De,
              appFaq: typeof window.appFaq,
              faqsJsonType: typeof window.faqsJson,
              faqsJson: window.faqsJson,
              extraKeys: window.predefinedExtraOptions ? Object.keys(window.predefinedExtraOptions).slice(0, 12) : null,
            })"""
        ), ensure_ascii=True)[:2000])
        try:
            page.wait_for_function("() => !!(window.jQuery || window.$ || window.De)", timeout=30000)
            print("js_ready", page.evaluate(
                """() => ({ jq: typeof window.jQuery, dollar: typeof window.$, De: typeof window.De })"""
            ))
        except Exception as exc:
            print("jq_wait", str(exc)[:200])
            print("FAIL: kwork app js not loaded")
            (LOG_DIR / "kwork_landing_fill.png").write_bytes(browser.screenshot())
            return 1

        print("size_dom0", json.dumps(dump_size_dom(page), ensure_ascii=True)[:2500])
        print("title", json.dumps(fill_contentedit(page, ".js-kwork-title-editor", built["title"]), ensure_ascii=True))
        print("desc", json.dumps(fill_desc_or_inst(page, "description", built["description"]), ensure_ascii=True))
        print("inst", json.dumps(fill_desc_or_inst(page, "instruction", built["instruction"]), ensure_ascii=True))
        print("size_fill", json.dumps(fill_size_volume(page, built["service_size"], built["volume"]), ensure_ascii=True)[:2500])
        print("sync_title", json.dumps(sync_contentedit(page, ".js-kwork-title-editor"), ensure_ascii=True))
        print("hid_title", json.dumps(set_hidden(page, "#step1-name", built["title"]), ensure_ascii=True))
        print("hidden", json.dumps(dump_hidden(page), ensure_ascii=True)[:2500])
        extras = fill_extras_js(page, built["extras"])
        if not extras or not (extras[0].get("typed") or {}).get("nameText"):
            extras = fill_extras_playwright(page, built["extras"])
        print("extras", json.dumps(extras, ensure_ascii=True)[:2500])
        removed = delete_empty_extras(page)
        print("extra_removed", removed)
        print("hidden2", json.dumps(dump_hidden(page).get("faq"), ensure_ascii=True)[:1500])
        applied = apply_faqs(page, built["faqs"])
        qs = [x.get("question") for x in (applied.get("getData") or [])]
        print("faq", json.dumps({"ok": applied.get("ok"), "qs": qs, "reason": applied.get("reason")}, ensure_ascii=True))
        if len(set(qs)) != 7:
            print("FAIL faq count", qs)
            (LOG_DIR / "kwork_landing_fill.png").write_bytes(browser.screenshot())
            return 1
        page.wait_for_timeout(500)
        removed2 = delete_empty_extras(page)
        print("extra_removed2", removed2)
        qs = [x.get("question") for x in (applied.get("getData") or [])]
        print("faq", json.dumps({"ok": applied.get("ok"), "qs": qs, "reason": applied.get("reason")}, ensure_ascii=True))
        if len(set(qs)) != 7:
            print("FAIL faq count", qs)
            (LOG_DIR / "kwork_landing_fill.png").write_bytes(browser.screenshot())
            return 1
        page.wait_for_timeout(800)
        href = page.evaluate("() => location.href") or ""
        if "/edit?id=51315832" not in href:
            print("FAIL wrong url before save", href)
            return 1
        print("save_btns", json.dumps(page.evaluate(
            """() => {
              const $ = window.jQuery || window.$;
              return {
                btns: [...document.querySelectorAll('.js-save-kwork')].map((el) => ({
                  text: ((el.innerText || '').replace(/\\s+/g, ' ').trim()).slice(0, 40),
                  h: Math.round(el.getBoundingClientRect().height),
                  jqDis: $ ? !!$(el).prop('disabled') : null,
                  loading: $ ? $(el).data('loading') : null,
                })),
                portfolioType: window.portfolioType,
                appPort: typeof window.appPortfolioListSortable,
                sortable: $ ? !!$('.sortable-card-list[data-type=\"portfolios\"]').data('sortableList') : null,
              };
            }"""
        ), ensure_ascii=True))
        print("size_before_save", json.dumps(fill_size_volume(page, built["service_size"], built["volume"]), ensure_ascii=True)[:2000])
        page.evaluate(
            """() => {
              const btn = document.querySelector('.js-wrap-save-btn-kwork .js-save-kwork') || document.querySelector('.js-save-kwork');
              if (!btn) return;
              btn.classList.remove('disabled', 'js-uploader-button-disable');
              btn.disabled = false;
              const $ = window.jQuery || window.$;
              if ($) $(btn).prop('disabled', false).removeClass('disabled js-uploader-button-disable');
            }"""
        )
        saved = {"ok": False}
        try:
            with page.expect_response(lambda r: "save_kwork" in r.url or "draft_save" in r.url, timeout=15000) as resp_info:
                invoked = page.evaluate(
                    """() => {
                      const $ = window.jQuery || window.$;
                      $('.js-save-kwork').prop('disabled', false).removeClass('disabled kw-button--loading js-uploader-button-disable').data('loading', false);
                      const btn = $('.js-wrap-save-btn-kwork .js-save-kwork')[0] || $('.js-save-kwork')[0];
                      try {
                        const handlers = ($._data(btn, 'events') || {}).click || [];
                        const deh = handlers.find((h) => /De\\(/.test(String(h.handler)));
                        if (!deh) return { ok: false, err: 'no_de_handler' };
                        deh.handler({ target: btn, which: 1, preventDefault: function() {}, stopPropagation: function() {} });
                        return { ok: true, how: 'De(false)' };
                      } catch (e) {
                        return { ok: false, err: String(e), stack: String(e.stack || '').slice(0, 400) };
                      }
                    }"""
                )
                print("de_invoke", json.dumps(invoked, ensure_ascii=True))
                if not invoked.get("ok"):
                    raise RuntimeError(invoked.get("err") or "de_invoke_fail")
            resp = resp_info.value
            saved = {"ok": True, "how": "De(false)", "status": resp.status, "url": resp.url}
        except Exception as exc:
            saved = {"ok": False, "how": "green_expect_fail", "err": str(exc)[:400]}
        print("save", json.dumps(saved, ensure_ascii=True))
        errdump = page.evaluate(
            """() => {
              const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
              const fieldErrs = [...document.querySelectorAll('.kwork-save-step__field-error, .js-kwork-save-field-error')].map((el) => ({
                hidden: el.classList.contains('hidden'),
                display: getComputedStyle(el).display,
                t: n(el.textContent).slice(0, 160),
              }));
              const size = [...document.querySelectorAll('#service-size-block, #volume-block, [name=volume], [name=service_size], .js-field-input-service-size-editor, .js-volume')].map((el) => ({
                id: el.id,
                name: el.getAttribute('name'),
                cls: String(el.className || '').slice(0, 90),
                hiddenCls: el.classList.contains('hidden'),
                display: getComputedStyle(el).display,
                h: Math.round(el.getBoundingClientRect().height),
                val: n((el.value || el.innerText || '')).slice(0, 80),
              }));
              let faq = {};
              try {
                const vm = window.appFaq && window.appFaq.$refs && window.appFaq.$refs.faq;
                faq = {
                  incomplete: vm && vm.isContainsErrorsOrIncompleteQuestion,
                  getN: vm && vm.getData ? vm.getData().length : 0,
                  html: ((document.querySelector('#app-faq') || {}).innerHTML || '').length,
                };
              } catch (e) {
                faq = { err: String(e) };
              }
              return {
                y: window.scrollY,
                fieldErrs: fieldErrs.filter((x) => !x.hidden && x.display !== 'none' && x.t).slice(0, 20),
                fieldErrsAll: fieldErrs.slice(0, 20),
                size,
                faq,
              };
            }"""
        )
        Path("logs/kwork_landing_save_errors.json").write_text(json.dumps(errdump, ensure_ascii=False, indent=2), encoding="utf-8")
        print("save_errors", json.dumps(errdump, ensure_ascii=True)[:3000])
        if saved.get("how") == "green_expect" and saved.get("status") == 200 and "save_kwork" in (saved.get("url") or ""):
            pass
        elif not saved.get("ok") or saved.get("how") not in {"De(false)", "jq_green_save", "dom_green_save", "green_expect"}:
            print("FAIL save how", saved)
            (LOG_DIR / "kwork_landing_fill.png").write_bytes(browser.screenshot())
            return 1
        if saved.get("how") == "green_expect" and "draft_save" in (saved.get("url") or ""):
            print("FAIL used draft_save", saved)
            return 1
        page.wait_for_timeout(8000)
        save_posts = [p for p in posts if "save_kwork" in p.get("url", "")]
        print("save_req", json.dumps([p for p in save_posts if p.get("kind") == "req"], ensure_ascii=True)[:2000])
        if saved.get("how") == "green_expect" and saved.get("status") == 200:
            save_posts.append({"url": saved.get("url"), "status": saved.get("status"), "body": "(expect_response)"})
        print("save_posts", json.dumps(save_posts, ensure_ascii=True)[:2000])
        if not any(p.get("status") == 200 for p in save_posts if p.get("kind") != "req"):
            print("FAIL no save_kwork 200", json.dumps(posts, ensure_ascii=True)[:2000])
            (LOG_DIR / "kwork_landing_fill.png").write_bytes(browser.screenshot())
            return 1
        print("save_err", page.evaluate(
            """() => {
              const t = (document.body && document.body.innerText) || '';
              const m = t.match(/ошибк[аи].{0,120}|заполните.{0,80}|минимум.{0,60}|некорректн.{0,80}/i);
              return m ? m[0] : '';
            }"""
        ))
        (LOG_DIR / "kwork_landing_fill.png").write_bytes(browser.screenshot())
        posts.clear()
        browser.navigate(EDIT_URL)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        page.wait_for_selector("#step1-description, .js-kwork-title-editor", timeout=25000)
        page.wait_for_timeout(2500)
        verify = dump_form(page)
        print("verify", json.dumps(verify, ensure_ascii=True))
        (LOG_DIR / "kwork_landing_verify.png").write_bytes(browser.screenshot())
        extra_names = [x for x in (verify.get("extraFilled") or [])]
        faq_qs = verify.get("faqs") or []
        weekly = any("Еженедельный" in x or "еженедельный" in x for x in extra_names + (verify.get("faqAs") or []))
        empty_extra = any(not (x or "").strip() for x in (verify.get("extras") or []))
        title_ok = (verify.get("title") or "") == built["title"] or (verify.get("titleHidden") or "") == built["title"]
        desc_ok = 100 <= int(verify.get("descLen") or 0) <= 1200
        inst_ok = 100 <= int(verify.get("instLen") or 0) <= 500
        size_ok = bool(
            (verify.get("size") or "").strip()
            or (verify.get("sizeHidden") or "").strip()
            or (verify.get("bundleSize") or "").strip()
        )
        extras_ok = extra_names == WANTED_EXTRAS
        faq_ok = len(set(faq_qs)) == 7 and any("пример" in x.lower() for x in faq_qs) and any("блок" in x.lower() for x in faq_qs)
        type_ok = bool(verify.get("typeOn")) and bool(verify.get("kindOn"))
        ok = title_ok and desc_ok and inst_ok and size_ok and extras_ok and faq_ok and type_ok and not weekly and not empty_extra
        print("accept", json.dumps({
            "title_ok": title_ok,
            "desc_ok": desc_ok,
            "inst_ok": inst_ok,
            "size_ok": size_ok,
            "extras_ok": extras_ok,
            "faq_ok": faq_ok,
            "type_ok": type_ok,
            "weekly": weekly,
            "empty_extra": empty_extra,
            "save_how": saved.get("how"),
        }, ensure_ascii=True))
        return 0 if ok else 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
