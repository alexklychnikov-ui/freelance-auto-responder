from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.kwork_auth import (
    KworkAuthError,
    KworkCredentials,
    ensure_logged_in,
    is_logged_in,
    login,
)


class AuthBrowser:
    def __init__(self, logged_in: bool = False) -> None:
        self.logged_in = logged_in
        self.urls: list[str] = []
        self.eval_calls: list[str] = []

    def navigate(self, url: str) -> None:
        self.urls.append(url)

    def evaluate(self, js: str):
        self.eval_calls.append(js)
        if "location.pathname.startsWith" in js or "Вход" in js:
            return self.logged_in
        if "btn.click()" in js:
            self.logged_in = True
            return {"ok": True}
        return None

    def snapshot(self) -> str:
        return ""

    def click(self, _sel: str) -> None:
        pass

    def fill(self, _sel: str, _text: str) -> None:
        pass

    def screenshot(self) -> bytes:
        return b""


def test_is_logged_in_false() -> None:
    browser = AuthBrowser(logged_in=False)
    assert is_logged_in(browser) is False


def test_is_logged_in_true() -> None:
    browser = AuthBrowser(logged_in=True)
    assert is_logged_in(browser) is True


def test_ensure_logged_in_skips_when_session_active() -> None:
    browser = AuthBrowser(logged_in=True)
    creds = KworkCredentials(login="user@test.ru", password="secret")
    ensure_logged_in(browser, creds)
    assert browser.urls == []


def test_ensure_logged_in_performs_login() -> None:
    browser = AuthBrowser(logged_in=False)
    creds = KworkCredentials(login="user@test.ru", password="secret")
    ensure_logged_in(browser, creds)
    assert "https://kwork.ru/login" in browser.urls
    assert browser.logged_in is True


def test_ensure_logged_in_without_creds_raises() -> None:
    browser = AuthBrowser(logged_in=False)
    with pytest.raises(KworkAuthError, match="KWORK_LOGIN"):
        ensure_logged_in(browser, None)


def test_login_captcha_raises() -> None:
    browser = MagicMock()
    browser.evaluate.return_value = {"ok": False, "reason": "captcha"}
    with pytest.raises(KworkAuthError, match="CAPTCHA"):
        login(browser, KworkCredentials(login="a", password="b"))
