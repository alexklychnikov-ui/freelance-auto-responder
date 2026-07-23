"""Export storage_state from data/yandex_browser_profile."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROFILE = Path("data/yandex_browser_profile")
OUT = Path("data/yandex_storage.json")


def main() -> int:
    from playwright.sync_api import sync_playwright

    if not PROFILE.exists():
        print(f"FAIL: нет профиля {PROFILE} — сначала yandex_login_interactive.py")
        return 1

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="chrome",
            headless=True,
        )
        page = ctx.new_page()
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
            })"""
        )
        OUT.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(OUT))
        ctx.close()

    data = json.loads(OUT.read_text(encoding="utf-8"))
    domains = sorted(
        {
            c.get("domain", "")
            for c in data.get("cookies", [])
            if "yandex" in c.get("domain", "")
        }
    )
    print("check:", check)
    print("yandex cookie domains:", domains)
    print("total cookies:", len(data.get("cookies", [])))
    if check.get("isPassport"):
        print("FAIL: guest / passport session")
        return 1

    vps = "LightRAG_Naive:/opt/freelance-responder/data/yandex_storage.json"
    r = subprocess.run(["scp", str(OUT), vps], capture_output=True, text=True)
    if r.returncode == 0:
        print("OK: uploaded to VPS")
        subprocess.run(
            ["ssh", "LightRAG_Naive", "sudo systemctl restart freelance-responder"],
            check=False,
        )
    else:
        print("upload failed:", r.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
