"""Постобработка текста отклика перед вставкой в форму."""
from __future__ import annotations

import re


def strip_response_markdown(text: str) -> str:
    """Убрать markdown-выделение (**bold**, __bold__) из GPT-ответа."""
    cleaned = text
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")
    return cleaned.strip()
