from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from src.analyzer.context_compress import compress_context_text
from src.analyzer.github_stack import load_github_stack
from src.analyzer.lightrag_http import search_lightrag_http
from src.config import Settings, get_settings
from src.models import ProjectFull

GENERAL_STACK_QUERY = (
    "Технический стек Александра Клычниковова, проекты портфолио: Python, AI, "
    "Telegram-боты, FastAPI, LightRAG, RAG, автоматизация, Docker, Next.js. "
    "Какие типы задач он реально делал по кейсам из портфолио."
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


def build_project_stack_query(project: ProjectFull) -> str:
    parts = [
        project.title or "",
        project.full_description or "",
        " ".join(project.tags or []),
    ]
    tz = " ".join(p.strip() for p in parts if p.strip())
    if len(tz) > 1500:
        tz = tz[:1500] + "…"
    return (
        "Какие проекты и кейсы Александра Клычниковова из портфолио наиболее релевантны "
        f"этому заказу: {tz}"
    )


class LightRagClient:
    def __init__(
        self,
        search_fn: SearchFn | None = None,
        *,
        base_url: str | None = None,
        api_key: str = "",
        github_username: str = "alexklychnikov-ui",
        github_token: str = "",
        github_stack_cache: str = "data/github_stack_cache.json",
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._github_username = github_username
        self._github_token = github_token
        self._github_stack_cache = github_stack_cache

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

    def get_github_stack(self) -> str:
        return load_github_stack(
            username=self._github_username,
            token=self._github_token,
            cache_path=Path(self._github_stack_cache),
        )

    def _maybe_compress(self, text: str) -> str:
        if not self._settings.headroom_compress_context:
            return text
        return compress_context_text(
            text,
            model=self._settings.openai_model,
            proxy_url=self._settings.headroom_proxy_url,
            min_chars=self._settings.headroom_context_min_chars,
        )

    def search_stack_context(self) -> str:
        return self._maybe_compress(self._search_fn(GENERAL_STACK_QUERY, "mix"))

    def search_project_context(self, project: ProjectFull) -> str:
        query = build_project_stack_query(project)
        return self._maybe_compress(self._search_fn(query, "mix"))

    def search_response_rules(self) -> str:
        return self._maybe_compress(self._search_fn(RULES_QUERY, "mix"))

    def get_scoring_context(self, project: ProjectFull) -> str:
        github_stack = self.get_github_stack()
        lightrag = self.search_project_context(project)
        parts = [
            "## Стек и репозитории GitHub (источник правды для score)\n" + github_stack,
        ]
        if lightrag.strip():
            parts.append(
                "## Релевантные кейсы из LightRAG по этому заказу\n" + lightrag.strip()
            )
        return "\n\n".join(parts)

    def get_full_context(self) -> str:
        stack = self.search_stack_context()
        rules = self.search_response_rules()
        parts = [p for p in (stack, rules) if p.strip()]
        return "\n\n---\n\n".join(parts)
