from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def search_lightrag_http(
    base_url: str,
    query: str,
    mode: str = "mix",
    *,
    api_key: str = "",
    timeout: float = 90.0,
) -> str:
    url = base_url.rstrip("/") + "/query"
    payload = {
        "query": query,
        "mode": mode,
        "only_need_context": True,
    }
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("response", "context", "result", "data"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return str(data)
    return str(data)
