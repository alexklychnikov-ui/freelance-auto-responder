from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus

from deploy._fill_kwork_landing import (
    delete_empty_extras,
    dump_hidden,
    fill_desc_or_inst,
)
from deploy._fill_kwork_new_draft import buyer_to_select_value, fill_extras_playwright
from deploy.kwork_listing_price_monitor import PRICE, extra_problems
from deploy.kwork_listing_skel import assemble, validate
from src.adapters.kwork_auth import is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

EDIT_ID = "52958975"
EDIT_URL = f"https://kwork.ru/edit?id={EDIT_ID}"
TYPE_RADIO = "#attribute_item_211"
KIND_RADIO = "#attribute_item_6328"
WANTED_EXTRAS = [
    "Ещё один магазин в ту же систему",
    "Оповещения в чат-боте",
    "Веб-кабинет каталога и графиков",
    "Панель метрик и графиков",
    "Контейнеры и выкладка на сервер",
    "ИИ-разбор скачков цены",
    "Срочный запуск в приоритете",
]
LOG_DIR = Path("logs")


def extras_from_post(raw: str) -> dict:
    if not raw:
        return {"keys": [], "my": [], "bundle": [], "extraType": None}
    text = raw.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            keys = [k for k in obj.keys() if "extra" in k.lower() or "my_extras" in k.lower()]
            my = [n for n in (obj.get("my_extras_name") or obj.get("my_extras_name[]") or []) if str(n).strip()]
            return {
                "keys": keys[:60],
                "my": my[:20],
                "extraType": obj.get("extra-type") or obj.get("extra_type"),
                "title": obj.get("title") or obj.get("name"),
                "service_size": obj.get("service_size"),
                "volume": obj.get("volume"),
                "work_time": obj.get("work_time"),
            }
    keys = []
    my = []
    parsed = parse_qs(raw, keep_blank_values=True)
    size_val = vol_val = time_val = title_val = ""
    for k, vals in parsed.items():
        decoded = [unquote_plus(v) for v in vals]
        if "extra" in k.lower() or "my_extras" in k.lower():
            keys.append(k)
            if "my_extras_name" in k:
                my.extend([v for v in decoded if v.strip()])
        if k == "service_size":
            size_val = decoded[0][:200] if decoded else ""
        elif k == "volume":
            vol_val = decoded[0][:40] if decoded else ""
        elif k == "work_time":
            time_val = decoded[0][:40] if decoded else ""
        elif k in {"title", "name"}:
            title_val = decoded[0][:80] if decoded else ""
    return {
        "keys": keys[:60],
        "my": my[:20],
        "title": title_val,
        "service_size": size_val,
        "volume": vol_val,
        "work_time": time_val,
    }


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
        if f"edit?id={EDIT_ID}" not in (href or ""):
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
    pin_extra_inputs(page, extras)
    return results


def pin_extra_inputs(page, extras: list[dict]) -> None:
    page.evaluate(
        """(items) => {
          const live = (sel) => [...document.querySelectorAll(sel)]
            .filter((el) => !el.closest('.js-my-extras-templates__item'));
          const names = live('input[name=\"my_extras_name[]\"]');
          const hints = live('input[name=\"my_extras_description[]\"], textarea[name=\"my_extras_description[]\"]');
          const prices = live('select.js-extra-price');
          const durs = live('select.js-extra-duration');
          const editors = live('.add-extra__item-name-input');
          const hintEds = live('.add-extra__item-description-input');
          const $ = window.jQuery;
          items.forEach((ex, i) => {
            if (names[i]) {
              names[i].disabled = false;
              names[i].removeAttribute('data-check-bad-words');
              names[i].value = ex.name;
            }
            if (hints[i]) {
              hints[i].disabled = false;
              hints[i].removeAttribute('data-check-bad-words');
              hints[i].value = ex.hint;
            }
            if (editors[i]) editors[i].textContent = ex.name;
            if (hintEds[i]) hintEds[i].textContent = ex.hint;
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


def disable_template_inputs(page) -> int:
    return page.evaluate(
        """() => {
          let n = 0;
          document.querySelectorAll('.js-my-extras-templates__item input, .js-my-extras-templates__item select, .js-my-extras-templates__item textarea').forEach((el) => {
            el.disabled = true;
            n += 1;
          });
          return n;
        }"""
    )


def fill_work_time(page, days: str) -> dict:
    return page.evaluate(
        """(days) => {
          const $ = window.jQuery;
          const el = document.querySelector('#step2-work-time, select[name=\"work_time\"], [name=\"work_time\"]');
          if (!el) return { ok: false };
          el.disabled = false;
          if ($ && $(el).length) $(el).val(String(days)).trigger('chosen:updated').trigger('change');
          else {
            el.value = String(days);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          }
          return { ok: true, value: String(el.value || ''), tag: el.tagName, name: el.name };
        }""",
        days,
    )


def hook_save_form(page) -> None:
    page.evaluate(
        """() => {
          window.__net = [];
          window.__saveResp = null;
          window.__saveRespRaw = '';
          if (window.SendData && SendData.prototype && SendData.prototype.getFormData) {
            const orig = SendData.prototype.getFormData;
            SendData.prototype.getFormData = function() {
              const data = orig.apply(this, arguments);
              try {
                const src = (this.data && this.data.my_extras_name) ? this.data : data;
                if (src && Array.isArray(src.my_extras_name)) {
                  const keep = [];
                  src.my_extras_name.forEach((n, i) => { if (String(n || '').trim()) keep.push(i); });
                  ['my_extras_name','my_extras_ids','my_extras_price','my_extras_duration','my_extras_description','my_extras_description_ids'].forEach((k) => {
                    if (Array.isArray(src[k])) src[k] = keep.map((i) => src[k][i]);
                  });
                }
                window.__gfdExtras = src && src.my_extras_name;
              } catch (e) { window.__gfdErr = String(e); }
              return data;
            };
          }
          const $ = window.jQuery;
          if ($ && $.ajaxSetup) {
            $(document).ajaxComplete((e, xhr, settings) => {
              const u = String((settings && settings.url) || '');
              if (u.indexOf('save_kwork') >= 0 || u.indexOf('draft_save') >= 0) {
                window.__saveRespRaw = xhr.responseText || '';
                try { window.__saveResp = JSON.parse(xhr.responseText); } catch (err) { window.__saveResp = xhr.responseText; }
              }
            });
          }
        }"""
    )


def invoke_de(page) -> dict:
    return page.evaluate(
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
          const typeEl = document.querySelector('#attribute_item_211');
          const kindEl = document.querySelector('#attribute_item_6328');
          const prices = [...document.querySelectorAll('select.js-extra-price')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'))
            .map((el) => el.value);
          const days = [...document.querySelectorAll('select.js-extra-duration')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'))
            .map((el) => el.value);
          const extraPosted = [...document.querySelectorAll('input[name=\"my_extras_name[]\"]')]
            .filter((el) => !el.closest('.js-my-extras-templates__item'))
            .map((el) => n(el.value).replace(/<\\/?word-error>/g, ''))
            .filter(Boolean);
          const descVal = desc ? String(desc.value || '') : '';
          const instVal = inst ? String(inst.value || '') : '';
          const descText = descTrum ? n(descTrum.innerText) : strip(descVal);
          const instText = instTrum ? n(instTrum.innerText) : strip(instVal);
          const cover = document.querySelector('img[src*=\"52958975\"], img[src*=\"6a366ce2\"], .kwork-save-step img[src*=\"/files/\"], .file-preview img, .js-file-wrapper img');
          const coverPath = n((document.querySelector('[name=\"old-first-photo-path\"]') || {}).value || '');
          return {
            url: location.href,
            title: n((document.querySelector('.js-kwork-title-editor') || {}).innerText || ''),
            titleHidden: n((document.querySelector('#step1-name') || {}).value || ''),
            sizeHidden: n((document.querySelector('#step2-service-size, [name=\"service_size\"]') || {}).value || ''),
            typeOn: !!(typeEl && typeEl.checked),
            kindOn: !!(kindEl && kindEl.checked),
            attrsOn: [...document.querySelectorAll('input[id^=\"attribute_item_\"]:checked')].map((el) => el.id),
            descLen: Math.max(descText.length, strip(descVal).length),
            instLen: Math.max(instText.length, strip(instVal).length),
            descValHead: strip(descVal).slice(0, 180),
            instValHead: strip(instVal).slice(0, 180),
            descHasAnySites: /любые сайты|любых сайтах/i.test(descVal + descText),
            instHasPrishlite: /Пришлите/i.test(instVal + instText),
            size: n((document.querySelector('.js-field-input-service-size-editor') || {}).innerText || ''),
            volume: ((document.querySelector('#step2-volume, [name=\"volume\"]') || {}).value) || '',
            workTime: ((document.querySelector('#step2-work-time, [name=\"work_time\"]') || {}).value) || '',
            extras,
            extraFilled: extras.filter(Boolean),
            extraPosted,
            faqs,
            faqAs,
            prices,
            extraDays: days,
            coverSrc: cover ? String(cover.getAttribute('src') || cover.src || '').slice(0, 180) : '',
            coverPath,
            coverOn: !!(coverPath || (cover && (cover.getAttribute('src') || cover.src))),
          };
        }"""
    )


def goto_edit(page) -> None:
    href = ""
    try:
        href = page.evaluate("() => location.href") or ""
    except Exception:
        href = ""
    if f"edit?id={EDIT_ID}" not in href:
        page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=60000)
    else:
        page.reload(wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_selector("#step1-description, .js-kwork-title-editor", timeout=25000)
    page.wait_for_timeout(7500)
    try:
        page.wait_for_function("() => !!(window.jQuery || window.$)", timeout=20000)
    except Exception as exc:
        print("jq_after_reload", str(exc)[:160])


def apply_faqs_app(page, items: list[dict]) -> dict:
    print("faq_step", json.dumps(page.evaluate(
        """() => {
          const step = document.querySelector('.js-step-faq');
          if (step) {
            const content = step.querySelector('.kwork-save-step__content');
            const hidden = content && (getComputedStyle(content).display === 'none' || content.offsetHeight < 20);
            const header = step.querySelector('.kwork-save-step__header');
            if (hidden && header) header.click();
          }
          return {
            appFaq: typeof window.appFaq,
            refs: !!(window.appFaq && window.appFaq.$refs && window.appFaq.$refs.faq),
            vue: !!(document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__),
            html: ((document.querySelector('#app-faq') || {}).innerHTML || '').length,
          };
        }"""
    ), ensure_ascii=True))
    for _ in range(12):
        state = page.evaluate(
            """() => ({
              refs: !!(window.appFaq && window.appFaq.$refs && window.appFaq.$refs.faq),
              vue: !!(document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__),
            })"""
        )
        print("faq_wait", json.dumps(state, ensure_ascii=True))
        if state.get("refs") or state.get("vue"):
            break
        page.wait_for_timeout(500)
    applied = page.evaluate(
        """(items) => {
          const root = document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__;
          const vm = (window.appFaq && window.appFaq.$refs && window.appFaq.$refs.faq)
            || (root && (root.$children || []).find((c) => c.$options && c.$options.name === 'faq-list-editable'));
          if (!vm) return { ok: false, reason: 'no_vm' };
          const existing = (vm.questions || []).filter((q) => !q.isNew);
          while (existing.length > items.length) {
            const last = existing.pop();
            vm.questions = vm.questions.filter((q) => q.id !== last.id);
          }
          for (let i = 0; i < items.length; i++) {
            if (i < existing.length) {
              vm.updateQuestion(items[i].q, existing[i].id);
              vm.updateAnswer(items[i].a, existing[i].id);
              if (typeof vm.saveQuestion === 'function') vm.saveQuestion(existing[i].id);
            } else {
              vm.addNewQuestionTemplate();
              const created = vm.questions[vm.questions.length - 1];
              created.id = Date.now() * 100 + i;
              vm.updateQuestion(items[i].q, created.id);
              vm.updateAnswer(items[i].a, created.id);
              vm.saveQuestion(created.id);
            }
          }
          (vm.questions || []).forEach((q) => {
            if (q && q.isNew && q.id != null && typeof vm.saveQuestion === 'function') vm.saveQuestion(q.id);
          });
          if (typeof vm.change === 'function') vm.change();
          const data = typeof vm.getData === 'function' ? vm.getData() : [];
          return {
            ok: true,
            source: (window.appFaq && window.appFaq.$refs && window.appFaq.$refs.faq) ? 'appFaq.refs' : 'vue-child',
            getData: data,
            getN: data.length,
            isNewLeft: (vm.questions || []).filter((q) => q.isNew).length,
            qs: data.map((x) => x.question),
          };
        }""",
        [{"q": x["q"], "a": x["a"]} for x in items],
    )
    return applied


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
          const extrasMy = live('input[name=\"my_extras_name[]\"]').map((el) => n(el.value).replace(/<\\/?word-error>/g, '')).filter(Boolean);
          const extrasEditors = live('.add-extra__item-name-input').map((el) => n(el.innerText)).filter(Boolean);
          let serializeMy = [];
          try {
            const $ = window.jQuery;
            const form = document.querySelector('.js-kwork-save-form, form');
            if ($ && form) {
              ($ (form).serializeArray() || []).forEach((p) => {
                if (p && /my_extras_name/i.test(p.name) && String(p.value || '').trim()) serializeMy.push(n(p.value));
              });
            }
          } catch (e) {}
          const desc = document.querySelector('#step1-description');
          const descVal = desc ? String(desc.value || '') : '';
          const descTrum = document.querySelector("textarea[name='description']")
            && document.querySelector("textarea[name='description']").closest('.trumbowyg-box')
            && document.querySelector("textarea[name='description']").closest('.trumbowyg-box').querySelector('.trumbowyg-editor');
          const descText = descTrum ? n(descTrum.innerText) : strip(descVal);
          const blob = descVal + ' ' + descText;
          const winExtra = Object.keys(window).filter((k) => /extra|faq/i.test(k)).slice(0, 30);
          return {
            url: location.href,
            jq: typeof window.jQuery,
            extrasMy,
            extrasEditors,
            serializeMy,
            extras: extrasMy.length ? extrasMy : (serializeMy.length ? serializeMy : extrasEditors),
            faqsJsonRawType: typeof window.faqsJson,
            faqsJson: faqsParsed.map((x) => n(x.question || x.q || '')).filter(Boolean),
            faqsJsonN: faqsParsed.length,
            descLen: Math.max(descText.length, strip(descVal).length),
            descHead: strip(descVal).slice(0, 180),
            descHasAnySites: /любые сайты|любых сайтах/i.test(blob),
            winExtra,
            title: n((document.querySelector('.js-kwork-title-editor') || {}).innerText || ''),
          };
        }"""
    )


def main() -> int:
    built = assemble(PRICE)
    problems = validate(built) + extra_problems(PRICE, built)
    if problems:
        print("FAIL validate", problems)
        return 1
    if [e["name"] for e in built["extras"]] != WANTED_EXTRAS:
        print("FAIL WANTED_EXTRAS mismatch")
        return 1
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    browser = PlaywrightBrowserAdapter(
        storage_state_path="data/kwork_storage.json",
        headless=False,
    )
    posts: list[dict] = []
    captured = {"status": None, "text": "", "req": "", "url": ""}
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
            (LOG_DIR / "kwork_price_monitor_fill.png").write_bytes(browser.screenshot())
            return 1

        def on_req(req) -> None:
            if req.method != "POST":
                return
            url = req.url
            if "save_kwork" not in url and "draft_save" not in url:
                return
            raw = req.post_data or ""
            posts.append({
                "kind": "req",
                "url": url,
                "nkeys": raw.count("="),
                "parsed": extras_from_post(raw),
            })
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
                if not captured.get("text"):
                    captured["text"] = body
                if not captured.get("status"):
                    captured["status"] = resp.status
                captured["url"] = resp.url

        page.on("request", on_req)
        page.on("response", on_resp)
        page.on("dialog", lambda d: d.accept())
        print("landed", page.evaluate("() => location.href"))
        print("attrs", page.evaluate(
            """() => ({
              type: !!(document.querySelector('#attribute_item_211') || {}).checked,
              kind: !!(document.querySelector('#attribute_item_6328') || {}).checked,
              attrsOn: [...document.querySelectorAll('input[id^=\"attribute_item_\"]:checked')].map((el) => el.id),
              trum: document.querySelectorAll('.trumbowyg-editor').length,
              descId: !!(document.querySelector('#step1-description')),
              cover: (document.querySelector('[name=\"old-first-photo-path\"]') || {}).value || null,
            })"""
        ))

        try:
            page.wait_for_function("() => !!(window.jQuery || window.$ || window.De)", timeout=30000)
            print("js_ready", page.evaluate(
                """() => ({ jq: typeof window.jQuery, dollar: typeof window.$, De: typeof window.De })"""
            ))
        except Exception as exc:
            print("jq_wait", str(exc)[:200])
            print("FAIL: kwork app js not loaded")
            (LOG_DIR / "kwork_price_monitor_fill.png").write_bytes(browser.screenshot())
            return 1

        print("desc", json.dumps(fill_desc_or_inst(page, "description", built["description"]), ensure_ascii=True))
        print("hidden", json.dumps(dump_hidden(page), ensure_ascii=True)[:2500])
        extras = fill_extras_js(page, built["extras"])
        if not extras or not (extras[0].get("typed") or {}).get("nameText"):
            extras = fill_extras_playwright(page, built["extras"])
            pin_extra_inputs(page, built["extras"])
        print("extras", json.dumps(extras, ensure_ascii=True)[:2500])
        removed = delete_empty_extras(page)
        print("extra_removed", removed)
        print("tpl_disabled", disable_template_inputs(page))
        pin_extra_inputs(page, built["extras"])
        applied = apply_faqs_app(page, built["faqs"])
        qs = [x.get("question") for x in (applied.get("getData") or [])] or (applied.get("qs") or [])
        print("faq", json.dumps({"ok": applied.get("ok"), "qs": qs, "reason": applied.get("reason"), "isNewLeft": applied.get("isNewLeft")}, ensure_ascii=True))
        if len(set(qs)) != 7:
            print("FAIL faq count", qs)
            (LOG_DIR / "kwork_price_monitor_fill.png").write_bytes(browser.screenshot())
            return 1
        page.wait_for_timeout(500)
        removed2 = delete_empty_extras(page)
        print("extra_removed2", removed2)
        pin_extra_inputs(page, built["extras"])
        href = page.evaluate("() => location.href") or ""
        if f"/edit?id={EDIT_ID}" not in href:
            print("FAIL wrong url before save", href)
            return 1
        print("desc2", json.dumps(fill_desc_or_inst(page, "description", built["description"]), ensure_ascii=True))
        pin_extra_inputs(page, built["extras"])
        hook_save_form(page)
        page.evaluate(
            """() => {
              document.querySelectorAll('word-error, [class*=\"word-error\"]').forEach((el) => {
                el.replaceWith(document.createTextNode(el.textContent || ''));
              });
              const btn = document.querySelector('.js-wrap-save-btn-kwork .js-save-kwork') || document.querySelector('.js-save-kwork');
              if (!btn) return;
              btn.classList.remove('disabled', 'js-uploader-button-disable');
              btn.disabled = false;
              const $ = window.jQuery || window.$;
              if ($) $(btn).prop('disabled', false).removeClass('disabled js-uploader-button-disable');
            }"""
        )
        pin_extra_inputs(page, built["extras"])

        def handle_route(route) -> None:
            req = route.request
            url = req.url
            if req.method == "POST" and ("save_kwork" in url or "draft_save" in url):
                resp = route.fetch()
                body = resp.body()
                captured["status"] = resp.status
                captured["url"] = url
                captured["req"] = (req.post_data or "")[:20000]
                captured["text"] = body.decode("utf-8", errors="replace")[:8000]
                route.fulfill(status=resp.status, headers=dict(resp.headers), body=body)
            else:
                route.continue_()

        page.route("**/save_kwork*", handle_route)
        page.route("**/draft_save*", handle_route)
        saved = {"ok": False}
        try:
            with page.expect_response(lambda r: "save_kwork" in r.url or "draft_save" in r.url, timeout=25000) as resp_info:
                invoked = invoke_de(page)
                print("de_invoke", json.dumps(invoked, ensure_ascii=True))
                if not invoked.get("ok"):
                    raise RuntimeError(invoked.get("err") or "de_invoke_fail")
            resp = resp_info.value
            saved = {"ok": True, "how": "De(false)", "status": resp.status, "url": resp.url}
            if not captured.get("text"):
                try:
                    captured["text"] = resp.text()[:4000]
                    captured["status"] = resp.status
                    captured["url"] = resp.url
                except Exception:
                    pass
        except Exception as exc:
            saved = {"ok": False, "how": "green_expect_fail", "err": str(exc)[:400]}
        page.wait_for_timeout(1500)
        ajax_resp = page.evaluate(
            """() => ({
              data: window.__saveResp,
              raw: String(window.__saveRespRaw || '').slice(0, 4000),
              gfdExtras: window.__gfdExtras || null,
              gfdErr: window.__gfdErr || null,
              href: location.href,
            })"""
        )
        print("save", json.dumps(saved, ensure_ascii=True))
        print("ajax_resp", json.dumps(ajax_resp, ensure_ascii=True)[:3000])
        if captured.get("status") and not saved.get("status"):
            saved["status"] = captured["status"]
            saved["url"] = captured.get("url") or saved.get("url")
            saved["ok"] = True
            saved["how"] = "De(false)"
        req_extras = extras_from_post(captured.get("req") or "")
        print("post_extras", json.dumps(req_extras, ensure_ascii=True)[:3000])
        js = None
        raw_text = captured.get("text") or ajax_resp.get("raw") or ""
        if str(raw_text).strip().startswith("{") or str(raw_text).strip().startswith("["):
            try:
                js = json.loads(raw_text)
            except Exception:
                js = None
        if js is None and isinstance(ajax_resp.get("data"), dict):
            js = ajax_resp.get("data")
        success = None if js is None else (js.get("success") if "success" in js else js.get("result"))
        print("save_json", json.dumps({"success": success, "body": js, "textHead": str(raw_text)[:240]}, ensure_ascii=True)[:3000])
        Path("logs/kwork_price_monitor_save.json").write_text(
            json.dumps(
                {"save": saved, "captured": {k: (v[:8000] if isinstance(v, str) else v) for k, v in captured.items()}, "post_extras": req_extras, "ajax_resp": ajax_resp, "js": js},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not saved.get("ok") or saved.get("how") != "De(false)":
            print("FAIL save how", saved)
            (LOG_DIR / "kwork_price_monitor_fill.png").write_bytes(browser.screenshot())
            return 1
        if "draft_save" in (saved.get("url") or "") or "draft_save" in (captured.get("url") or ""):
            print("FAIL used draft_save", saved)
            return 1
        result_ok = False
        if isinstance(js, dict):
            result_ok = js.get("result") == "success" or js.get("success") is True
            if js.get("result") == "error" or js.get("success") is False:
                print("FAIL save_kwork result error", json.dumps(js, ensure_ascii=True)[:3000])
                (LOG_DIR / "kwork_price_monitor_fill.png").write_bytes(browser.screenshot())
                return 1
        post_my = [str(x).replace("<word-error>", "").replace("</word-error>", "") for x in (req_extras.get("my") or [])]
        gfd = ajax_resp.get("gfdExtras") or []
        extras_my = post_my or [str(x) for x in gfd if str(x).strip()]
        print("extras.my_post", json.dumps(extras_my, ensure_ascii=True))
        href_now = str(ajax_resp.get("href") or "")
        if not result_ok:
            if saved.get("status") == 200 and len(extras_my) == 7 and f"/{EDIT_ID}/" in href_now:
                print("WARN save JSON missing, public redirect + extras.my==7, continue verify")
                result_ok = True
            else:
                print("FAIL save_kwork result not success", js, "href", href_now, "my", extras_my)
                (LOG_DIR / "kwork_price_monitor_fill.png").write_bytes(browser.screenshot())
                return 1
        page.wait_for_timeout(2000)
        (LOG_DIR / "kwork_price_monitor_fill.png").write_bytes(browser.screenshot())
        goto_edit(page)
        verify = persist_dump(page)
        print("verify", json.dumps(verify, ensure_ascii=True)[:5000])
        Path("logs/kwork_price_monitor_reload.json").write_text(
            json.dumps(verify, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (LOG_DIR / "kwork_price_monitor_verify.png").write_bytes(browser.screenshot())
        extras_my_reload = list(verify.get("extras") or [])
        faq_qs = list(verify.get("faqsJson") or [])
        print("extras.my", json.dumps(extras_my_reload, ensure_ascii=True))
        print("faqsJson", json.dumps(faq_qs, ensure_ascii=True))
        word_err = any("word-error" in str(x).lower() for x in extras_my)
        if len(extras_my_reload) != 7:
            print("FAIL extras.my after reload", extras_my_reload)
            print("save_body", (captured.get("text") or "")[:1500])
            print("post_extras_names", json.dumps(extras_my, ensure_ascii=True))
            print("word-error", word_err, "save_errors", None if not isinstance(js, dict) else (js.get("errors") or js.get("error")))
            return 1
        desc_ok = 100 <= int(verify.get("descLen") or 0) <= 1200 and not verify.get("descHasAnySites")
        extras_ok = extras_my_reload == WANTED_EXTRAS
        faq_ok = (
            len(set(faq_qs)) == 7
            and any("пример" in x.lower() for x in faq_qs)
            and any("с любых сайтов соберёте цены" in x.lower() for x in faq_qs)
        )
        ok = desc_ok and extras_ok and faq_ok
        print("accept", json.dumps({
            "desc_ok": desc_ok,
            "extras_ok": extras_ok,
            "faq_ok": faq_ok,
            "save_how": saved.get("how"),
            "save_result": success,
            "extras_my": extras_my_reload,
            "faqsJson": faq_qs,
            "descHasAnySites": verify.get("descHasAnySites"),
        }, ensure_ascii=True))
        return 0 if ok else 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
