from __future__ import annotations

import json
import re
from pathlib import Path

from deploy._fill_kwork_landing import fill_desc_or_inst, write_textarea
from deploy._fill_kwork_price_monitor import invoke_de
from deploy.kwork_listing_landing import LANDING
from deploy.kwork_listing_price_monitor import PRICE
from deploy.kwork_listing_skel import assemble, to_kwork_html, validate
from src.adapters.kwork_auth import is_logged_in
from src.browser.playwright_adapter import PlaywrightBrowserAdapter

LOG_DIR = Path("logs")
GLUE = ("логина2", "ruКакие", "себе2)")

KWWORKS = [
    {
        "id": "52958975",
        "url": "https://kwork.ru/edit?id=52958975",
        "listing": PRICE,
        "log": "kwork_desc_breaks_52958975",
    },
    {
        "id": "51315832",
        "url": "https://kwork.ru/edit?id=51315832",
        "listing": LANDING,
        "log": "kwork_desc_breaks_51315832",
    },
]


def p_count(html: str) -> int:
    return len(re.findall(r"<p[\s>]", html or "", flags=re.I))


def dump_desc_inst(page) -> dict:
    return page.evaluate(
        """() => {
          const live = (sel) => [...document.querySelectorAll(sel)]
            .filter((el) => !el.closest('.js-my-extras-templates__item'));
          const taDesc = document.querySelector('#step1-description');
          const taInst = document.querySelector('#step1-instruction');
          const boxOf = (ta) => ta && ta.closest('.trumbowyg-box');
          const edOf = (ta) => {
            const box = boxOf(ta);
            return box ? box.querySelector('.trumbowyg-editor') : null;
          };
          const descEd = edOf(taDesc);
          const instEd = edOf(taInst);
          const descVal = taDesc ? String(taDesc.value || '') : '';
          const instVal = taInst ? String(taInst.value || '') : '';
          const descInner = descEd ? String(descEd.innerText || '') : '';
          const instInner = instEd ? String(instEd.innerText || '') : '';
          let extrasMy = live('input[name="my_extras_name[]"]')
            .map((el) => String(el.value || '').trim())
            .filter(Boolean);
          if (!extrasMy.length) {
            try {
              const cur = window.MyExtras && MyExtras._currentExtras && MyExtras._currentExtras.my;
              if (Array.isArray(cur) && cur.length) {
                extrasMy = cur.map((x) => {
                  if (typeof x === 'string') return x.trim();
                  return String((x && (x.name || x.title)) || '').trim();
                }).filter(Boolean);
              }
            } catch (e) {}
          }
          const pOf = (s) => ((s || '').match(/<p[\\s>]/gi) || []).length;
          return {
            url: location.href,
            descVal,
            instVal,
            descP: pOf(descVal),
            instP: pOf(instVal),
            descInner,
            instInner,
            descLen: descVal.replace(/<[^>]+>/g, '').trim().length,
            instLen: instVal.replace(/<[^>]+>/g, '').trim().length,
            extrasMy,
            extrasMyLen: extrasMy.length,
          };
        }"""
    )


def glue_hits(blob: str) -> list[str]:
    return [g for g in GLUE if g in (blob or "")]


def accept_ok(d: dict, extras_before: list[str]) -> dict:
    desc_val = d.get("descVal") or ""
    inst_val = d.get("instVal") or ""
    desc_inner = (d.get("descInner") or "").replace("\r\n", "\n")
    inst_inner = (d.get("instInner") or "").replace("\r\n", "\n")
    blob = desc_val + inst_val + desc_inner + inst_inner
    extras = list(d.get("extrasMy") or [])
    p_desc = int(d.get("descP") or 0)
    p_inst = int(d.get("instP") or 0)
    glue = glue_hits(blob)
    nl_tasks = "Какие задачи закрывает\n" in desc_inner
    nl_inst = "Пришлите:\n" in inst_inner or "Пришлите\n" in inst_inner
    i1, i2, i3 = inst_inner.find("1)"), inst_inner.find("2)"), inst_inner.find("3)")
    steps_split = i1 >= 0 and i2 > i1 and i3 > i2 and "\n" in inst_inner[i1:i3 + 2]
    extras_ok = len(extras) >= 5
    if extras_before and len(extras) < len(extras_before):
        extras_ok = False
    ok = (
        p_desc >= 6
        and p_inst >= 4
        and not glue
        and nl_tasks
        and nl_inst
        and steps_split
        and extras_ok
    )
    return {
        "ok": ok,
        "descP": p_desc,
        "instP": p_inst,
        "glue": glue,
        "nl_tasks": nl_tasks,
        "nl_inst": nl_inst,
        "steps_split": steps_split,
        "extrasMyLen": len(extras),
        "extrasMy": extras,
        "extras_ok": extras_ok,
    }


def hook_save(page) -> None:
    page.evaluate(
        """() => {
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
              } catch (e) {}
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


def goto_edit(page, url: str, kwork_id: str) -> None:
    href = ""
    try:
        href = page.evaluate("() => location.href") or ""
    except Exception:
        href = ""
    if f"edit?id={kwork_id}" not in href:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    else:
        page.reload(wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_selector("#step1-description, .js-kwork-title-editor", timeout=25000)
    page.wait_for_timeout(5000)
    try:
        page.wait_for_function("() => !!(window.jQuery || window.$)", timeout=20000)
    except Exception as exc:
        print("jq_after_reload", str(exc)[:160])


def save_edit(page, captured: dict) -> dict:
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
    ajax = page.evaluate(
        """() => ({
          data: window.__saveResp,
          raw: String(window.__saveRespRaw || '').slice(0, 4000),
          href: location.href,
        })"""
    )
    if captured.get("status") and not saved.get("status"):
        saved["status"] = captured["status"]
        saved["url"] = captured.get("url") or saved.get("url")
        saved["ok"] = True
        saved["how"] = "De(false)"
    js = None
    raw_text = captured.get("text") or ajax.get("raw") or ""
    if str(raw_text).strip().startswith("{") or str(raw_text).strip().startswith("["):
        try:
            js = json.loads(raw_text)
        except Exception:
            js = None
    if js is None and isinstance(ajax.get("data"), dict):
        js = ajax.get("data")
    success = None if js is None else (js.get("success") if "success" in js else js.get("result"))
    result_ok = isinstance(js, dict) and (js.get("result") == "success" or js.get("success") is True)
    if not result_ok and saved.get("status") == 200 and "draft_save" not in (saved.get("url") or ""):
        href_now = str(ajax.get("href") or "")
        if "/edit?id=" not in href_now:
            result_ok = True
    saved["result_ok"] = result_ok
    saved["success"] = success
    saved["js"] = js
    saved["ajax"] = ajax
    try:
        page.unroute("**/save_kwork*")
        page.unroute("**/draft_save*")
    except Exception:
        pass
    return saved


def refill_one(page, spec: dict) -> dict:
    kwork_id = spec["id"]
    url = spec["url"]
    built = assemble(spec["listing"])
    problems = validate(built)
    if problems:
        print("FAIL validate", kwork_id, problems)
        return {"id": kwork_id, "ok": False, "err": problems}
    desc_html = to_kwork_html(built["description"])
    inst_html = to_kwork_html(built["instruction"])
    print("html_p", json.dumps({
        "id": kwork_id,
        "descP": p_count(desc_html),
        "instP": p_count(inst_html),
        "descHtmlLen": len(desc_html),
        "instHtmlLen": len(inst_html),
        "descPlain": len(built["description"]),
        "instPlain": len(built["instruction"]),
    }, ensure_ascii=True))
    if p_count(desc_html) < 6 or p_count(inst_html) < 4:
        return {"id": kwork_id, "ok": False, "err": "html_p_low"}

    goto_edit(page, url, kwork_id)

    before = dump_desc_inst(page)
    extras_before = list(before.get("extrasMy") or [])
    print("before", json.dumps({
        "id": kwork_id,
        "descP": before.get("descP"),
        "instP": before.get("instP"),
        "descHead": (before.get("descVal") or "")[:120],
        "instHead": (before.get("instVal") or "")[:120],
        "extrasMyLen": len(extras_before),
        "extrasMy": extras_before,
        "glue": glue_hits((before.get("descVal") or "") + (before.get("instVal") or "") + (before.get("descInner") or "") + (before.get("instInner") or "")),
    }, ensure_ascii=True)[:2500])
    if len(extras_before) < 5:
        print("FAIL extras.my already empty, abort save", kwork_id, extras_before)
        return {"id": kwork_id, "ok": False, "err": "extras_empty_before", "extrasMy": extras_before}

    print("desc", json.dumps(fill_desc_or_inst(page, "description", desc_html), ensure_ascii=True)[:1500])
    print("inst", json.dumps(fill_desc_or_inst(page, "instruction", inst_html), ensure_ascii=True)[:1500])
    write_textarea(page, "#step1-description", desc_html)
    write_textarea(page, "#step1-instruction", inst_html)
    filled = dump_desc_inst(page)
    di = (filled.get("descInner") or "").replace("\r\n", "\n")
    ii = (filled.get("instInner") or "").replace("\r\n", "\n")
    print("filled", json.dumps({
        "descP": filled.get("descP"),
        "instP": filled.get("instP"),
        "descHead": (filled.get("descVal") or "")[:160],
        "instHead": (filled.get("instVal") or "")[:160],
        "descNl": "Какие задачи закрывает\n" in di,
        "instNl": "\n1)" in "\n" + ii,
        "extrasMyLen": filled.get("extrasMyLen"),
    }, ensure_ascii=True)[:2000])
    if int(filled.get("descP") or 0) < 6 or int(filled.get("instP") or 0) < 4:
        print("FAIL p-count before save", filled.get("descP"), filled.get("instP"))
        return {"id": kwork_id, "ok": False, "err": "p_count_pre_save", "filled": {
            "descP": filled.get("descP"), "instP": filled.get("instP"),
        }}

    hook_save(page)
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
    captured: dict = {"status": None, "text": "", "req": "", "url": ""}
    saved = save_edit(page, captured)
    print("save", json.dumps({
        "ok": saved.get("ok"),
        "how": saved.get("how"),
        "status": saved.get("status"),
        "url": saved.get("url"),
        "success": saved.get("success"),
        "result_ok": saved.get("result_ok"),
        "js": saved.get("js"),
    }, ensure_ascii=True)[:2500])
    if not saved.get("ok") or saved.get("how") != "De(false)" or saved.get("status") != 200:
        print("FAIL save", saved)
        return {"id": kwork_id, "ok": False, "err": "save", "saved": saved}
    if "draft_save" in (saved.get("url") or "") or "draft_save" in (captured.get("url") or ""):
        print("FAIL used draft_save")
        return {"id": kwork_id, "ok": False, "err": "draft_save"}
    if not saved.get("result_ok"):
        print("FAIL save result", saved.get("js"))
        return {"id": kwork_id, "ok": False, "err": "result_not_success", "js": saved.get("js")}

    page.wait_for_timeout(2000)
    goto_edit(page, url, kwork_id)
    verify = dump_desc_inst(page)
    acc = accept_ok(verify, extras_before)
    out = {
        "id": kwork_id,
        "ok": acc["ok"],
        "descP": acc["descP"],
        "instP": acc["instP"],
        "extrasMyLen": acc["extrasMyLen"],
        "extrasMy": acc["extrasMy"],
        "accept": acc,
        "descHead": (verify.get("descVal") or "")[:180],
        "instHead": (verify.get("instVal") or "")[:180],
        "descInnerHead": (verify.get("descInner") or "")[:200],
        "instInnerHead": (verify.get("instInner") or "")[:200],
        "save": {"status": saved.get("status"), "success": saved.get("success"), "how": saved.get("how")},
    }
    Path(LOG_DIR / f"{spec['log']}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("verify", json.dumps(out, ensure_ascii=True)[:4000])
    if not acc["ok"]:
        print("FAIL reload accept", json.dumps(acc, ensure_ascii=True))
    return out


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    browser = PlaywrightBrowserAdapter(
        storage_state_path="data/kwork_storage.json",
        headless=False,
    )
    results = []
    try:
        page = browser._ensure_page()
        browser.navigate(KWWORKS[0]["url"])
        page.wait_for_selector("#step1-description, .js-kwork-title-editor, a[href='/login']", timeout=25000)
        page.wait_for_timeout(1500)
        if not is_logged_in(browser):
            print("FAIL: not logged in")
            return 1
        for spec in KWWORKS:
            results.append(refill_one(page, spec))
        summary = [{
            "id": r.get("id"),
            "ok": r.get("ok"),
            "descP": r.get("descP"),
            "instP": r.get("instP"),
            "extrasMyLen": r.get("extrasMyLen"),
            "err": r.get("err"),
        } for r in results]
        print("SUMMARY", json.dumps(summary, ensure_ascii=True))
        Path(LOG_DIR / "kwork_desc_breaks_summary.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0 if all(r.get("ok") for r in results) else 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
