from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})

BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)

# Decimal / hex dword, or dotted forms with octal/hex/short segments (browser-style).
_IPV4_LIKE_RE = re.compile(
    r"^(?:"
    r"0x[0-9a-f]+"
    r"|\d+"
    r"|(?:(?:0x[0-9a-f]+|\d+)\.){1,3}(?:0x[0-9a-f]+|\d+)"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SSRFCheck:
    ok: bool
    error_code: str | None = None
    url: str | None = None
    hostname: str | None = None
    port: int | None = None
    ips: tuple[str, ...] = ()


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4-mapped IPv6 (:ffff:x.x.x.x) — re-check embedded v4
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    # not is_global covers RFC1918, loopback, link-local, CGNAT 100.64/10, etc.
    if not ip.is_global:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    return False


def is_blocked_ip_literal(value: str) -> bool:
    ip_obj = _host_as_ip(value.strip())
    if ip_obj is None:
        return False
    return _is_blocked_ip(ip_obj)


def _parse_ipv4_part(part: str) -> int | None:
    if not part:
        return None
    try:
        if part.lower().startswith("0x"):
            return int(part, 16)
        # Leading zero → octal (browser/curl style), except bare "0"
        if part.startswith("0") and len(part) > 1 and part.isdigit():
            return int(part, 8)
        if part.isdigit():
            return int(part, 10)
    except ValueError:
        return None
    return None


def _coerce_ipv4_like(host: str) -> ipaddress.IPv4Address | None:
    """Parse decimal/hex/octal/short IPv4 forms that ipaddress rejects."""
    if not _IPV4_LIKE_RE.fullmatch(host):
        return None
    if "." not in host:
        try:
            n = int(host, 16) if host.lower().startswith("0x") else int(host, 10)
        except ValueError:
            return None
        if n < 0 or n > 0xFFFFFFFF:
            return None
        return ipaddress.IPv4Address(n)

    parts = host.split(".")
    if not (1 <= len(parts) <= 4):
        return None
    nums: list[int] = []
    for p in parts:
        v = _parse_ipv4_part(p)
        if v is None or v < 0:
            return None
        nums.append(v)

    try:
        if len(nums) == 4:
            a, b, c, d = nums
            if any(x > 255 for x in (a, b, c, d)):
                return None
            packed = (a << 24) | (b << 16) | (c << 8) | d
        elif len(nums) == 3:
            a, b, c = nums
            if a > 255 or b > 255 or c > 0xFFFF:
                return None
            packed = (a << 24) | (b << 16) | c
        elif len(nums) == 2:
            a, b = nums
            if a > 255 or b > 0xFFFFFF:
                return None
            packed = (a << 24) | b
        else:
            if nums[0] > 0xFFFFFFFF:
                return None
            packed = nums[0]
        return ipaddress.IPv4Address(packed)
    except (ValueError, OverflowError):
        return None


def _host_as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _IPV4_LIKE_RE.fullmatch(host):
        coerced = _coerce_ipv4_like(host)
        if coerced is not None:
            return coerced
        # Looks like an IP trick but unparseable — treat as blocked sentinel
        return ipaddress.IPv4Address("0.0.0.0")
    return None


def _normalize_hostname(host: str) -> str:
    h = host.strip().rstrip(".").lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h


def validate_url(url: str, *, resolve: bool = True) -> SSRFCheck:
    """Validate URL against SSRF rules; optionally resolve and check all A/AAAA."""
    if not url or not str(url).strip():
        return SSRFCheck(ok=False, error_code="blocked_empty")

    raw = str(url).strip()
    try:
        parsed = urlparse(raw)
    except Exception:
        return SSRFCheck(ok=False, error_code="blocked_parse")

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return SSRFCheck(ok=False, error_code="blocked_scheme")

    if parsed.username is not None or parsed.password is not None:
        return SSRFCheck(ok=False, error_code="blocked_userinfo")

    hostname = parsed.hostname
    if not hostname:
        return SSRFCheck(ok=False, error_code="blocked_hostname")

    host = _normalize_hostname(hostname)
    if host in BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return SSRFCheck(ok=False, error_code="blocked_hostname")

    port = parsed.port
    if port is None:
        port = _default_port(scheme)
    elif port not in ALLOWED_PORTS:
        return SSRFCheck(ok=False, error_code="blocked_port")
    if port not in ALLOWED_PORTS:
        return SSRFCheck(ok=False, error_code="blocked_port")

    # IP literal (incl. decimal/octal/hex tricks) — no DNS, check directly
    ip_obj = _host_as_ip(host)
    if ip_obj is not None:
        if _is_blocked_ip(ip_obj):
            return SSRFCheck(ok=False, error_code="blocked_ip")
        normalized = _rebuild_url(parsed, host=str(ip_obj), port=port, scheme=scheme)
        return SSRFCheck(
            ok=True,
            url=normalized,
            hostname=str(ip_obj),
            port=port,
            ips=(str(ip_obj),),
        )

    if not resolve:
        normalized = _rebuild_url(parsed, host=host, port=port, scheme=scheme)
        return SSRFCheck(ok=True, url=normalized, hostname=host, port=port, ips=())

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return SSRFCheck(ok=False, error_code="dns_failure")
    except OSError:
        return SSRFCheck(ok=False, error_code="dns_failure")

    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            resolved = ipaddress.ip_address(ip_str)
        except ValueError:
            return SSRFCheck(ok=False, error_code="blocked_ip")
        if _is_blocked_ip(resolved):
            return SSRFCheck(ok=False, error_code="blocked_ip")
        ips.append(str(resolved))

    if not ips:
        return SSRFCheck(ok=False, error_code="dns_failure")

    normalized = _rebuild_url(parsed, host=host, port=port, scheme=scheme)
    return SSRFCheck(
        ok=True,
        url=normalized,
        hostname=host,
        port=port,
        ips=tuple(ips),
    )


def assert_host_safe_to_connect(host: str) -> str:
    """Resolve/validate host for connect; return pinned safe IP (first A/AAAA)."""
    host_n = _normalize_hostname(host)
    if host_n in BLOCKED_HOSTNAMES or host_n.endswith(".localhost"):
        raise PermissionError("blocked_hostname")

    ip_obj = _host_as_ip(host_n)
    if ip_obj is not None:
        if _is_blocked_ip(ip_obj):
            raise PermissionError("blocked_ip")
        return str(ip_obj)

    try:
        infos = socket.getaddrinfo(host_n, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PermissionError("dns_failure") from exc

    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        ip_str = info[4][0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            resolved = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise PermissionError("blocked_ip") from exc
        if _is_blocked_ip(resolved):
            raise PermissionError("blocked_ip")
        ips.append(str(resolved))

    if not ips:
        raise PermissionError("dns_failure")
    return ips[0]


def resolve_redirect_url(base_url: str, location: str) -> str:
    """Resolve absolute/relative redirect Location against base."""
    return urljoin(base_url, location)


def _format_netloc_host(host: str) -> str:
    # IPv6 literals always need brackets in URLs
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _rebuild_url(parsed, *, host: str, port: int, scheme: str) -> str:
    host_fmt = _format_netloc_host(host)
    # Omit default ports from netloc for cleaner URLs
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = host_fmt
    else:
        netloc = f"{host_fmt}:{port}"
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
