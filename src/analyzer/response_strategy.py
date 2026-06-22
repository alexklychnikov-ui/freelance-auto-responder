from __future__ import annotations

import re

from src.analyzer.project_brief import task_is_clear, tz_is_vague
from src.models import ProjectFull

_STRUCTURE_VARIANTS = ("A", "B", "C", "D")

_APPROACHES = (
    "understanding",  # акцент на понимании задачи
    "experience",  # похожие проекты
    "solution",  # предлагаемое решение
    "risks",  # риски и нюансы
    "questions",  # уточняющие вопросы
    "speed",  # скорость и организация
)

_TECH_RE = re.compile(
    r"\b(python|fastapi|aiogram|telegram|api|postgresql|docker|rag|llm|gpt|"
    r"парсинг|бот|интеграц|автоматиз)\b",
    re.IGNORECASE,
)
_URGENT_RE = re.compile(
    r"(срочн|asap|сегодня|завтра|вчера|горящ|быстр|дедлайн|срок\s*—?\s*\d)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?|как\s+вы|подскаж|уточн|вопрос", re.IGNORECASE)


def _combined_text(project: ProjectFull) -> str:
    from src.analyzer.project_brief import build_project_brief

    return build_project_brief(project)


def _desc(project: ProjectFull) -> str:
    return _combined_text(project)


def _customer_style(project: ProjectFull) -> dict[str, str | bool | int]:
    desc = _desc(project)
    title = (project.title or "").strip()
    combined = f"{title}\n{desc}"
    length = len(desc)
    return {
        "description_length": length,
        "is_brief": length < 200,
        "is_detailed": length >= 800,
        "uses_technical_terms": bool(_TECH_RE.search(combined)),
        "asks_questions": bool(_QUESTION_RE.search(combined)),
        "feels_urgent": bool(_URGENT_RE.search(combined)),
        "tone_hint": (
            "лаконичный заказчик — отвечай короче"
            if length < 200
            else "развёрнутый заказчик — можно чуть подробнее"
            if length >= 800
            else "нейтральный объём — умеренная длина"
        ),
    }


def _pick_approach(project: ProjectFull) -> str:
    style = _customer_style(project)
    if task_is_clear(project):
        if style["uses_technical_terms"]:
            return "solution"
        return "understanding"
    if style["feels_urgent"]:
        return "speed"
    if tz_is_vague(project) and style["is_brief"]:
        return "questions"
    if style["asks_questions"]:
        return "understanding"
    if style["uses_technical_terms"]:
        return "solution"
    pid = sum(ord(c) for c in project.project_id)
    return _APPROACHES[pid % len(_APPROACHES)]


def _pick_structure(project: ProjectFull, approach: str) -> str:
    mapping = {
        "understanding": "A",
        "experience": "B",
        "solution": "A",
        "risks": "C",
        "questions": "A",
        "speed": "D",
    }
    base = mapping.get(approach, "A")
    if tz_is_vague(project) and _customer_style(project)["is_brief"]:
        return "D"
    pid = int(re.sub(r"\D", "", project.project_id) or "0")
    variant = _STRUCTURE_VARIANTS[(pid + ord(base)) % len(_STRUCTURE_VARIANTS)]
    return variant


def _pick_style(project: ProjectFull, approach: str) -> str:
    style = _customer_style(project)
    if style["uses_technical_terms"]:
        return "экспертный"
    if style["is_brief"]:
        return "лаконичный"
    if approach in ("questions", "understanding"):
        return "дружелюбный"
    if approach == "risks":
        return "деловой"
    return "разговорно-деловой"


def build_generation_strategy(project: ProjectFull) -> dict[str, str]:
    approach = _pick_approach(project)
    structure = _pick_structure(project, approach)
    customer = _customer_style(project)
    return {
        "approach": approach,
        "structure_variant": structure,
        "writing_style": _pick_style(project, approach),
        "customer_style": str(customer),
        "structure_guide": {
            "A": "понимание задачи → краткое решение → вопрос",
            "B": "похожий опыт → организация работы → предложение обсудить",
            "C": "замечание по постановке → риски/нюансы → решение",
            "D": "3–5 предложений без вступлений и воды",
        }[structure],
    }
