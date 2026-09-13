from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from src.evidence.collector import EvidenceService
from src.evidence.discovery import ResourceCandidate
from src.evidence.fetcher import MAX_FETCH_BYTES, SourceContent, fetch_url
from src.evidence.normalize import html_to_text, normalize_content
from src.evidence.ssrf import validate_url


def test_block_loopback_ip() -> None:
    r = validate_url("http://127.0.0.1/")
    assert r.ok is False
    assert r.error_code == "blocked_ip"
    out = fetch_url("http://127.0.0.1/")
    assert out.error_code == "blocked_ip"
    assert out.text is None


def test_block_ipv6_loopback_ula_mapped() -> None:
    assert validate_url("http://[::1]/").error_code == "blocked_ip"
    assert validate_url("http://[::ffff:127.0.0.1]/").error_code == "blocked_ip"
    assert validate_url("http://[::ffff:7f00:1]/").error_code == "blocked_ip"
    assert validate_url("http://[fc00::1]/").error_code == "blocked_ip"
    assert validate_url("http://[fd12:3456:789a::1]/").error_code == "blocked_ip"
    assert validate_url("http://[fe80::1]/").error_code == "blocked_ip"
    assert fetch_url("http://[::1]/").error_code == "blocked_ip"


def test_block_cgnat_and_ipv4_tricks() -> None:
    assert validate_url("http://100.64.0.1/").error_code == "blocked_ip"
    assert validate_url("http://2130706433/").error_code == "blocked_ip"
    assert validate_url("http://0x7f000001/").error_code == "blocked_ip"
    assert validate_url("http://127.1/").error_code == "blocked_ip"
    assert validate_url("http://0177.0.0.1/").error_code == "blocked_ip"
    assert validate_url("http://0/").error_code == "blocked_ip"


def test_block_metadata_ip() -> None:
    r = validate_url("http://169.254.169.254/latest/meta-data/")
    assert r.ok is False
    assert r.error_code == "blocked_ip"
    out = fetch_url("http://169.254.169.254/latest/meta-data/")
    assert out.error_code == "blocked_ip"


def test_block_file_scheme() -> None:
    r = validate_url("file:///etc/passwd")
    assert r.ok is False
    assert r.error_code == "blocked_scheme"
    out = fetch_url("file:///etc/passwd")
    assert out.error_code == "blocked_scheme"


def test_block_data_and_javascript_schemes() -> None:
    assert validate_url("data:text/html,hi").error_code == "blocked_scheme"
    assert validate_url("javascript:alert(1)").error_code == "blocked_scheme"


def test_block_userinfo_and_nonstandard_port() -> None:
    assert validate_url("https://user:pass@example.com/").error_code == "blocked_userinfo"
    assert validate_url("https://example.com:8443/").error_code == "blocked_port"
    assert validate_url("https://example.com:8080/").error_code == "blocked_port"
    assert validate_url("http://example.com:8080/").error_code == "blocked_port"


def test_block_private_rfc1918() -> None:
    assert validate_url("http://10.0.0.1/").error_code == "blocked_ip"
    assert validate_url("http://192.168.1.1/").error_code == "blocked_ip"
    assert validate_url("http://172.16.5.5/").error_code == "blocked_ip"


def _public_addrinfo(host, port, *args, **kwargs):
    port_n = port or 0
    return [
        (2, 1, 6, "", ("93.184.216.34", port_n)),
    ]


class _FakeStreamResp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict | None = None,
        url: str = "https://example.com/",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self.url = url
        self._chunks = chunks or []

    def iter_bytes(self):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_happy_path_html_normalize() -> None:
    html = (
        "<html><head><title>Demo Title</title>"
        "<script>evil()</script><style>.x{}</style></head>"
        "<body><p>Hello   world</p></body></html>"
    )
    fake = _FakeStreamResp(
        url="https://example.com/page",
        chunks=[html.encode("utf-8")],
    )
    client = MagicMock(spec=httpx.Client)
    client.stream.return_value = fake

    with patch("src.evidence.ssrf.socket.getaddrinfo", side_effect=_public_addrinfo):
        out = fetch_url("https://example.com/page", client=client)

    assert out.error_code is None
    assert out.method == "http"
    assert out.status_code == 200
    assert out.text is not None
    assert "Demo Title" in out.text
    assert "Hello world" in out.text
    assert "evil" not in out.text
    assert out.title == "Demo Title"
    client.stream.assert_called_once()


def test_oversized_response_stopped() -> None:
    big = b"x" * (MAX_FETCH_BYTES + 1000)
    fake = _FakeStreamResp(
        url="https://example.com/big",
        headers={"content-type": "text/plain"},
        chunks=[big],
    )
    client = MagicMock(spec=httpx.Client)
    client.stream.return_value = fake

    with patch("src.evidence.ssrf.socket.getaddrinfo", side_effect=_public_addrinfo):
        out = fetch_url("https://example.com/big", client=client)

    assert out.error_code == "too_large"
    assert out.text is None


def test_redirect_to_private_ip_blocked() -> None:
    redirect = _FakeStreamResp(
        status_code=302,
        headers={"location": "http://127.0.0.1/secret", "content-type": "text/html"},
        url="https://example.com/go",
        chunks=[],
    )
    client = MagicMock(spec=httpx.Client)
    client.stream.return_value = redirect

    with patch("src.evidence.ssrf.socket.getaddrinfo", side_effect=_public_addrinfo):
        out = fetch_url("https://example.com/go", client=client)

    assert out.error_code == "blocked_ip"
    assert out.text is None


def test_normalize_plain_passthrough() -> None:
    doc = normalize_content("  alpha   beta  \n\n\ngamma  ", "text/plain")
    assert doc.title is None
    assert "alpha beta" in doc.text
    assert "gamma" in doc.text


def test_html_to_text_fixture_smoke() -> None:
    from pathlib import Path

    html = Path("tests/fixtures/evidence/3252339_torgi_gov_public.html").read_text(
        encoding="utf-8"
    )
    doc = html_to_text(html)
    assert doc.title is not None
    assert "torgi.gov.ru" in doc.title
    assert "25000001234567890001" in doc.text
    assert "Московская область" in doc.text


def test_evidence_service_fetch_candidate_verified() -> None:
    html = b"<html><head><title>T</title></head><body>body ok</body></html>"
    fake = _FakeStreamResp(url="https://example.com/", chunks=[html])
    client = MagicMock(spec=httpx.Client)
    client.stream.return_value = fake

    cand = ResourceCandidate(
        kind="url",
        role="reference",
        input_ref="https://example.com/",
        title_hint="ex",
    )
    svc = EvidenceService()
    with patch("src.evidence.ssrf.socket.getaddrinfo", side_effect=_public_addrinfo):
        src = svc.fetch_candidate(cand, client=client)

    assert src.status == "verified"
    assert src.fetch_method == "http"
    assert src.error_code is None
    assert src.content_hash
    assert src.http_status == 200
    assert src.title == "T"


def test_evidence_service_fetch_rejects_ssrf() -> None:
    svc = EvidenceService()
    cand = ResourceCandidate(
        kind="url",
        role="unknown",
        input_ref="http://127.0.0.1/",
    )
    src = svc.fetch_candidate(cand)
    assert src.status == "rejected"
    assert src.error_code == "blocked_ip"


def test_evidence_service_attachment_rejected() -> None:
    svc = EvidenceService()
    cand = ResourceCandidate(
        kind="attachment",
        role="documentation",
        input_ref="tz.pdf",
    )
    src = svc.fetch_candidate(cand)
    assert src.status == "rejected"
    assert src.error_code == "not_http_url"


def test_source_content_dataclass() -> None:
    sc = SourceContent(
        final_url="https://x.test",
        status_code=200,
        content_type="text/html",
        text="hi",
        error_code=None,
    )
    assert sc.method == "http"


def test_browser_path_no_storage_state() -> None:
    """Playwright evidence path must not load platform cookies/storage_state."""
    from src.evidence.fetcher import fetch_with_browser

    fake_page = MagicMock()
    fake_page.url = "https://example.com/"
    fake_page.content.return_value = (
        "<html><head><title>B</title></head><body>browser ok</body></html>"
    )

    fake_browser = MagicMock()
    fake_browser._storage_state_path = None
    fake_browser._ensure_page.return_value = fake_page
    fake_browser.snapshot.return_value = fake_page.content.return_value

    with (
        patch("src.evidence.ssrf.socket.getaddrinfo", side_effect=_public_addrinfo),
        patch(
            "src.evidence.fetcher.PlaywrightBrowserAdapter",
            return_value=fake_browser,
        ) as adapter_cls,
    ):
        out = fetch_with_browser("https://example.com/")

    adapter_cls.assert_called_once_with(storage_state_path=None, headless=True)
    assert fake_browser._storage_state_path is None
    fake_page.route.assert_called_once()
    assert out.error_code is None
    assert out.method == "browser"
    assert out.text is not None
    assert "browser ok" in out.text


def test_browser_route_guard_blocks_private_hop() -> None:
    from src.evidence.fetcher import fetch_with_browser

    fake_page = MagicMock()
    fake_page.url = "https://example.com/"

    def _route(pattern, handler):
        req = MagicMock()
        req.url = "http://127.0.0.1/secret"
        route = MagicMock()
        route.request = req
        handler(route)
        route.abort.assert_called_once()

    fake_page.route.side_effect = _route

    fake_browser = MagicMock()
    fake_browser._storage_state_path = None
    fake_browser._ensure_page.return_value = fake_page

    with (
        patch("src.evidence.ssrf.socket.getaddrinfo", side_effect=_public_addrinfo),
        patch(
            "src.evidence.fetcher.PlaywrightBrowserAdapter",
            return_value=fake_browser,
        ),
    ):
        out = fetch_with_browser("https://example.com/")

    assert out.method == "browser"
    assert out.error_code == "blocked_ip"
    assert out.text is None
