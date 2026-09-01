from __future__ import annotations

import json
from pathlib import Path

from src.adapters.kwork_auth import is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from deploy._fill_kwork_new_draft import save_kwork_playwright

EDIT_URL = "https://kwork.ru/edit?id=54261579"

FAQ4 = [
    {
        "q": "Что входит в стоимость?",
        "a": "1 открытый сайт без авторизации, до 200 строк, основные поля и Excel-файл. Капча, логин, несколько сайтов, расписание и Telegram в базовый кворк не входят.",
    },
    {
        "q": "Какие сроки выполнения?",
        "a": "Базовый кворк — 1 день после ссылки и списка колонок. Опции добавляют 0–2 дня, срок указан в карточке опции.",
    },
    {
        "q": "Что нужно от меня для старта?",
        "a": "Ссылка на раздел, список колонок для Excel и пример 3–5 строк, если есть. Если ТЗ нет — опишите, что хотите видеть в таблице, зафиксирую объём до старта.",
    },
    {
        "q": "Можно ли внести правки после сдачи?",
        "a": "Да, один цикл правок по составу колонок в рамках оговорённого объёма. Новый сайт, новые поля или другой формат — через опции. 3 дня отвечаю на вопросы по файлу.",
    },
]


def faq_titles(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('.faq-list-editable__question-text-formatted')]
          .map((el) => (el.textContent || '').trim())"""
    )


def delete_all(page) -> None:
    for _ in range(12):
        left = page.locator(".faq-list-editable__item").count()
        if left == 0:
            break
        page.evaluate(
            """() => {
              const el = document.querySelector('.faq-list-editable__control--remove');
              if (el) el.click();
            }"""
        )
        page.wait_for_timeout(400)
        modal = page.locator(".k-modal-outer, .k-modal-dialog, .k-modal-content")
        if modal.count():
            for label in ("Удалить", "Да", "Подтвердить", "OK"):
                btn = page.locator(".k-modal-content__footer button, .k-modal-content__footer .kw-button, .k-modal-dialog button").filter(has_text=label)
                if btn.count():
                    btn.first.click(force=True)
                    page.wait_for_timeout(400)
                    break
            else:
                page.evaluate(
                    """() => {
                      const footer = document.querySelector('.k-modal-content__footer');
                      const b = footer && [...footer.querySelectorAll('button, .kw-button, a')].find((el) =>
                        /удалить|да|подтверд/i.test(el.textContent || '')
                      );
                      if (b) b.click();
                    }"""
                )
                page.wait_for_timeout(400)
        if page.locator(".faq-list-editable__item").count() >= left:
            print("delete_stuck", left)
            break


def dump_faq(page, tag: str) -> dict:
    info = page.evaluate(
        """() => {
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const root = document.querySelector('#app-faq');
          const add = document.querySelector('.faq-list-editable__add');
          return {
            items: document.querySelectorAll('.faq-list-editable__item').length,
            editors: document.querySelectorAll('#app-faq .trumbowyg-editor').length,
            formatted: [...document.querySelectorAll('.faq-list-editable__question-text-formatted')].map((el) => n(el.textContent)),
            add: add ? { c: add.className, t: n(add.textContent), vis: !!(add.offsetParent) } : null,
            addForm: !!document.querySelector('.faq-list-editable__add-form'),
            links: [...(root ? root.querySelectorAll('a, button, span') : [])]
              .map((el) => ({ t: n(el.textContent).slice(0, 40), c: String(el.className||'').slice(0, 90) }))
              .filter((x) => x.t && x.t.length < 40)
              .slice(0, 40),
            text: root ? n(root.innerText).slice(0, 700) : '',
          };
        }"""
    )
    print("dump", tag, json.dumps(info, ensure_ascii=True))
    return info


def last_editor_question(page) -> str:
    return page.evaluate(
        """() => {
          const eds = [...document.querySelectorAll('#app-faq .faq-list-editable__question .trumbowyg-editor')];
          const el = eds[eds.length - 1];
          return el ? (el.innerText || '').trim() : '';
        }"""
    )


def form_state(page) -> dict:
    return page.evaluate(
        """() => {
          const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const eds = [...document.querySelectorAll('.trumbowyg-editor')];
          const save = document.querySelector('.js-save-kwork');
          const descTa = document.querySelector('textarea[name="description"]');
          const instTa = document.querySelector('textarea[name="instruction"]');
          return {
            descLen: eds[0] ? n(eds[0].innerText).length : 0,
            instLen: eds[1] ? n(eds[1].innerText).length : 0,
            descTaLen: descTa ? n(descTa.value).length : 0,
            instTaLen: instTa ? n(instTa.value).length : 0,
            saveDisabled: save ? !!save.disabled : null,
            extras: document.querySelectorAll('.add-extra__item-name-input').length,
            extraNames: [...document.querySelectorAll('.add-extra__item-name-input')].map((el) => n(el.textContent || el.value).slice(0, 40)),
            saves: [...document.querySelectorAll('button, a, .kw-button')].filter((el) => /сохранить/i.test(el.textContent || el.value || '')).map((el) => ({
              d: !!el.disabled,
              aria: el.getAttribute('aria-disabled'),
              vis: !!(el.offsetWidth || el.offsetHeight),
              c: String(el.className || '').slice(0, 140),
            })),
            faqTa: [...document.querySelectorAll('#app-faq textarea')].map((t) => ({
              n: t.name, l: n(t.value).length, v: n(t.value).slice(0, 40),
            })),
          };
        }"""
    )


def collapse_faq_edit(page) -> None:
    page.evaluate(
        """() => {
          const eds = [...document.querySelectorAll('#app-faq .trumbowyg-editor')];
          eds.forEach((ed) => ed.blur());
          const active = document.activeElement;
          if (active && active.blur) active.blur();
        }"""
    )
    heading = page.locator("#app-faq").get_by_text("Ответы на частые вопросы")
    if heading.count():
        heading.first.click(force=True)
    page.wait_for_timeout(400)


def click_add(page) -> None:
    for attempt in range(6):
        found = page.evaluate(
            """() => {
              const byClass = document.querySelector('.faq-list-editable__add');
              const byText = [...document.querySelectorAll('#app-faq a, #app-faq span, #app-faq button, #app-faq div')]
                .find((el) => /^добавить вопрос$/i.test((el.textContent || '').trim()));
              const el = byClass || byText;
              if (!el) return false;
              el.scrollIntoView({ block: 'center' });
              el.click();
              return true;
            }"""
        )
        if found:
            page.wait_for_timeout(500)
            return
        collapse_faq_edit(page)
        print("add_retry", attempt)
    dump_faq(page, "add_missing")
    raise RuntimeError("FAQ add button missing")


def type_in_editor(page, locator, text: str) -> str:
    locator.scroll_into_view_if_needed()
    locator.click()
    page.wait_for_timeout(120)
    handle = locator.element_handle()
    focused = page.evaluate(
        """(el) => {
          el.focus();
          const range = document.createRange();
          range.selectNodeContents(el);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          return document.activeElement === el;
        }""",
        handle,
    )
    if not focused:
        raise RuntimeError("FAQ editor not focused")
    page.keyboard.type(text, delay=8)
    return page.evaluate("(el) => (el.innerText || '').trim().slice(0, 60)", handle)


def fill_editors(page, item: dict) -> None:
    q_ed = page.locator("#app-faq .faq-list-editable__question .trumbowyg-editor").last
    a_ed = page.locator("#app-faq .faq-list-editable__answer .trumbowyg-editor").last
    q_ed.wait_for(state="visible", timeout=8000)
    print("typed_q", type_in_editor(page, q_ed, item["q"]))
    print("typed_a", type_in_editor(page, a_ed, item["a"]))
    page.wait_for_timeout(300)


def add_one(page, item: dict) -> None:
    editing = last_editor_question(page)
    if page.locator("#app-faq .faq-list-editable__question .trumbowyg-editor").count() == 0 or editing:
        click_add(page)
    fill_editors(page, item)


def apply_faqs_vue(page, items: list[dict]) -> dict:
    return page.evaluate(
        """(items) => {
          const root = document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__;
          const vm = root && (root.$children || []).find((c) => c.$options && c.$options.name === 'faq-list-editable');
          if (!vm) return { ok: false, reason: 'no_vm' };
          const existing = vm.questions.filter((q) => !q.isNew);
          while (existing.length > items.length) {
            const last = existing.pop();
            vm.questions = vm.questions.filter((q) => q.id !== last.id);
          }
          for (let i = 0; i < items.length; i++) {
            if (i < existing.length) {
              vm.updateQuestion(items[i].q, existing[i].id);
              vm.updateAnswer(items[i].a, existing[i].id);
            } else {
              vm.addNewQuestionTemplate();
              const created = vm.questions[vm.questions.length - 1];
              created.id = Date.now() * 100 + i;
              vm.updateQuestion(items[i].q, created.id);
              vm.updateAnswer(items[i].a, created.id);
              vm.saveQuestion(created.id);
            }
          }
          vm.change();
          const busEv = window.bus && window.bus._events && window.bus._events['change-faq-list-editable'];
          const fields = [...document.querySelectorAll('input, textarea')].filter((el) => /faq/i.test((el.name || '') + (el.id || ''))).map((el) => ({
            n: el.name, id: el.id, v: String(el.value || '').slice(0, 200),
          }));
          return {
            ok: true,
            getData: vm.getData(),
            faqsJson: vm.faqsJson,
            jsonSrc: vm.$options.computed && vm.$options.computed.faqsJson ? String(vm.$options.computed.faqsJson) : null,
            busCount: Array.isArray(busEv) ? busEv.length : (busEv ? 1 : 0),
            incomplete: vm.isContainsErrorsOrIncompleteQuestion,
            questions: vm.questions.map((q) => ({ id: q.id, q: q.question, isNew: !!q.isNew })),
            fields,
            saveDisabled: !!(document.querySelector('.js-save-kwork') || {}).disabled,
          };
        }""",
        [{"q": x["q"], "a": x["a"]} for x in items],
    )


def main() -> int:
    browser = PlaywrightBrowserAdapter(
        storage_state_path="data/kwork_storage.json",
        headless=True,
    )
    try:
        browser.navigate(EDIT_URL)
        browser.wait_ms(4000)
        if not is_logged_in(browser):
            print("FAIL: not logged in")
            return 1
        page = browser._ensure_page()
        print("before", json.dumps(faq_titles(page), ensure_ascii=True))
        applied = apply_faqs_vue(page, FAQ4)
        print("applied", json.dumps({"ok": applied.get("ok"), "qs": [x.get("question") for x in (applied.get("getData") or [])]}, ensure_ascii=True))
        data = (applied or {}).get("getData") or []
        if len(data) != 4 or len({x.get("question") for x in data}) != 4:
            print("FAIL: vue getData is not 4 unique")
            Path("logs/kwork_faq4.png").write_bytes(browser.screenshot())
            return 1
        ref_data = page.evaluate("() => window.appFaq.$refs.faq.getData()")
        print("ref_data", json.dumps([x.get("question") for x in (ref_data or [])], ensure_ascii=True))
        extra = page.locator(".add-extra__item-name-input").first
        extra.click()
        page.keyboard.type(" ")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(800)
        save_cls = page.evaluate("() => (document.querySelector('.js-save-kwork') || {}).className || ''")
        print("save_cls", save_cls)
        kwork_posts: list[dict] = []

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
            kwork_posts.append({"url": resp.url, "status": resp.status, "body": body})

        page.on("response", on_resp)
        clicked = page.evaluate(
            """() => {
              const i = window.jQuery('.js-save-kwork');
              i.prop('disabled', false).removeClass('disabled');
              i.trigger('click');
              return { cls: i.attr('class'), disabled: i.prop('disabled') };
            }"""
        )
        print("jq_click", json.dumps(clicked, ensure_ascii=True))
        page.wait_for_timeout(5000)
        print("kwork_posts", json.dumps(kwork_posts, ensure_ascii=True)[:2500])
        browser.navigate(EDIT_URL)
        browser.wait_ms(4000)
        titles = faq_titles(page)
        print("after_reload", json.dumps(titles, ensure_ascii=True))
        Path("logs/kwork_faq4.png").write_bytes(browser.screenshot())
        return 0 if len(set(titles)) == 4 else 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
