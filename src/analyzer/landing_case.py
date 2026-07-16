"""Кейс MyPortfolio для заказов на лендинг / одностраничник."""
from __future__ import annotations

import re

from src.analyzer.project_brief import build_project_brief
from src.models import ProjectFull

LANDING_PATTERN = (
    r"лендинг|одностранич\w*|landing(?:\s*page)?|сайт[- ]?визитк|"
    r"одно\s*странич\w*\s+сайт|сайт\s+по\s+тз|визитк\w*\s+сайт"
)
LANDING_TASK_RE = re.compile(LANDING_PATTERN, re.I)

MY_PORTFOLIO_REPO = "https://github.com/alexklychnikov-ui/MyPortfolio"

MY_PORTFOLIO_CASE_CONTEXT = f"""\
## Подтверждённый кейс: лендинг / одностраничный сайт
Репозиторий: {MY_PORTFOLIO_REPO}
Что сделано: production лендинг-портфолио (разработка, не «только дизайн») — Next.js 16 App Router,
React 19, TypeScript, Tailwind CSS, секции Projects/Services/Skills, RU/EN i18n, FastAPI backend,
aiogram Telegram-bot, GitHub→OpenAI pipeline, PostgreSQL/Prisma, Docker Compose, VPS + nginx SSL.
Релевантно для заказов Kwork: одностраничник по ТЗ, лендинг, сайт-визитка, небольшой сайт на React/Next.js.
При совпадении типа задачи с этим кейсом — score >= 7, matched_skills: Next.js, TypeScript, Tailwind, веб-MVP.
"""

MY_PORTFOLIO_GITHUB_LINE = (
    "- MyPortfolio — лендинг-портфолио: Next.js 16, React 19, TypeScript, Tailwind, "
    "FastAPI, aiogram, PostgreSQL/Prisma, Docker, i18n RU/EN, продакшн VPS"
)


def project_brief_text(project: ProjectFull) -> str:
    return build_project_brief(project)


def is_landing_project(project: ProjectFull) -> bool:
    brief = project_brief_text(project)
    return bool(brief and LANDING_TASK_RE.search(brief))


def landing_scoring_context() -> str:
    return MY_PORTFOLIO_CASE_CONTEXT.strip()
