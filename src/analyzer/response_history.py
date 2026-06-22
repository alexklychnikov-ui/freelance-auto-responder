from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.responses.prepared_store import PreparedResponseStore


def _first_sentence(text: str, max_len: int = 120) -> str:
    chunk = text.strip().split("\n", 1)[0].strip()
    for sep in (". ", "! ", "? ", "… "):
        if sep in chunk:
            chunk = chunk.split(sep, 1)[0] + sep.strip()
            break
    return chunk[:max_len].strip()


def _last_sentence(text: str, max_len: int = 120) -> str:
    body = text.strip()
    for sep in (". ", "! ", "? "):
        parts = body.rsplit(sep, 1)
        if len(parts) == 2 and len(parts[1]) > 10:
            return (parts[1])[:max_len].strip()
    return body[-max_len:].strip()


def load_recent_response_context(
    store: PreparedResponseStore,
    *,
    limit: int = 15,
    preview_chars: int = 280,
) -> dict[str, list[str] | int]:
    items = sorted(
        store.list_all(),
        key=lambda i: i.prepared_at,
        reverse=True,
    )[:limit]

    texts = [i.response_text.strip() for i in items if i.response_text and i.response_text.strip()]
    return {
        "count": len(texts),
        "recent_openings": [_first_sentence(t) for t in texts],
        "recent_closings": [_last_sentence(t) for t in texts],
        "recent_previews": [t[:preview_chars] for t in texts],
        "recent_titles": [i.title for i in items[:limit]],
    }
