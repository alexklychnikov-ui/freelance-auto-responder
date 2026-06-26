from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def compress_context_text(
    text: str,
    *,
    model: str,
    proxy_url: str = "",
    min_chars: int = 2500,
    max_chars_fallback: int = 6000,
    timeout: float = 45.0,
) -> str:
    stripped = (text or "").strip()
    if not stripped or len(stripped) < min_chars:
        return text

    base = (proxy_url or "").strip().rstrip("/")
    if not base:
        return _truncate(stripped, max_chars_fallback)

    payload = {
        "messages": [{"role": "user", "content": stripped}],
        "model": model,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base}/v1/compress", json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("Headroom compress failed, fallback truncate: %s", exc)
        return _truncate(stripped, max_chars_fallback)

    if isinstance(data, dict) and data.get("error"):
        logger.warning("Headroom compress error: %s", data.get("error"))
        return _truncate(stripped, max_chars_fallback)

    messages = data.get("messages") if isinstance(data, dict) else None
    if isinstance(messages, list) and messages:
        content = messages[0].get("content") if isinstance(messages[0], dict) else None
        if isinstance(content, str) and content.strip():
            saved = int(data.get("tokens_saved") or 0)
            if saved > 0:
                logger.info(
                    "Headroom compressed context: %s -> %s tokens (saved %s)",
                    data.get("tokens_before"),
                    data.get("tokens_after"),
                    saved,
                )
            return content

    return _truncate(stripped, max_chars_fallback)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"
