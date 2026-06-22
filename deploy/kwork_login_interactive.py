"""One-time Kwork login in dedicated profile → storage_state for VPS."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROFILE_DIR = Path("data/kwork_browser_profile")
OUT = Path("data/kwork_storage.json")


def main() -> int:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("Откроется окно Chrome. Залогинься на kwork.ru если нужно.")
    print("Когда увидишь свой аккаунт (не «Вход») — нажми Enter здесь...")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
        )
        page = ctx.new_page()
        page.goto("https://kwork.ru/login", wait_until="domcontentloaded")
        input()
        try:
            page.goto("https://kwork.ru/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        check = page.evaluate(
            """() => ({
              hasLogin: [...document.querySelectorAll('a')].some(a => (a.textContent||'').trim()==='Вход'),
              hasInbox: !!document.querySelector('a[href*="/inbox"]'),
            })"""
        )
        if check.get("hasLogin"):
            print("FAIL: всё ещё гость")
            ctx.close()
            return 1
        OUT.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(OUT))
        ctx.close()

    print(f"OK: {OUT}")
    vps = "LightRAG_Naive:/opt/freelance-responder/data/kwork_storage.json"
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
