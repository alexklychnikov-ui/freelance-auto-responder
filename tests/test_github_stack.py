from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import httpx

from src.analyzer.github_stack import (
    STATIC_FALLBACK,
    _build_stack_text,
    load_github_stack,
)
from src.analyzer.lightrag_client import LightRagClient, build_project_stack_query
from src.models import ProjectFull


def test_build_stack_text_from_repos() -> None:
    repos = [
        {
            "name": "LightRAG",
            "description": "Graph RAG pipeline",
            "language": "Python",
            "topics": ["rag", "ai"],
            "stargazers_count": 10,
            "fork": False,
            "pushed_at": "2026-01-01",
        },
        {
            "name": "MyPortfolio",
            "description": "Portfolio site",
            "language": "TypeScript",
            "topics": ["nextjs"],
            "stargazers_count": 5,
            "fork": False,
            "pushed_at": "2025-12-01",
        },
    ]
    text = _build_stack_text("alexklychnikov-ui", repos)
    assert "github.com/alexklychnikov-ui" in text
    assert "LightRAG" in text
    assert "Python" in text
    assert "TypeScript" in text


def test_load_github_stack_uses_cache(tmp_path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                "fetched_at": time.time(),
                "stack_text": "cached stack from GitHub",
            }
        ),
        encoding="utf-8",
    )
    result = load_github_stack(cache_path=cache)
    assert result == "cached stack from GitHub"


def test_load_github_stack_fallback_on_fetch_error(tmp_path) -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.HTTPError("fail")
    result = load_github_stack(
        cache_path=tmp_path / "missing.json",
        http_client=client,
    )
    assert result == STATIC_FALLBACK


def test_build_project_stack_query() -> None:
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Telegram-бот",
        full_description="aiogram парсинг",
        tags=["Python"],
    )
    query = build_project_stack_query(project)
    assert "Telegram-бот" in query
    assert "aiogram" in query
    assert "Чего НЕ делал" not in query


def test_scoring_context_github_plus_project_query() -> None:
    calls: list[str] = []

    def search_fn(query: str, mode: str) -> str:
        calls.append(query)
        return f"case:{query[:40]}"

    client = LightRagClient(
        search_fn=search_fn,
        github_stack_cache="data/github_stack_cache.json",
    )
    client.get_github_stack = lambda: "GitHub stack: Python, aiogram"  # type: ignore[method-assign]

    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Бот",
        full_description="Telegram",
    )
    ctx = client.get_scoring_context(project)
    assert "GitHub stack" in ctx
    assert "Релевантные кейсы" in ctx
    assert len(calls) == 1
    assert "Бот" in calls[0]
