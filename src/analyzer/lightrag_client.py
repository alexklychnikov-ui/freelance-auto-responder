from __future__ import annotations

import logging

from collections.abc import Callable

from src.analyzer.lightrag_http import search_lightrag_http

STACK_QUERY = (
    "Технический стек Александра Клычниковова, проекты портфолио, опыт Python AI "
    "Telegram парсинг FastAPI Docker. Что из этого применимо к фриланс-задачам разработки?"
)

RULES_QUERY = (
    "Правила отклика на фриланс-проект: структура сопроводительного письма, ошибки, "
    "формула первого абзаца, что писать для python-разработчика. "
    "Примеры хорошего и плохого отклика."
)

logger = logging.getLogger(__name__)


SearchFn = Callable[[str, str], str]


def _noop_search(query: str, mode: str) -> str:
    return ""


class LightRagClient:
    def __init__(
        self,
        search_fn: SearchFn | None = None,
        *,
        base_url: str | None = None,
        api_key: str = "",
    ) -> None:
        if search_fn is not None:
            self._search_fn = search_fn
        elif base_url:
            base = base_url.rstrip("/")
            key = api_key

            def _http_search(query: str, mode: str) -> str:
                try:
                    return search_lightrag_http(base, query, mode, api_key=key)
                except Exception as exc:
                    logger.warning("LightRAG HTTP search failed: %s", exc)
                    return ""

            self._search_fn = _http_search
        else:
            self._search_fn = _noop_search

    def search_stack_context(self) -> str:
        return self._search_fn(STACK_QUERY, "mix")

    def search_response_rules(self) -> str:
        return self._search_fn(RULES_QUERY, "mix")

    def get_full_context(self) -> str:
        stack = self.search_stack_context()
        rules = self.search_response_rules()
        parts = [p for p in (stack, rules) if p.strip()]
        return "\n\n---\n\n".join(parts)
