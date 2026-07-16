from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from src.analyzer.landing_case import MY_PORTFOLIO_GITHUB_LINE

from src.analyzer.landing_case import MY_PORTFOLIO_GITHUB_LINE

logger = logging.getLogger(__name__)

DEFAULT_GITHUB_USER = "alexklychnikov-ui"
GITHUB_API = "https://api.github.com"
CACHE_TTL_SECONDS = 24 * 3600

STATIC_FALLBACK = f"""\
Профиль: https://github.com/alexklychnikov-ui
Фокус: Python, FastAPI, Flask, aiogram, OpenAI, LangChain, LightRAG, RAG, Telegram-боты,
автоматизация, PostgreSQL, pgvector, Docker, Next.js, Prisma, nginx, Dynamics AX (X++).

Ключевые репозитории:
- LightRAG — Graph RAG, ingestion, hybrid retrieval, Telegram, MCP, VPS
{MY_PORTFOLIO_GITHUB_LINE}
- PriceMonitoring — мониторинг цен, Telegram alerts, Grafana, Docker
- TGRecordsOfExpenses — бот учёта расходов, OCR, Excel, AI
- DynamicsAX — AI-ассистент для AX 2012 / X++
- tgDialog-Memory — Telegram-бот с долгосрочной памятью (Pinecone)
- TGBotBothMemory — OpenAI-бот, контекстная и векторная память
"""


def _format_repo(repo: dict[str, Any]) -> str:
    name = repo.get("name") or ""
    if name == "MyPortfolio":
        return MY_PORTFOLIO_GITHUB_LINE
    desc = (repo.get("description") or "").strip()
    lang = repo.get("language") or ""
    topics = ", ".join(repo.get("topics") or [])
    parts = [f"- {name}"]
    if desc:
        parts.append(f": {desc}")
    meta: list[str] = []
    if lang:
        meta.append(lang)
    if topics:
        meta.append(topics)
    if meta:
        parts.append(f" [{'; '.join(meta)}]")
    return "".join(parts)


def _fetch_repos(username: str, token: str = "") -> list[dict[str, Any]]:
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos: list[dict[str, Any]] = []
    page = 1
    with httpx.Client(timeout=30.0) as client:
        while page <= 5:
            response = client.get(
                f"{GITHUB_API}/users/{username}/repos",
                params={
                    "per_page": 100,
                    "page": page,
                    "sort": "pushed",
                    "direction": "desc",
                },
                headers=headers,
            )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return repos


def _build_stack_text(username: str, repos: list[dict[str, Any]]) -> str:
    public = [r for r in repos if not r.get("fork")]
    public.sort(
        key=lambda r: (
            -(r.get("stargazers_count") or 0),
            r.get("pushed_at") or "",
        ),
        reverse=False,
    )
    languages: dict[str, int] = {}
    for repo in public:
        lang = repo.get("language")
        if lang:
            languages[lang] = languages.get(lang, 0) + 1

    lang_line = ", ".join(
        f"{name} ({count})" for name, count in sorted(languages.items(), key=lambda x: -x[1])
    )
    pinned_names = {
        "LightRAG",
        "MyPortfolio",
        "PriceMonitoring",
        "TGRecordsOfExpenses",
        "DynamicsAX",
        "tgDialog-Memory",
        "TGBotBothMemory",
    }
    pinned = [r for r in public if r.get("name") in pinned_names]
    rest = [r for r in public if r.get("name") not in pinned_names][:15]

    lines = [
        f"GitHub: https://github.com/{username}",
        f"Публичных репозиториев: {len(public)}",
        f"Языки: {lang_line or '—'}",
        "",
        "Закреплённые / ключевые проекты:",
    ]
    lines.extend(_format_repo(r) for r in pinned)
    if rest:
        lines.append("")
        lines.append("Другие репозитории (по активности):")
        lines.extend(_format_repo(r) for r in rest)
    return "\n".join(lines)


def _read_cache(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(data.get("fetched_at", 0)) > CACHE_TTL_SECONDS:
            return None
        text = str(data.get("stack_text") or "").strip()
        return text or None
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        logger.warning("github_stack cache read failed: %s", exc)
        return None


def _write_cache(path: Path, stack_text: str, username: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "username": username,
        "fetched_at": time.time(),
        "stack_text": stack_text,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_github_stack(
    *,
    username: str = DEFAULT_GITHUB_USER,
    token: str = "",
    cache_path: Path | None = None,
    http_client: httpx.Client | None = None,
) -> str:
    cache = cache_path or Path("data/github_stack_cache.json")
    cached = _read_cache(cache)
    if cached:
        return cached

    try:
        if http_client is not None:
            repos = _fetch_repos_with_client(http_client, username, token)
        else:
            repos = _fetch_repos(username, token)
        if repos:
            text = _build_stack_text(username, repos)
            _write_cache(cache, text, username)
            return text
    except Exception as exc:
        logger.warning("GitHub stack fetch failed: %s", exc)

    return STATIC_FALLBACK


def _fetch_repos_with_client(
    client: httpx.Client, username: str, token: str
) -> list[dict[str, Any]]:
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos: list[dict[str, Any]] = []
    page = 1
    while page <= 5:
        response = client.get(
            f"{GITHUB_API}/users/{username}/repos",
            params={
                "per_page": 100,
                "page": page,
                "sort": "pushed",
                "direction": "desc",
            },
            headers=headers,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos
