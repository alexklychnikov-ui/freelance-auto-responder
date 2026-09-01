from __future__ import annotations

import json
from pathlib import Path

from deploy._fill_kwork_new_draft import fill_extras_playwright
from deploy._fix_kwork_faq4 import apply_faqs_vue
from deploy.kwork_listing_skel import assemble, validate
from src.adapters.kwork_auth import is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

DRAFT_URL = "https://kwork.ru/new?draft_id=54298174"
TYPE_RADIO = "#attribute_item_212032"


def type_trumbowyg(page, name: str, text: str) -> None:
    ed = (
        page.locator(f"textarea[name='{name}']")
        .locator("xpath=ancestor::*[contains(@class,'trumbowyg-box')][1]")
        .locator(".trumbowyg-editor")
    )
    ed.first.click()
    page.keyboard.press("Control+A")
    page.keyboard.type(text, delay=8)


def main() -> int:
    built = assemble()
    problems = validate(built)
    if problems:
        print("FAIL validate", problems)
        return 1
    browser = PlaywrightBrowserAdapter(
        storage_state_path="data/kwork_storage.json",
        headless=True,
    )
    posts: list[dict] = []
    try:
        browser.navigate(DRAFT_URL)
        browser.wait_ms(4000)
        if not is_logged_in(browser):
            print("FAIL: not logged in")
            return 1
        page = browser._ensure_page()

        def on_resp(resp) -> None:
            if "kwork.ru" not in resp.url or "yandex" in resp.url:
                return
            if resp.request.method != "POST":
                return
            body = ""
            try:
                body = resp.text()[:400]
            except Exception:
                body = ""
            posts.append({"url": resp.url, "status": resp.status, "body": body})

        page.on("response", on_resp)
        page.on("dialog", lambda d: d.accept())
        radio = page.locator(TYPE_RADIO)
        if radio.count():
            page.locator("label[for='attribute_item_212032']").click(force=True)
            page.wait_for_timeout(300)
        print("type_checked", page.evaluate("() => (document.querySelector('#attribute_item_212032')||{}).checked"))

        type_trumbowyg(page, "description", built["description"])
        type_trumbowyg(page, "instruction", built["instruction"])
        size = page.locator(".js-field-input-service-size-editor")
        if size.count() and size.first.is_visible():
            size.first.click()
            page.keyboard.press("Control+A")
            page.keyboard.type(built["service_size"], delay=15)
        page.evaluate(
            """(vol) => {
              const el = document.querySelector('[name="volume"]');
              if (!el) return false;
              el.value = vol;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }""",
            built["volume"],
        )
        print("landed", page.evaluate("() => location.href"))
        extras = fill_extras_playwright(page, built["extras"])
        print("extras", json.dumps(extras, ensure_ascii=True)[:2000])
        wanted_extra = next((e for e in built["extras"] if "той же структуре" in e["name"]), None)
        if wanted_extra:
            extra_i = built["extras"].index(wanted_extra)
            # Kwork extra grid has no buyer 500 (select 625): 400 or 800. Use 800.
            price_set = page.evaluate(
                """(i) => {
                  const el = document.querySelectorAll('select.js-extra-price')[i];
                  const $ = window.jQuery;
                  if ($ && el) $(el).val('1000').trigger('chosen:updated').trigger('change');
                  return el ? el.value : null;
                }""",
                extra_i,
            )
            print("price_set", price_set)
        names = page.locator(".add-extra__item-name-input")
        dels = page.locator(".js-add-extra-delete")
        for i in range(names.count() - 1, -1, -1):
            if not (names.nth(i).inner_text() or "").strip() and dels.count() > i:
                try:
                    dels.nth(i).click(force=True, timeout=3000)
                    page.wait_for_timeout(400)
                except Exception as exc:
                    print("extra_del_skip", i, str(exc)[:120])
        page.wait_for_timeout(200)
        applied = apply_faqs_vue(page, built["faqs"])
        qs = [x.get("question") for x in (applied.get("getData") or [])]
        print("faq", json.dumps({"ok": applied.get("ok"), "qs": qs}, ensure_ascii=True))
        if len(set(qs)) != 7:
            print("FAIL faq count", qs)
            Path("logs/kwork_report_fill.png").write_bytes(browser.screenshot())
            return 1
        page.wait_for_timeout(2100)
        href = page.evaluate("() => location.href") or ""
        if "/edit?id=" in href:
            saved = page.evaluate(
                """() => {
                  try {
                    const i = window.jQuery && window.jQuery('.js-save-kwork');
                    if (i && i.length) i.prop('disabled', false).removeClass('disabled');
                    if (typeof De === 'function') {
                      De(false);
                      return { ok: true, how: 'De(false)' };
                    }
                    if (i && i.length) {
                      i.trigger('click');
                      return { ok: true, how: 'jq_click' };
                    }
                    return { ok: false, err: 'no_De' };
                  } catch (e) {
                    return { ok: false, err: String(e) };
                  }
                }"""
            )
        else:
            saved = page.evaluate(
                """() => {
                  try {
                    window.KworkSaveModule.saveDraft();
                    return { ok: true, how: 'saveDraft' };
                  } catch (e) {
                    return { ok: false, err: String(e) };
                  }
                }"""
            )
        print("save", json.dumps(saved, ensure_ascii=True))
        page.wait_for_timeout(8000)
        print("save_err", page.evaluate(
            """() => {
              const t = (document.body && document.body.innerText) || '';
              const m = t.match(/ошибк[аи].{0,120}|заполните.{0,80}|минимум.{0,60}|некорректн.{0,80}/i);
              return m ? m[0] : '';
            }"""
        ))
        print("posts", json.dumps(posts, ensure_ascii=True)[:2000])
        Path("logs/kwork_report_fill.png").write_bytes(browser.screenshot())
        browser.navigate(DRAFT_URL)
        browser.wait_ms(4000)
        verify = page.evaluate(
            """() => {
              const n = (s) => (s || '').replace(/\\s+/g, ' ').trim();
              const faqs = [...document.querySelectorAll('.faq-list-editable__question-text-formatted')].map((el) => n(el.textContent));
              const extras = [...document.querySelectorAll('.add-extra__item-name-input')].map((el) => n(el.textContent)).filter(Boolean);
              let faqAs = [];
              try {
                const root = document.querySelector('#app-faq') && document.querySelector('#app-faq').__vue__;
                const vm = root && (root.$children || []).find((c) => c.$options && c.$options.name === 'faq-list-editable');
                if (vm && vm.getData) faqAs = vm.getData().map((x) => n(x.answer || ''));
              } catch (e) {}
              const desc = document.querySelectorAll('.trumbowyg-editor')[0];
              const inst = document.querySelectorAll('.trumbowyg-editor')[1];
              const typeEl = document.querySelector('#attribute_item_212032');
              const prices = [...document.querySelectorAll('select.js-extra-price')].map((el) => el.value);
              return {
                url: location.href,
                title: n((document.querySelector('.js-kwork-title-editor') || {}).innerText || ''),
                typeOn: !!(typeEl && typeEl.checked),
                descLen: desc ? n(desc.innerText).length : 0,
                instLen: inst ? n(inst.innerText).length : 0,
                size: n((document.querySelector('.js-field-input-service-size-editor') || {}).innerText || ''),
                extras,
                faqs,
                faqAs,
                prices,
              };
            }"""
        )
        print("verify", json.dumps(verify, ensure_ascii=True))
        Path("logs/kwork_report_verify.png").write_bytes(browser.screenshot())
        extra_names = verify.get("extras") or []
        faq_as = verify.get("faqAs") or []
        weekly = any("Еженедельный" in x or "еженедельный" in x for x in extra_names + faq_as)
        ok = (
            (verify.get("descLen") or 0) >= 100
            and (verify.get("instLen") or 0) >= 100
            and 5 <= len(extra_names) <= 7
            and len(set(verify.get("faqs") or [])) == 7
            and verify.get("typeOn")
            and any("Ещё один отчёт по той же структуре" in x for x in extra_names)
            and not weekly
        )
        return 0 if ok else 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
