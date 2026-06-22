"""Export storage_state from data/kwork_browser_profile."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROFILE = Path("data/kwork_browser_profile")
OUT = Path("data/kwork_storage.json")


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="chrome",
            headless=True,
        )
        page = ctx.new_page()
        try:
            page.goto("https://kwork.ru/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        check = page.evaluate(
            """() => ({
              url: location.href,
              hasLogin: [...document.querySelectorAll('a')].some(a => (a.textContent||'').trim()==='Вход'),
              hasInbox: !!document.querySelector('a[href*="/inbox"]'),
            })"""
        )
        OUT.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(OUT))
        ctx.close()

    data = json.loads(OUT.read_text(encoding="utf-8"))
    names = [c["name"] for c in data.get("cookies", []) if "kwork" in c.get("domain", "")]
    print("check:", check)
    print("kwork cookies:", names)
    print("total cookies:", len(data.get("cookies", [])))
    if check.get("hasLogin"):
        print("FAIL: guest session")
        return 1

    vps = "LightRAG_Naive:/opt/freelance-responder/data/kwork_storage.json"
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
