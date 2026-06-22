"""Export Kwork session: copy full Chrome Default profile, then storage_state."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def export_storage(out_path: Path) -> int:
    from playwright.sync_api import sync_playwright

    chrome_ud = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
    tmp = Path(tempfile.mkdtemp(prefix="kwork_prof_"))
    try:
        shutil.copy2(chrome_ud / "Local State", tmp / "Local State")
        shutil.copytree(
            chrome_ud / "Default",
            tmp / "Default",
            ignore=shutil.ignore_patterns(
                "Cache", "Code Cache", "GPUCache", "Service Worker", "blob_storage"
            ),
        )

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(tmp),
                channel="chrome",
                headless=True,
            )
            page = ctx.new_page()
            page.goto("https://kwork.ru/", wait_until="networkidle", timeout=90000)
            page.wait_for_timeout(2000)
            check = page.evaluate(
                """() => ({
                  hasLogin: [...document.querySelectorAll('a')].some(a => (a.textContent||'').trim()==='Вход'),
                  hasInbox: !!document.querySelector('a[href*="/inbox"]'),
                })"""
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(out_path))
            ctx.close()

        data = json.loads(out_path.read_text(encoding="utf-8"))
        names = [c["name"] for c in data.get("cookies", []) if "kwork" in c.get("domain", "")]
        print("kwork cookies:", names)
        print("check:", check)
        auth = [n for n in names if not n.startswith("_ym")]
        if not auth and check.get("hasLogin"):
            return 1
        if check.get("hasLogin"):
            print("FAIL: guest session")
            return 1
        print(f"OK -> {out_path} ({len(names)} kwork cookies)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/kwork_storage.json")
    sys.exit(export_storage(dest))
