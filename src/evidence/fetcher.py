from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpcore
import httpx

from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.evidence.normalize import normalize_content
from src.evidence.ssrf import (
    assert_host_safe_to_connect,
    resolve_redirect_url,
    validate_url,
)

MAX_FETCH_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5

_TEXT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "application/javascript",
    "application/xhtml+xml",
)


@dataclass
class SourceContent:
    final_url: str | None
    status_code: int | None
    content_type: str | None
    text: str | None
    error_code: str | None
    method: Literal["http", "browser"] = "http"
    title: str | None = None
    raw_text: str | None = None


class _SSRFBackend(httpcore.SyncBackend):
    """Connect only after host/IP SSRF check; pin first safe resolved address."""

    def connect_tcp(  # type: ignore[override]
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        pinned = assert_host_safe_to_connect(host)
        return super().connect_tcp(
            pinned,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


def _ssrf_transport() -> httpx.HTTPTransport:
    transport = httpx.HTTPTransport()
    transport._pool._network_backend = _SSRFBackend()  # noqa: SLF001
    return transport


def _is_text_content_type(content_type: str | None) -> bool:
    if not content_type:
        return True
    ct = content_type.split(";")[0].strip().lower()
    return any(ct.startswith(p) or p in ct for p in _TEXT_TYPES) or ct in {
        "application/json",
        "application/xml",
        "text/xml",
        "application/xhtml+xml",
    }


def _decode_body(data: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    if content_type:
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip("\"'") or "utf-8"
                break
    try:
        return data.decode(charset)
    except LookupError:
        return data.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def fetch_url(
    url: str,
    *,
    timeout: float = 60.0,
    max_bytes: int = MAX_FETCH_BYTES,
    client: httpx.Client | None = None,
    keep_raw: bool = False,
) -> SourceContent:
    """Streaming GET with SSRF guards, redirect re-check, size/time limits."""
    current = url
    owns_client = client is None
    http_client = client
    try:
        if http_client is None:
            http_client = httpx.Client(
                transport=_ssrf_transport(),
                timeout=timeout,
                follow_redirects=False,
                headers={
                    "User-Agent": "FreelanceAutoResponder-Evidence/1.0",
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
                },
            )

        for _ in range(MAX_REDIRECTS + 1):
            check = validate_url(current, resolve=True)
            if not check.ok:
                return SourceContent(
                    final_url=current,
                    status_code=None,
                    content_type=None,
                    text=None,
                    error_code=check.error_code or "blocked_ssrf",
                    method="http",
                )

            assert check.url is not None
            current = check.url

            try:
                assert http_client is not None
                with http_client.stream("GET", current) as resp:
                    if resp.status_code in {301, 302, 303, 307, 308}:
                        location = resp.headers.get("location")
                        if not location:
                            return SourceContent(
                                final_url=current,
                                status_code=resp.status_code,
                                content_type=resp.headers.get("content-type"),
                                text=None,
                                error_code="redirect_missing_location",
                                method="http",
                            )
                        current = resolve_redirect_url(current, location)
                        continue

                    content_type = resp.headers.get("content-type")
                    if not _is_text_content_type(content_type):
                        return SourceContent(
                            final_url=str(resp.url),
                            status_code=resp.status_code,
                            content_type=content_type,
                            text=None,
                            error_code="unsupported_content_type",
                            method="http",
                        )

                    buf = bytearray()
                    oversized = False
                    for chunk in resp.iter_bytes():
                        if not chunk:
                            continue
                        if len(buf) + len(chunk) > max_bytes:
                            # take remainder up to cap then stop
                            remain = max_bytes - len(buf)
                            if remain > 0:
                                buf.extend(chunk[:remain])
                            oversized = True
                            break
                        buf.extend(chunk)

                    if oversized:
                        return SourceContent(
                            final_url=str(resp.url),
                            status_code=resp.status_code,
                            content_type=content_type,
                            text=None,
                            error_code="too_large",
                            method="http",
                        )

                    if resp.status_code >= 400:
                        return SourceContent(
                            final_url=str(resp.url),
                            status_code=resp.status_code,
                            content_type=content_type,
                            text=None,
                            error_code="http_error",
                            method="http",
                        )

                    raw = _decode_body(bytes(buf), content_type)
                    doc = normalize_content(raw, content_type)
                    if not doc.text:
                        return SourceContent(
                            final_url=str(resp.url),
                            status_code=resp.status_code,
                            content_type=content_type,
                            text=None,
                            error_code="empty_content",
                            method="http",
                            title=doc.title,
                        )
                    return SourceContent(
                        final_url=str(resp.url),
                        status_code=resp.status_code,
                        content_type=content_type,
                        text=doc.text,
                        error_code=None,
                        method="http",
                        title=doc.title,
                        raw_text=raw if keep_raw else None,
                    )
            except PermissionError as exc:
                code = str(exc) if str(exc) else "blocked_ip"
                return SourceContent(
                    final_url=current,
                    status_code=None,
                    content_type=None,
                    text=None,
                    error_code=code,
                    method="http",
                )
            except httpx.TimeoutException:
                return SourceContent(
                    final_url=current,
                    status_code=None,
                    content_type=None,
                    text=None,
                    error_code="timeout",
                    method="http",
                )
            except httpx.HTTPError:
                return SourceContent(
                    final_url=current,
                    status_code=None,
                    content_type=None,
                    text=None,
                    error_code="connect_error",
                    method="http",
                )

        return SourceContent(
            final_url=current,
            status_code=None,
            content_type=None,
            text=None,
            error_code="too_many_redirects",
            method="http",
        )
    finally:
        if owns_client and http_client is not None:
            http_client.close()


def fetch_with_browser(
    url: str,
    *,
    timeout: float = 60.0,
) -> SourceContent:
    """Optional clean Playwright path (no storage_state / platform cookies)."""
    check = validate_url(url, resolve=True)
    if not check.ok:
        return SourceContent(
            final_url=url,
            status_code=None,
            content_type=None,
            text=None,
            error_code=check.error_code or "blocked_ssrf",
            method="browser",
        )

    browser = PlaywrightBrowserAdapter(storage_state_path=None, headless=True)
    blocked_codes: list[str] = []
    try:
        assert check.url is not None
        page = browser._ensure_page()  # noqa: SLF001
        assert browser._storage_state_path is None  # noqa: SLF001

        def _ssrf_route_guard(route) -> None:
            req_url = route.request.url
            if req_url.startswith(("about:", "blob:", "data:")):
                route.continue_()
                return
            hop = validate_url(req_url, resolve=True)
            if not hop.ok:
                blocked_codes.append(hop.error_code or "blocked_ssrf")
                route.abort()
                return
            route.continue_()

        page.route("**/*", _ssrf_route_guard)
        timeout_ms = max(1000, int(timeout * 1000))
        page.goto(check.url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(min(1500, timeout_ms))

        if blocked_codes:
            return SourceContent(
                final_url=str(page.url) if page.url else check.url,
                status_code=None,
                content_type=None,
                text=None,
                error_code=blocked_codes[0],
                method="browser",
            )

        final_url = str(page.url) if page is not None else check.url
        final_check = validate_url(final_url, resolve=True)
        if not final_check.ok:
            return SourceContent(
                final_url=final_url,
                status_code=None,
                content_type=None,
                text=None,
                error_code=final_check.error_code or "blocked_ssrf",
                method="browser",
            )
        html = browser.snapshot()
        doc = normalize_content(html, "text/html")
        if not doc.text:
            return SourceContent(
                final_url=final_url,
                status_code=200,
                content_type="text/html",
                text=None,
                error_code="empty_content",
                method="browser",
                title=doc.title,
            )
        return SourceContent(
            final_url=final_url,
            status_code=200,
            content_type="text/html",
            text=doc.text,
            error_code=None,
            method="browser",
            title=doc.title,
        )
    except Exception:
        if blocked_codes:
            return SourceContent(
                final_url=check.url,
                status_code=None,
                content_type=None,
                text=None,
                error_code=blocked_codes[0],
                method="browser",
            )
        return SourceContent(
            final_url=check.url,
            status_code=None,
            content_type=None,
            text=None,
            error_code="browser_error",
            method="browser",
        )
    finally:
        try:
            browser.close()
        except Exception:
            pass
