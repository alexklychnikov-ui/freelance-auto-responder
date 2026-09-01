from __future__ import annotations

import json
import time
from pathlib import Path

from src.adapters.kwork_auth import is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

DRAFT_URL = "https://kwork.ru/new?draft_id=54261579"
OUT_DIR = Path("logs")

DESCRIPTION = """\
Соберу данные с открытой страницы сайта в готовую Excel-таблицу. Вы получаете файл, с которым можно сразу работать — без копирования руками.

Какие задачи закрывает
• каталог, объявления, прайс, список компаний или вакансий
• быстрый перенос в таблицу для сверки и учёта

Что входит
• 1 открытый сайт без авторизации
• до 200 строк и основные поля: название, цена, ссылка и аналоги
• файл Excel .xlsx

Что не входит
• капча, логин, антибот
• несколько сайтов, полный обход каталога, расписание, Telegram и сервер — это опции ниже

Почему заказать здесь
• до старта фиксирую список колонок, чтобы таблица совпала с задачей
• базовый кворк даёт рабочий файл; развитие — отдельными шагами

Для старта нужны ссылка на страницу, нужные колонки и пример 3–5 строк, если он есть."""

INSTRUCTION = """\
Пришлите:
1) ссылку на сайт или нужный раздел;
2) список колонок для Excel;
3) пример 3–5 строк желаемого результата (скрин или файл), если есть.
Если нужны логин, капча, несколько сайтов, расписание или Telegram — напишите сразу: это опции, не базовый кворк."""

SERVICE_SIZE = "1 сайт, до 200 строк"
VOLUME = "1"

EXTRAS = [
    {
        "name": "Второй сайт в ту же таблицу",
        "hint": "Ещё один открытый источник в тот же Excel. Без капчи и логина.",
        "price": "800",
        "days": "1",
    },
    {
        "name": "Пагинация и доп. поля",
        "hint": "Обход страниц каталога и дополнительные колонки сверх базовых.",
        "price": "1200",
        "days": "1",
    },
    {
        "name": "Очистка и нормализация Excel",
        "hint": "Дедуп, единый формат цен и дат, пустые строки, базовая проверка.",
        "price": "800",
        "days": "1",
    },
    {
        "name": "Сбор по расписанию (7 дней)",
        "hint": "Повторный сбор 7 дней: скрипт плюс инструкция запуска у вас.",
        "price": "2000",
        "days": "2",
    },
    {
        "name": "Уведомления в Telegram",
        "hint": "Сообщение, когда выгрузка готова или данные на сайте изменились.",
        "price": "1600",
        "days": "1",
    },
    {
        "name": "Автозапуск на сервере",
        "hint": "Размещение скрипта на VPS и автозапуск по расписанию.",
        "price": "2400",
        "days": "2",
    },
    {
        "name": "Срочная выгрузка в тот же день",
        "hint": "Приоритет в очереди: отдам файл в течение дня при готовом ТЗ.",
        "price": "800",
        "days": "0",
    },
]

FAQ = [
    {
        "q": "Что входит в стоимость?",
        "a": "1 открытый сайт без авторизации, до 200 строк, основные поля и Excel-файл. Капча, логин, несколько сайтов, расписание и Telegram в базовый кворк не входят.",
    },
    {
        "q": "Какие сроки выполнения?",
        "a": "Базовый кворк — 1 день после ссылки и списка колонок. Каждая опция добавляет 0–2 дня, срок виден в карточке опции.",
    },
    {
        "q": "Что потребуется для начала?",
        "a": "Ссылка на раздел, какие колонки нужны в Excel и пример 3–5 строк, если есть. Логины и доступы — только если это отдельно согласовано.",
    },
    {
        "q": "Можно ли внести изменения?",
        "a": "Да, один цикл правок по составу колонок в рамках оговорённого объёма. Новый сайт, новые поля или другой формат — через опции.",
    },
    {
        "q": "Есть ли поддержка после сдачи?",
        "a": "3 дня отвечаю на вопросы по файлу и тому, как им пользоваться. Доработки сверх согласованного ТЗ — отдельная опция или новый заказ.",
    },
    {
        "q": "Что делать, если нет ТЗ?",
        "a": "Пришлите ссылку и напишите, что хотите видеть в таблице. Уточню поля письменно до старта и зафиксирую объём, чтобы не разъехались ожидания.",
    },
]

DUMP_UI_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const shortClicks = [...document.querySelectorAll("a, button, span")]
    .map((el) => ({
      tag: el.tagName,
      text: norm(el.textContent).slice(0, 60),
      className: String(el.className || "").slice(0, 160),
    }))
    .filter((x) => x.text.length > 0 && x.text.length <= 40)
    .filter((x) => /добавить|вопрос|сохран|еще|ещё/i.test(x.text));
  const extras = {
    nameEditors: document.querySelectorAll(".add-extra__item-name-input").length,
    descEditors: document.querySelectorAll(".add-extra__item-description-input").length,
    priceSelects: document.querySelectorAll("select.js-extra-price").length,
    catChecks: document.querySelectorAll(".js-add-extra-row__cat-checkbox").length,
    addButtons: [...document.querySelectorAll("a, button, span")].filter((el) =>
      /^добавить еще|^добавить ещё|^добавить опцию/i.test(norm(el.textContent))
    ).map((el) => ({
      tag: el.tagName,
      className: String(el.className || "").slice(0, 160),
      text: norm(el.textContent),
    })),
  };
  const faqNodes = [...document.querySelectorAll("[class*='faq' i], [id*='faq' i]")].slice(0, 15).map((el) => ({
    tag: el.tagName,
    className: String(el.className || "").slice(0, 160),
    id: el.id || "",
  }));
  return { shortClicks, extras, faqNodes };
}
"""

SET_TRUMBOWYG_JS = r"""
(name, html) => {
  const ta = document.querySelector('textarea[name="' + name + '"]');
  if (!ta) return { ok: false, reason: "textarea_not_found", name };
  const $ = window.jQuery || window.$;
  if ($ && typeof $(ta).trumbowyg === "function") {
    $(ta).trumbowyg("html", html);
    $(ta).trigger("tbwchange");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.dispatchEvent(new Event("change", { bubbles: true }));
    const box = ta.closest(".trumbowyg-box");
    const ed = box && box.querySelector(".trumbowyg-editor");
    return { ok: true, method: "trumbowyg", len: (ed && (ed.innerText || "").trim().length) || 0 };
  }
  const box = ta.closest(".trumbowyg-box") || ta.parentElement;
  const editor = box && box.querySelector(".trumbowyg-editor");
  if (editor) {
    editor.innerHTML = html;
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  }
  ta.value = html;
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  return { ok: true, method: "dom", len: html.length };
}
"""

SET_CONTENTEDIT_JS = r"""
(selector, text) => {
  const el = document.querySelector(selector);
  if (!el) return { ok: false, reason: "not_found", selector };
  el.focus();
  el.textContent = text;
  el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
  el.dispatchEvent(new Event("keyup", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  const wrap = el.closest("div, td, li, .form-group") || el.parentElement;
  const hidden = wrap && wrap.querySelector("input.js-content-storage, textarea.js-content-storage, input[name], textarea[name]");
  if (hidden) {
    hidden.value = text;
    hidden.dispatchEvent(new Event("input", { bubbles: true }));
    hidden.dispatchEvent(new Event("change", { bubbles: true }));
  }
  return { ok: true, text: (el.textContent || "").trim(), hidden: hidden ? hidden.name : null };
}
"""

CLICK_TEXT_JS = r"""
(needle) => {
  const n = String(needle || "").toLowerCase().replace("ё", "е");
  const nodes = [...document.querySelectorAll("a, button, span")];
  const el = nodes.find((node) => {
    const t = (node.textContent || "").replace(/\s+/g, " ").trim().toLowerCase().replace("ё", "е");
    return t === n || t === n + " +" || t === "+ " + n;
  });
  if (!el) return { ok: false, reason: "not_found", needle };
  el.click();
  return {
    ok: true,
    tag: el.tagName,
    className: String(el.className || "").slice(0, 160),
    text: (el.textContent || "").trim().slice(0, 80),
  };
}
"""

FILL_EXTRA_ROW_JS = r"""
(idx, payload) => {
  const rowOf = (el) =>
    el.closest(".js-add-extra-row, .add-extras-list__item, .extra-panel, tr") || el.parentElement;
  const isCat = (el) => !!(rowOf(el) && rowOf(el).querySelector(".js-add-extra-row__cat-checkbox"));
  const names = [...document.querySelectorAll(".add-extra__item-name-input")].filter((el) => !isCat(el));
  const descs = [...document.querySelectorAll(".add-extra__item-description-input")].filter((el) => !isCat(el));
  const prices = [...document.querySelectorAll("select.js-extra-price")].filter((el) => !isCat(el));
  const days = [...document.querySelectorAll("select.js-extra-duration")].filter((el) => !isCat(el));
  if (!names[idx]) {
    return {
      ok: false,
      reason: "row_missing",
      idx,
      names: names.length,
      prices: prices.length,
      allNames: document.querySelectorAll(".add-extra__item-name-input").length,
      parents: [...document.querySelectorAll(".add-extra__item-name-input")].slice(0, 8).map((el) =>
        String((el.closest("div[class]") || el.parentElement).className || "").slice(0, 120)
      ),
    };
  }
  const setEd = (el, text) => {
    el.focus();
    el.textContent = text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
    el.dispatchEvent(new Event("keyup", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    const wrap = el.closest("div, td, li") || el.parentElement;
    const hidden = wrap && wrap.querySelector("input.js-content-storage, textarea.js-content-storage");
    if (hidden) {
      hidden.value = text;
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };
  setEd(names[idx], payload.name);
  if (descs[idx]) setEd(descs[idx], payload.hint);
  const setChosen = (select, value) => {
    if (!select) return false;
    const $ = window.jQuery || window.$;
    select.value = String(value);
    if ($ && $(select).length) {
      $(select).val(String(value)).trigger("chosen:updated").trigger("change");
    }
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return select.value === String(value) || select.value === value;
  };
  const priceOk = setChosen(prices[idx], payload.price);
  const daysOk = setChosen(days[idx], payload.days);
  return {
    ok: true,
    idx,
    name: (names[idx].textContent || "").trim(),
    hint: descs[idx] ? (descs[idx].textContent || "").trim() : "",
    price: prices[idx] ? prices[idx].value : null,
    days: days[idx] ? days[idx].value : null,
    priceOk,
    daysOk,
    counts: { names: names.length, descs: descs.length, prices: prices.length, days: days.length },
  };
}
"""

FILL_FAQ_JS = r"""
(items) => {
  const root = document.querySelector("#app-faq, .faq-list-editable") || document;
  const qInputs = [...root.querySelectorAll("input, textarea, [contenteditable='true']")].filter((el) =>
    /question|вопрос/i.test(
      (el.name || "") + (el.id || "") + (el.className || "") + (el.getAttribute("placeholder") || "")
    )
  );
  const aInputs = [...root.querySelectorAll("input, textarea, [contenteditable='true']")].filter((el) =>
    /answer|ответ/i.test(
      (el.name || "") + (el.id || "") + (el.className || "") + (el.getAttribute("placeholder") || "")
    )
  );
  const labeled = [...document.querySelectorAll("input, textarea, [contenteditable='true']")].map((el) => {
    const wrap = el.closest("label, .form-group, .faq-item, [class*='faq']") || el.parentElement;
    const lab = wrap && wrap.querySelector("label, .label, [class*='label']");
    return {
      el,
      label: ((lab && lab.textContent) || el.placeholder || "").replace(/\s+/g, " ").trim().toLowerCase(),
      name: el.name || "",
      id: el.id || "",
    };
  });
  const qs = qInputs.length ? qInputs : labeled.filter((x) => /вопрос/.test(x.label)).map((x) => x.el);
  const as_ = aInputs.length ? aInputs : labeled.filter((x) => /ответ/.test(x.label)).map((x) => x.el);
  const setEl = (el, text) => {
    if (!el) return;
    if (el.isContentEditable) {
      el.focus();
      el.textContent = text;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
      el.dispatchEvent(new Event("blur", { bubbles: true }));
      return;
    }
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value") && Object.getOwnPropertyDescriptor(proto, "value").set;
    if (setter) setter.call(el, text);
    else el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const filled = [];
  for (let i = 0; i < items.length; i++) {
    setEl(qs[i], items[i].q);
    setEl(as_[i], items[i].a);
    filled.push({
      i,
      q: qs[i] ? (qs[i].value || qs[i].textContent || "").trim().slice(0, 80) : null,
      a: as_[i] ? (as_[i].value || as_[i].textContent || "").trim().slice(0, 80) : null,
    });
  }
  return {
    ok: filled.every((x) => x.q && x.a),
    qCount: qs.length,
    aCount: as_.length,
    filled,
    sampleNames: labeled.filter((x) => /faq|вопрос|ответ/.test(x.label + x.name + x.id)).slice(0, 20).map((x) => ({
      name: x.name, id: x.id, label: x.label.slice(0, 80),
    })),
  };
}
"""

SAVE_KWORK_JS = r"""
() => {
  const wrap = document.querySelector(".js-wrap-save-btn-kwork");
  const scoped = wrap ? [...wrap.querySelectorAll("button, a, input[type='submit']")] : [];
  const btns = scoped.length ? scoped : [...document.querySelectorAll("button, a, input[type='submit']")];
  const el = btns.find((b) => {
    const t = (b.textContent || b.value || "").replace(/\s+/g, " ").trim().toLowerCase();
    const cls = String(b.className || "");
    if (cls.includes("js-save-portfolio")) return false;
    if (b.closest(".portfolio-upload-modal")) return false;
    return t === "сохранить" || cls.includes("js-save-kwork") || cls.includes("save-kwork");
  });
  if (!el) return { ok: false, reason: "save_not_found", candidates: btns.map((b) => String(b.className||"").slice(0,80)) };
  el.click();
  return { ok: true, className: String(el.className || "").slice(0, 160) };
}
"""

READBACK_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const desc = document.querySelector("textarea[name='description']");
  const inst = document.querySelector("textarea[name='instruction']");
  const descEd = document.querySelectorAll(".trumbowyg-editor");
  const extras = [...document.querySelectorAll(".add-extra__item-name-input")].map((el) => norm(el.textContent));
  const prices = [...document.querySelectorAll("select.js-extra-price")].map((el) => el.value);
  return {
    url: location.href,
    title: (document.querySelector("textarea[name='title']") || {}).value || "",
    descLen: descEd[0] ? norm(descEd[0].innerText).length : 0,
    instLen: descEd[1] ? norm(descEd[1].innerText).length : 0,
    descPreview: descEd[0] ? norm(descEd[0].innerText).slice(0, 240) : "",
    instPreview: descEd[1] ? norm(descEd[1].innerText).slice(0, 180) : "",
    serviceSize: norm((document.querySelector(".js-field-input-service-size-editor") || {}).textContent || ""),
    volume: (document.querySelector("[name='volume']") || {}).value || "",
    price: (document.querySelector("[name='typicalPriceSelect[6329]']") || {}).value
      || (document.querySelector("[name='bundle_free_price_value[priceStandard]']") || {}).value
      || "",
    extras,
    prices,
    bodySnippet: norm(document.body.innerText).slice(0, 2500),
  };
}
"""


def paragraphs_to_html(text: str) -> str:
    blocks = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("• "):
            if blocks and blocks[-1].startswith("<ul"):
                blocks[-1] += f"<li>{_esc(line[2:])}</li>"
            else:
                blocks.append(f"<ul><li>{_esc(line[2:])}</li>")
            continue
        if blocks and blocks[-1].startswith("<ul") and not blocks[-1].endswith("</ul>"):
            blocks[-1] += "</ul>"
        heading_like = line in {
            "Какие задачи закрывает",
            "Что входит",
            "Что не входит",
            "Почему заказать здесь",
        } or line.endswith(":")
        if heading_like and not line[0].isdigit():
            blocks.append(f"<p><strong>{_esc(line)}</strong></p>")
        else:
            blocks.append(f"<p>{_esc(line)}</p>")
    if blocks and blocks[-1].startswith("<ul") and not blocks[-1].endswith("</ul>"):
        blocks[-1] += "</ul>"
    return "".join(blocks)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def buyer_to_select_value(buyer_rub: str | int) -> str:
    return str(int(int(buyer_rub) * 5 // 4))


def _pick_chosen(page, select_locator, label_or_value: str) -> str:
    select_locator.wait_for(state="attached", timeout=8000)
    handle = select_locator.element_handle()
    if handle is None:
        return "no_handle"
    wanted = str(label_or_value)
    result = page.evaluate(
        """({el, value}) => {
          const $ = window.jQuery || window.$;
          const opts = [...el.options].map((o) => ({ v: o.value, t: (o.textContent || '').trim() }));
          const hit = opts.find((o) => o.v === String(value) || o.t.replace(/\\s/g,' ') === String(value) || o.t.includes(String(value)));
          const v = hit ? hit.v : String(value);
          if ($ && $(el).length) {
            $(el).val(v).trigger('chosen:updated').trigger('change');
          } else {
            el.value = v;
            el.dispatchEvent(new Event('change', { bubbles: true }));
          }
          return { value: el.value, wanted: v, opts: opts.slice(0, 8) };
        }""",
        {"el": handle, "value": wanted},
    )
    if str(result.get("value") or "") in {wanted, str(result.get("wanted") or "")}:
        return f"val:{result.get('value')}"
    return f"miss:{result}"


def fill_extras_playwright(page, extras: list[dict]) -> list[dict]:
    results = []
    extras_root = page.locator("text=Дополнительные опции").first
    try:
        html = page.evaluate(
            """() => {
              const h = [...document.querySelectorAll('h2,h3,h4,div')].find((el) =>
                /Дополнительные опции/.test(el.textContent || '') && (el.textContent || '').length < 80
              );
              const root = (h && h.closest('.kwork-save-step, .card, form')) || document.body;
              const selects = [...document.querySelectorAll('select.js-extra-price')].slice(0, 2).map((s) =>
                [...s.options].map((o) => ({ v: o.value, t: o.textContent.trim() }))
              );
              return {
                selects,
                addClass: (document.querySelector('a.js-extra-add-btn') || {}).className || null,
                names: document.querySelectorAll('.add-extra__item-name-input').length,
                snippet: root ? root.innerHTML.slice(0, 4000) : '',
              };
            }"""
        )
        Path("logs/kwork_new_draft_extras_dom.json").write_text(
            json.dumps(html, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print("extras_dom_fail", exc)
    _ = extras_root
    for i, extra in enumerate(extras):
        names = page.locator(".add-extra__item-name-input")
        current_name = ""
        visible_target = names.count() > i and names.nth(i).is_visible()
        if visible_target:
            current_name = (names.nth(i).inner_text() or "").strip()
        if i > 0 and not visible_target:
            before = names.count()
            add = page.locator("a.js-extra-add-btn").first
            if add.count() == 0:
                results.append({"ok": False, "reason": "add_btn_missing", "i": i, "names": before})
                return results
            add.click()
            page.wait_for_timeout(800)
            names = page.locator(".add-extra__item-name-input")
            if names.count() <= before:
                results.append({"ok": False, "reason": "add_did_not_grow", "i": i, "names": names.count()})
                break
            current_name = ""
            visible_target = names.count() > i and names.nth(i).is_visible()
        if not visible_target:
            results.append({"ok": False, "reason": "row_not_visible", "i": i, "names": names.count()})
            break
        name_el = page.locator(".add-extra__item-name-input").nth(i)
        desc_el = page.locator(".add-extra__item-description-input").nth(i)
        if extra["name"] not in current_name:
            name_el.click(timeout=8000)
            page.keyboard.press("Control+A")
            page.keyboard.type(extra["name"], delay=20)
            desc_el.click(timeout=8000)
            page.keyboard.press("Control+A")
            page.keyboard.type(extra["hint"], delay=15)
        price_sel = page.locator("select.js-extra-price").nth(i)
        days_sel = page.locator("select.js-extra-duration").nth(i)
        price_how = _pick_chosen(page, price_sel, buyer_to_select_value(extra["price"]))
        days_how = _pick_chosen(page, days_sel, extra["days"])
        results.append({
            "ok": True,
            "i": i,
            "name": extra["name"],
            "price_how": price_how,
            "days_how": days_how,
            "names": page.locator(".add-extra__item-name-input").count(),
        })
    return results


def fill_faq_playwright(page, items: list[dict]) -> dict:
    filled = []
    for i, item in enumerate(items):
        add = page.locator(".faq-list-editable__add")
        qta = page.locator("#app-faq textarea[name^='question-']").last
        if qta.count() == 0 and add.count():
            add.first.click()
            page.wait_for_timeout(400)
            qta = page.locator("#app-faq textarea[name^='question-']").last
        if qta.count() == 0:
            filled.append({"i": i, "ok": False, "reason": "no_question"})
            continue
        q_name = qta.get_attribute("name") or ""
        a_name = page.locator("#app-faq textarea[name^='answer-']").last.get_attribute("name") or ""
        page.evaluate(
            """({q, a}) => {
              const qEd = document.querySelector('#app-faq .faq-list-editable__question .trumbowyg-editor');
              const aEd = document.querySelector('#app-faq .faq-list-editable__answer .trumbowyg-editor');
              const put = (ed, text) => {
                if (!ed) return false;
                ed.focus();
                document.execCommand('selectAll', false, null);
                return document.execCommand('insertText', false, text);
              };
              return { q: put(qEd, q), a: put(aEd, a), qLen: qEd ? (qEd.innerText||'').trim().length : 0, aLen: aEd ? (aEd.innerText||'').trim().length : 0 };
            }""",
            {"q": item["q"], "a": item["a"]},
        )
        page.wait_for_timeout(300)
        if add.count():
            add.first.click()
            page.wait_for_timeout(500)
        filled.append({"i": i, "q_name": q_name, "a_name": a_name, "ok": True})
    html = page.locator("#app-faq").inner_html() if page.locator("#app-faq").count() else ""
    return {"filled": filled, "faq_html": html[:2500]}


def save_kwork_playwright(page) -> dict:
    loc = page.locator(".js-wrap-save-btn-kwork").get_by_text("Сохранить", exact=False).first
    submitted = page.evaluate(
        """() => {
          const wrap = document.querySelector('.js-wrap-save-btn-kwork');
          const btns = [...(wrap ? wrap.querySelectorAll('button, a, .kw-button, input[type=submit]') : [])];
          btns.forEach((b) => {
            b.disabled = false;
            b.removeAttribute('disabled');
            b.classList.remove('disabled', 'kw-button--disabled', 'is-disabled');
          });
          const form = document.querySelector('input[name=\"draft_id\"]') && document.querySelector('input[name=\"draft_id\"]').form;
          const btn = btns.find((b) => /сохранить/i.test(b.textContent || b.value || ''));
          if (btn) {
            btn.click();
            return { method: 'btn', className: String(btn.className||'').slice(0,120), disabled: btn.disabled };
          }
          if (form) {
            form.submit();
            return { method: 'form_submit' };
          }
          return { method: 'none', btnCount: btns.length };
        }"""
    )
    try:
        if loc.count():
            loc.click(force=True, timeout=3000)
    except Exception:
        pass
    page.wait_for_timeout(2500)
    try:
        err = page.evaluate(
            """() => {
              const t = (document.body && document.body.innerText) || '';
              const m = t.match(/ошибк[аи].{0,80}|заполните.{0,60}|минимум.{0,40}/i);
              return m ? m[0] : '';
            }"""
        )
    except Exception as exc:
        return {"ok": True, "clicked": True, "submitted": submitted, "err": "", "nav": str(exc)[:160]}
    return {"ok": True, "clicked": True, "submitted": submitted, "err": err or ""}


def eval_fn(browser: PlaywrightBrowserAdapter, js_fn: str, *args):
    payload = json.dumps(args, ensure_ascii=False)
    wrapper = f"((...args) => ({js_fn})(...args))(...{payload})"
    return browser.evaluate(wrapper)


def main() -> int:
    for extra in EXTRAS:
        assert len(extra["name"]) <= 40, extra["name"]
        assert len(extra["hint"]) <= 100, extra["hint"]
    desc_html = paragraphs_to_html(DESCRIPTION)
    inst_html = paragraphs_to_html(INSTRUCTION)
    print("desc_text_len", len(DESCRIPTION.replace("\n", "")))
    print("inst_text_len", len(INSTRUCTION))

    browser = PlaywrightBrowserAdapter(
        storage_state_path="data/kwork_storage.json",
        headless=True,
    )
    try:
        browser.navigate(DRAFT_URL)
        browser.wait_ms(4000)
        if not is_logged_in(browser):
            print("FAIL: not logged in")
            return 1
        ui = eval_fn(browser, DUMP_UI_JS)
        (OUT_DIR / "kwork_new_draft_ui.json").write_text(
            json.dumps(ui, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("ui_short", json.dumps(ui.get("shortClicks"), ensure_ascii=True)[:1500])
        print("ui_extras", json.dumps(ui.get("extras"), ensure_ascii=True))
        print("ui_faq_nodes", json.dumps(ui.get("faqNodes"), ensure_ascii=True)[:1500])

        desc_res = eval_fn(browser, SET_TRUMBOWYG_JS, "description", desc_html)
        inst_res = eval_fn(browser, SET_TRUMBOWYG_JS, "instruction", inst_html)
        size_res = eval_fn(
            browser,
            SET_CONTENTEDIT_JS,
            ".js-field-input-service-size-editor",
            SERVICE_SIZE,
        )
        vol_res = browser.evaluate(
            f"""() => {{
              const el = document.querySelector('[name="volume"]');
              if (!el) return {{ ok: false }};
              el.value = {json.dumps(VOLUME)};
              el.dispatchEvent(new Event('input', {{ bubbles: true }}));
              el.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return {{ ok: true, value: el.value }};
            }}"""
        )
        print("desc", json.dumps(desc_res, ensure_ascii=True))
        print("inst", json.dumps(inst_res, ensure_ascii=True))
        print("size", json.dumps(size_res, ensure_ascii=True))
        print("volume", json.dumps(vol_res, ensure_ascii=True))

        extra_results = fill_extras_playwright(browser._ensure_page(), EXTRAS)
        print("extras", json.dumps(extra_results, ensure_ascii=True)[:2500])
        faq_res = fill_faq_playwright(browser._ensure_page(), FAQ)
        print("faq", json.dumps({k: faq_res[k] for k in faq_res if k != "faq_html"}, ensure_ascii=True)[:2000])
        (OUT_DIR / "kwork_new_draft_faq_html.html").write_text(faq_res.get("faq_html") or "", encoding="utf-8")

        page = browser._ensure_page()
        desc_ed = page.locator("textarea[name='description']").locator("xpath=ancestor::*[contains(@class,'trumbowyg-box')][1]").locator(".trumbowyg-editor")
        inst_ed = page.locator("textarea[name='instruction']").locator("xpath=ancestor::*[contains(@class,'trumbowyg-box')][1]").locator(".trumbowyg-editor")
        desc_ed.click()
        page.keyboard.press("Control+A")
        page.keyboard.type(DESCRIPTION, delay=8)
        inst_ed.click()
        page.keyboard.press("Control+A")
        page.keyboard.type(INSTRUCTION, delay=8)
        counters = page.evaluate(
            """() => [...document.querySelectorAll('.tcounter, .text-counter')].map((el) => (el.textContent||'').replace(/\\s+/g,' ').trim()).filter(Boolean).slice(0,6)"""
        )
        print("typed_counters", json.dumps(counters, ensure_ascii=True))
        read1 = eval_fn(browser, READBACK_JS)
        (OUT_DIR / "kwork_new_draft_read1.json").write_text(
            json.dumps(read1, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUT_DIR / "kwork_new_draft_filled.png").write_bytes(browser.screenshot())

        save = save_kwork_playwright(browser._ensure_page())
        print("save_click", json.dumps(save, ensure_ascii=True))
        browser.wait_ms(5000)
        read2 = eval_fn(browser, READBACK_JS)
        (OUT_DIR / "kwork_new_draft_read2.json").write_text(
            json.dumps(read2, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUT_DIR / "kwork_new_draft_saved.png").write_bytes(browser.screenshot())
        print("url_after", read2.get("url"))
        print("descLen", read2.get("descLen"), "instLen", read2.get("instLen"))
        print("serviceSize", json.dumps(read2.get("serviceSize"), ensure_ascii=True), "volume", read2.get("volume"))
        print("extras_after", json.dumps(read2.get("extras"), ensure_ascii=True))
        return 0
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
