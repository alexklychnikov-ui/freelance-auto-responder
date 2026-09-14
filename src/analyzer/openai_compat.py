"""Shared OpenAI chat POST with a guard for models that reject `temperature`."""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_NO_TEMPERATURE_MODELS: set[str] = set()


def _without_temperature(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != "temperature"}


def _is_temperature_error(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    try:
        error = response.json().get("error") or {}
    except Exception:
        return False
    if not isinstance(error, dict):
        return False
    if error.get("param") == "temperature":
        return True
    message = str(error.get("message") or "").lower()
    return "temperature" in message and "support" in message


def post_chat(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
) -> httpx.Response:
    model = str(body.get("model") or "")
    if "temperature" in body and model in _NO_TEMPERATURE_MODELS:
        body = _without_temperature(body)
    response = client.post(url, headers=headers, json=body)
    if "temperature" not in body or not _is_temperature_error(response):
        return response
    _NO_TEMPERATURE_MODELS.add(model)
    logger.warning(
        "openai_model_rejects_temperature model=%s, retrying without temperature",
        model,
    )
    return client.post(url, headers=headers, json=_without_temperature(body))
