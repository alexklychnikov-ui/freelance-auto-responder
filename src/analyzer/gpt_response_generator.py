"""Public response generator — delegates to multi-agent ResponsePipeline."""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.analyzer.project_brief import build_project_brief
from src.analyzer.response_pipeline import (
    ResponsePipeline,
    _BANNED_OPENERS,
    _BANNED_PHRASES,
    soft_banned_issues,
)
from src.analyzer.response_qa import ResponseQaValidator
from src.config import Settings
from src.models import ProjectFull

logger = logging.getLogger(__name__)

# Re-export for older tests / callers — single source in response_pipeline


def _soft_banned_check(text: str) -> list[str]:
    return soft_banned_issues(text)


_PARSE_HALLUCINATION_RE = re.compile(r"парс\w*|скрап\w*", re.I)
_BOT_BRIEF_RE = re.compile(r"telegram[- ]?бот|aiogram|телеграм[- ]?бот", re.I)


def _topic_mismatch_issues(project: ProjectFull, text: str) -> list[str]:
    brief = build_project_brief(project)
    if not brief or re.search(r"парс\w*|скрап\w*", brief, re.I):
        return []
    if _BOT_BRIEF_RE.search(brief) and _PARSE_HALLUCINATION_RE.search(text):
        return ["topic:парсинг_не_в_тз"]
    return []


class GptResponseGenerator:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        qa_validator: ResponseQaValidator | None = None,
    ) -> None:
        self.settings = settings
        self._client = http_client
        self._pipeline = ResponsePipeline(settings, http_client=http_client)
        # Kept for backwards compatibility with callers that inject/close QA.
        self._qa = qa_validator or ResponseQaValidator(
            settings, http_client=http_client
        )
        self._owns_qa = qa_validator is None

    def close(self) -> None:
        self._pipeline.close()
        if self._owns_qa:
            self._qa.close()

    def _call_api(self, body: dict[str, Any], project_id: str) -> str:
        """Compatibility shim used by legacy tests."""
        return self._pipeline._post_chat(body, project_id)

    def generate(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        recent_responses: dict[str, Any] | None = None,
        platform_label: str | None = None,
        progress: Callable[[str], None] | None = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
        budget_mismatch: dict[str, Any] | None = None,
    ) -> str:
        _ = platform_label  # reserved for multi-platform prompts
        return self._pipeline.generate(
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses or {"count": 0},
            progress=progress,
            price_hint=price_hint,
            days_hint=days_hint,
            budget_mismatch=budget_mismatch,
        )

    async def generate_with_progress(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        notify: Callable[[str], Awaitable[None]],
        examples: str = "",
        recent_responses: dict[str, Any] | None = None,
        platform_label: str | None = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
        budget_mismatch: dict[str, Any] | None = None,
    ) -> str:
        _ = platform_label
        return await self._pipeline.generate_with_progress(
            project,
            lightrag_context,
            notify=notify,
            examples=examples,
            recent_responses=recent_responses or {"count": 0},
            price_hint=price_hint,
            days_hint=days_hint,
            budget_mismatch=budget_mismatch,
            threaded=True,
        )
