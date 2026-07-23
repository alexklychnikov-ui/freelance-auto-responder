"""One-time Yandex Uslugi login in dedicated profile → storage_state for VPS."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROFILE_DIR = Path("data/yandex_browser_profile")
OUT = Path("data/yandex_storage.json")


def main() -> int:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("Откроется окно Chrome. Залогинься в Яндекс (Исполнители / Услуги).")
    print(
        "Когда увидишь кабинет заказов "
        "(https://uslugi.yandex.ru/cab/orders) — нажми Enter здесь..."
    )

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
        )
        page = ctx.new_page()
        page.goto(
            "https://uslugi.yandex.ru/cab/orders?type=new",
            wait_until="domcontentloaded",
        )
        input()
        try:
            page.goto(
                "https://uslugi.yandex.ru/cab/orders?type=new",
                wait_until="domcontentloaded",
                timeout=60000,
            )
        except Exception:
            pass
        page.wait_for_timeout(2000)
        check = page.evaluate(
            """() => ({
              url: location.href,
              isPassport: /passport\\.yandex/i.test(location.href),
              hasOrderLinks: !!document.querySelector('a[href*="/order/"]'),
              title: document.title || '',
            })"""
        )
        if check.get("isPassport"):
            print("FAIL: всё ещё passport (не залогинен)")
            ctx.close()
            return 1
        url = str(check.get("url") or "")
        if "/registration" in url or "/cab/" not in url:
            print(
                "FAIL: нет кабинета исполнителя — открыт",
                url,
                "\nПройди регистрацию исполнителя на uslugi.yandex.ru "
                "и дождись страницы /cab/orders с заказами.",
            )
            ctx.close()
            return 1
        if not check.get("hasOrderLinks"):
            print(
                "WARN: /cab/orders открыт, но ссылок /order/ нет — "
                "сессия сохранится, но скан может быть пустым."
            )
        OUT.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(OUT))
        ctx.close()

    print(f"OK: {OUT}")
    print("check:", check)
    vps = "LightRAG_Naive:/opt/freelance-responder/data/yandex_storage.json"
    r = subprocess.run(["scp", str(OUT), vps], capture_output=True, text=True)
    if r.returncode == 0:
        print("OK: uploaded to VPS")
        subprocess.run(
            [
                "ssh",
                "LightRAG_Naive",
                "sudo systemctl restart freelance-responder",
            ],
            check=False,
        )
    else:
        print("upload manually:", r.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
