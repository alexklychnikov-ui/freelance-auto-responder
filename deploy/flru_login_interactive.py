"""FL.ru login via native Chrome (SmartCaptcha) → storage_state for VPS."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROFILE_DIR = Path("data/flru_browser_profile")
OUT = Path("data/flru_storage.json")
LOGIN_URL = "https://www.fl.ru/account/login/"
LISTING = "https://www.fl.ru/projects/?kind=1"


def _chrome_exe() -> Path:
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(key, "")
        if not base:
            continue
        candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Chrome не найден. Установи Google Chrome или используй "
        "deploy/export_flru_from_chrome.py"
    )


def _export_storage_state(profile_dir: Path, out_path: Path) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            locale="ru-RU",
            viewport={"width": 1400, "height": 900},
        )
        page = ctx.new_page()
        page.goto(LISTING, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)
        check = page.evaluate(
            """() => ({
              url: location.href,
              isLogin: /fl\\.ru\\/.*login/i.test(location.href),
              hasProjects: !!document.querySelector('a[href*="/projects/"]'),
              title: document.title || '',
            })"""
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(out_path))
        ctx.close()
    return check


def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _chrome_exe()

    print("FL.ru использует Yandex SmartCaptcha — в Playwright капча часто пустая.")
    print("Логинимся в обычном Chrome (не headless).")
    print()
    print(f"1) Откроется Chrome с профилем: {PROFILE_DIR.resolve()}")
    print(f"2) Залогинься: {LOGIN_URL}")
    print("3) Пройди капчу, открой ленту заказов")
    print(f"   {LISTING}")
    print("4) Закрой ВСЕ окна этого Chrome (важно!)")
    print("5) Вернись сюда и нажми Enter")
    print()

    proc = subprocess.Popen(
        [
            str(chrome),
            f"--user-data-dir={PROFILE_DIR.resolve()}",
            "--no-first-run",
            "--no-default-browser-check",
            LOGIN_URL,
        ]
    )
    input("Enter после логина и закрытия Chrome… ")
    if proc.poll() is None:
        print("Закрываю Chrome…")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Дождаться освобождения user-data-dir на Windows
    for _ in range(15):
        lock = PROFILE_DIR / "SingletonLock"
        if not lock.exists():
            break
        time.sleep(1)

    try:
        check = _export_storage_state(PROFILE_DIR, OUT)
    except Exception as exc:
        print(f"FAIL export: {exc}")
        print("Убедись, что все окна Chrome с этим профилем закрыты.")
        return 1

    if check.get("isLogin"):
        print("FAIL: всё ещё страница логина — сессия не сохранена")
        print("check:", check)
        return 1
    if not check.get("hasProjects"):
        print("WARN: ссылок /projects/ нет — сессия может быть слабой")
    print(f"OK: {OUT}")
    print("check:", check)

    vps = "LightRAG_Naive:/opt/freelance-responder/data/flru_storage.json"
    r = subprocess.run(["scp", str(OUT), vps], capture_output=True, text=True)
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
