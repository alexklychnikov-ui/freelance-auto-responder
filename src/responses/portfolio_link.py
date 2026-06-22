from __future__ import annotations

PORTFOLIO_URL = "https://portfolio.hayklyvibelexy.ru/"


def ensure_portfolio_link(text: str, url: str = PORTFOLIO_URL) -> str:
    body = text.strip()
    if not body:
        return body
    base = url.rstrip("/")
    if base in body or url in body:
        return body
    return f"{body}\n\nПортфолио: {url}"
