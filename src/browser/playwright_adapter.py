from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# VPS often has broken IPv6 (AAAA exists, no route) → Chromium Happy Eyeballs
# hangs with net::ERR_TIMED_OUT. Prefer IPv4 via host-resolver MAP rules.
_YANDEX_IPV4_HOSTS = (
    "uslugi.yandex.ru",
    "passport.yandex.ru",
    "passport.yandex.com",
    "yandex.ru",
    "www.yandex.ru",
    "yastatic.net",
    "mc.yandex.ru",
    "export.yandex.ru",
    # FL.ru: Chromium Happy Eyeballs / dual-stack hangs on some VPS paths
    "www.fl.ru",
    "fl.ru",
    "cdn.fl.ru",
    "static.fl.ru",
)

_TRANSIENT_NAV_MARKERS = (
    "ERR_TIMED_OUT",
    "Timeout",
    "ERR_CONNECTION",
    "ERR_NETWORK",
    "ERR_HTTP2",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_REFUSED",
    "ERR_SSL",
    "ERR_EMPTY_RESPONSE",
    "ERR_INTERNET_DISCONNECTED",
    "net::",
)


def _is_transient_nav_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(m in msg for m in _TRANSIENT_NAV_MARKERS)


def _resolve_ipv4(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return None
    if not infos:
        return None
    return infos[0][4][0]


def build_ipv4_host_resolver_rules(hosts: tuple[str, ...] = _YANDEX_IPV4_HOSTS) -> str:
    """Chromium --host-resolver-rules that pin dual-stack hosts to A records."""
    parts: list[str] = []
    for host in hosts:
        ip = _resolve_ipv4(host)
        if ip:
            parts.append(f"MAP {host} {ip}")
    return ",".join(parts)


def strip_ddos_guard_cookies(state: dict[str, Any]) -> dict[str, Any]:
    """Drop DDoS-Guard ``__ddg*`` cookies so Chromium can mint fresh ones.

    Stale ``__ddg*`` (often short-lived) poison fl.ru fetches: page.evaluate
    ``fetch`` throws ``TypeError: Failed to fetch`` and httpx hangs with
    auth cookies. Session cookies (id/pwd/PHPSESSID) are kept.
    """
    cookies = state.get("cookies")
    if not isinstance(cookies, list):
        return state
    kept: list[Any] = []
    dropped = 0
    for c in cookies:
        if isinstance(c, dict):
            name = str(c.get("name") or "")
            if name.startswith("__ddg"):
                dropped += 1
                continue
        kept.append(c)
    if dropped:
        logger.info("storage_state_stripped_ddg count=%d", dropped)
        out = dict(state)
        out["cookies"] = kept
        return out
    return state


def load_storage_state(path: Path) -> dict[str, Any] | None:
    """Load Playwright storage_state; repair trailing garbage from torn writes."""
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        try:
            data, end = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            logger.error(
                "storage_state_unreadable path=%s err=%s", path, exc
            )
            return None
        trailer = raw[end:].strip()
        logger.warning(
            "storage_state_repaired path=%s junk_chars=%d",
            path,
            len(raw) - end,
        )
        if trailer:
            _atomic_write_json(path, data)
    if not isinstance(data, dict):
        logger.error("storage_state_not_object path=%s", path)
        return None
    return strip_ddos_guard_cookies(data)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


class PlaywrightBrowserAdapter:
    """Headless Chromium for VPS/Linux (no BrowserMCP extension)."""

    def __init__(
        self,
        *,
        headless: bool = True,
        storage_state_path: str | None = None,
        proxy_url: str | None = None,
        navigate_timeout_ms: int = 60000,
        navigate_retries: int = 3,
    ) -> None:
        self._headless = headless
        self._storage_state_path = storage_state_path
        self._proxy_url = (proxy_url or "").strip() or None
        self._navigate_timeout_ms = navigate_timeout_ms
        self._navigate_retries = max(1, int(navigate_retries))
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def _launch_args(self) -> list[str]:
        args = ["--disable-ipv6"]
        if self._proxy_url:
            return args
        rules = build_ipv4_host_resolver_rules()
        if rules:
            args.append(f"--host-resolver-rules={rules}")
            logger.info("playwright_ipv4_resolver_rules hosts=%d", rules.count("MAP "))
        return args

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": self._headless,
            "args": self._launch_args(),
        }
        if self._proxy_url:
            launch_kwargs["proxy"] = {"server": self._proxy_url}
        self._browser = self._playwright.chromium.launch(
            **launch_kwargs,
        )
        context_kwargs: dict[str, Any] = {}
        if self._storage_state_path:
            state = load_storage_state(Path(self._storage_state_path))
            if state is not None:
                context_kwargs["storage_state"] = state
        self._context = self._browser.new_context(**context_kwargs)
        self._page = self._context.new_page()
        return self._page

    def _reset_page(self) -> None:
        """Drop page/context/browser. Does NOT save storage (avoid torn writes)."""
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def navigate(self, url: str) -> None:
        last_exc: BaseException | None = None
        attempts = self._navigate_retries
        for attempt in range(1, attempts + 1):
            try:
                page = self._ensure_page()
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._navigate_timeout_ms,
                )
                page.wait_for_timeout(1500)
                return
            except Exception as exc:
                last_exc = exc
                host = urlparse(url).hostname or "?"
                transient = _is_transient_nav_error(exc)
                logger.warning(
                    "playwright_navigate_failed attempt=%d/%d host=%s transient=%s err=%s",
                    attempt,
                    attempts,
                    host,
                    transient,
                    str(exc)[:200],
                )
                self._reset_page()
                if attempt < attempts and transient:
                    time.sleep(2.0 * attempt)
                    continue
                raise
        if last_exc is not None:
            raise last_exc

    def snapshot(self) -> str:
        return self._ensure_page().content()

    def click(self, ref_or_selector: str) -> None:
        page = self._ensure_page()
        if ref_or_selector.startswith("ref-") or ref_or_selector.startswith("@"):
            raise NotImplementedError("Playwright adapter uses CSS selectors only")
        page.click(ref_or_selector, timeout=15000)

    def fill(self, ref_or_selector: str, text: str) -> None:
        page = self._ensure_page()
        page.fill(ref_or_selector, text, timeout=15000)

    def fill_sequential(self, selector: str, text: str) -> None:
        page = self._ensure_page()
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=15000)
        loc.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        loc.press_sequentially(str(text), delay=40)
        page.keyboard.press("Tab")

    def evaluate(self, js: str) -> Any:
        return self._ensure_page().evaluate(js)

    def request_get(self, url: str, *, timeout_ms: int = 25000) -> str:
        self._ensure_page()
        response = self._context.request.get(
            url,
            timeout=timeout_ms,
            fail_on_status_code=False,
        )
        if not response.ok:
            raise RuntimeError(
                f"playwright_request_get_http_error status={response.status} "
                f"url={response.url}"
            )
        text = response.text()
        if not text.strip():
            raise RuntimeError(
                f"playwright_request_get_empty status={response.status} "
                f"url={response.url}"
            )
        return text

    def screenshot(self) -> bytes:
        return self._ensure_page().screenshot(full_page=True)

    def wait_ms(self, ms: int) -> None:
        self._ensure_page().wait_for_timeout(ms)

    def save_storage_state(self) -> None:
        if self._context is None or not self._storage_state_path:
            return
        path = Path(self._storage_state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Playwright writes via its own path API; dump via API then rewrite atomically
        # if needed. Prefer dict dump for atomic replace.
        state = self._context.storage_state()
        if isinstance(state, dict):
            _atomic_write_json(path, state)
        else:
            self._context.storage_state(path=str(path))

    def close(self) -> None:
        if self._context is not None and self._storage_state_path:
            try:
                self.save_storage_state()
            except Exception:
                pass
        self._reset_page()
