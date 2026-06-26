import httpx

from src.analyzer.context_compress import compress_context_text


def test_compress_context_short_passthrough():
    assert compress_context_text("short", model="gpt-4o-mini") == "short"


def test_compress_context_fallback_truncate(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *_a, **_k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "Client", _Client)
    text = "x" * 8000
    out = compress_context_text(text, model="gpt-4o-mini", proxy_url="http://127.0.0.1:8787", min_chars=100)
    assert len(out) < len(text)
    assert out.endswith("…")
