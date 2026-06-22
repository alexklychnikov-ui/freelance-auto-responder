from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from src.browser.base import BrowserClient

logger = logging.getLogger(__name__)

KWORK_LOGIN_URL = "https://kwork.ru/login"

IS_LOGGED_IN_JS = """
(() => {
  const loginLink = [...document.querySelectorAll('a')].find((a) => {
    const text = (a.textContent || '').trim();
    const href = a.getAttribute('href') || '';
    return text === 'Вход' || href === '/login' || href.endsWith('/login');
  });
  const sellerNav = document.querySelector('a[href="/seller"], a[href*="/inbox"]');
  const chatNav = document.querySelector('a[href*="/chat"]');
  if ((sellerNav || chatNav) && !loginLink) return true;
  if (loginLink) return false;
  return location.pathname.startsWith('/seller');
})()
"""

LOGIN_FORM_JS_TEMPLATE = """
(() => {{
  const login = {login_json};
  const password = {password_json};

  function setValue(el, value) {{
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }}

  const captcha = document.querySelector(
    '[class*="captcha" i], iframe[src*="captcha" i], [data-captcha]'
  );
  if (captcha) return {{ ok: false, reason: 'captcha' }};

  const userInput =
    document.querySelector('input[name="username"]') ||
    document.querySelector('input[name="login"]') ||
    document.querySelector('input[type="email"]') ||
    document.querySelector('input[autocomplete="username"]') ||
    document.querySelector('form input[type="text"]');
  const passInput =
    document.querySelector('input[name="password"]') ||
    document.querySelector('input[type="password"]');

  if (!userInput || !passInput) {{
    return {{ ok: false, reason: 'inputs_not_found' }};
  }}

  setValue(userInput, login);
  setValue(passInput, password);

  const btn =
    document.querySelector('button[type="submit"]') ||
    document.querySelector('input[type="submit"]') ||
    document.querySelector('form button') ||
    [...document.querySelectorAll('button')].find((b) =>
      /войти|вход|login/i.test(b.textContent || '')
    );

  if (!btn) return {{ ok: false, reason: 'submit_not_found' }};
  btn.click();
  return {{ ok: true }};
}})()
"""


class KworkAuthError(RuntimeError):
    pass


@dataclass
class KworkCredentials:
    login: str
    password: str


def is_logged_in(browser: BrowserClient) -> bool:
    result = browser.evaluate(IS_LOGGED_IN_JS)
    return bool(result)


def login(browser: BrowserClient, credentials: KworkCredentials) -> None:
    browser.navigate(KWORK_LOGIN_URL)
    js = LOGIN_FORM_JS_TEMPLATE.format(
        login_json=json.dumps(credentials.login),
        password_json=json.dumps(credentials.password),
    )
    result = browser.evaluate(js)
    if not isinstance(result, dict) or not result.get("ok"):
        reason = result.get("reason") if isinstance(result, dict) else "unknown"
        if reason == "captcha":
            raise KworkAuthError(
                "Kwork login blocked by CAPTCHA — complete manually in browser"
            )
        raise KworkAuthError(f"Kwork login failed: {reason}")

    browser.navigate("https://kwork.ru/")
    if not is_logged_in(browser):
        raise KworkAuthError("Kwork login submitted but session not detected")


def ensure_logged_in(
    browser: BrowserClient,
    credentials: KworkCredentials | None,
    *,
    force: bool = False,
) -> bool:
    if credentials is None:
        if is_logged_in(browser):
            return True
        raise KworkAuthError(
            "Kwork session not active. Set KWORK_LOGIN and KWORK_PASSWORD in .env"
        )

    if not force and is_logged_in(browser):
        logger.debug("Kwork: already logged in")
        return True

    logger.info("Kwork: logging in as %s", credentials.login)
    login(browser, credentials)
    logger.info("Kwork: login OK")
    return True
