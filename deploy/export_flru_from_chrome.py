"""Export FL.ru session from your main Google Chrome profile (already logged in)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def export_storage(out_path: Path) -> int:
    from playwright.sync_api import sync_playwright

    chrome_ud = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
    if not chrome_ud.is_dir():
        print("FAIL: Chrome User Data not found")
        return 1

    print("ВАЖНО: закрой все окна Google Chrome перед экспортом.")
    input("Enter когда Chrome полностью закрыт… ")

    tmp = Path(tempfile.mkdtemp(prefix="flru_prof_"))
    try:
        shutil.copy2(chrome_ud / "Local State", tmp / "Local State")
        shutil.copytree(
            chrome_ud / "Default",
            tmp / "Default",
            ignore=shutil.ignore_patterns(
                "Cache",
                "Code Cache",
                "GPUCache",
                "Service Worker",
                "blob_storage",
            ),
        )

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(tmp),
                channel="chrome",
                headless=True,
                ignore_default_args=["--enable-automation"],
            )
            page = ctx.new_page()
            page.goto(
                "https://www.fl.ru/projects/?kind=1",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            page.wait_for_timeout(2500)
            check = page.evaluate(
                """() => ({
                  isLogin: /fl\\.ru\\/.*login/i.test(location.href),
                  hasProjects: !!document.querySelector('a[href*="/projects/"]'),
                  url: location.href,
                })"""
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(out_path))
            ctx.close()

        data = json.loads(out_path.read_text(encoding="utf-8"))
        fl_cookies = [
            c for c in data.get("cookies", []) if "fl.ru" in c.get("domain", "")
        ]
        print("fl.ru cookies:", len(fl_cookies))
        print("check:", check)
        if check.get("isLogin"):
            print("FAIL: в Chrome ты не залогинен на FL.ru")
            print("Сначала зайди на fl.ru в обычном Chrome, потом повтори скрипт.")
            return 1
        print(f"OK -> {out_path}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/flru_storage.json")
    code = export_storage(dest)
    if code != 0:
        return code
    vps = "LightRAG_Naive:/opt/freelance-responder/data/flru_storage.json"
    r = subprocess.run(["scp", str(dest), vps], capture_output=True, text=True)
    if r.returncode == 0:
        print("OK: uploaded to VPS")
        subprocess.run(
            ["ssh", "LightRAG_Naive", "sudo systemctl restart freelance-responder"],
            check=False,
        )
    else:
        print("upload manually:", r.stderr or r.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
