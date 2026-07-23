from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from src.config import Settings
from src.models import GptScoreResult, ProjectFull

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path("promptFrilance.md")

SCORING_SYSTEM_PROMPT = """\
Ты — ассистент фрилансера Александра Клычниковова (Python/AI/Telegram/MVP).

Оцени КОНКРЕТНЫЙ проект: сопоставь требования ТЗ с реальным стеком из github_stack
(репозитории https://github.com/alexklychnikov-ui). lightrag_context — только релевантные кейсы по этому заказу.
Не завышай score. Если ключевая технология из ТЗ отсутствует в github_stack — score <= 4.

Стек Александра (подходит):
- Python, FastAPI, aiogram, Telegram-боты, парсинг, API-интеграции
- AI/LLM, RAG, автоматизация, MVP
- Лендинг, одностраничный сайт, сайт-визитка на Next.js/React/Tailwind — кейс MyPortfolio (github.com/alexklychnikov-ui/MyPortfolio)

Явно НЕ подходит / нет опыта (score <= 4, fit=false, recommendation=пропустить):
- Нативные мобильные приложения Android и iOS (Swift, Kotlin, Java, Objective-C)
- Flutter / React Native / Xamarin как основная разработка (без подтверждённого кейса в контексте)
- Чистый дизайн/макет БЕЗ разработки (только Figma/PSD, «нарисовать», без вёрстки и кода)
- WordPress/Тильда без кастомного кода
- Штатная позиция, не разовый проект

ВАЖНО про лендинги:
- «Создать одностраничный сайт / лендинг по ТЗ» на Next.js/React — это веб-MVP, НЕ «чистый дизайн»
- Если в scoring_context есть кейс MyPortfolio — score >= 7, fit=true, suggested_project_type=Веб-MVP
- matched_skills для лендинга: Next.js, TypeScript, Tailwind, React (если есть в github_stack/MyPortfolio)

Критерии score >= 7:
- Основная работа: Python / боты / AI / API / автоматизация / веб-MVP / лендинг на Next.js
- Есть релевантный кейс в github_stack или lightrag_context (для лендинга — MyPortfolio)
- Реализуемо одним разработчиком за разумный срок

Микробюджет 500–1000 ₽:
- Низкий бюджет — НЕ повод снижать score, если задача в стеке (Python/боты/AI/парсинг/API/MVP)
- Оценивай по соответствию стеку; matched_skills заполняй при любом реальном совпадении технологий
- Для таких заказов score 4–6 допустим при явном stack-match (даже если fit=false по общим правилам)

Правила полей (обязательно):
- reason: 1–2 предложения — почему именно этот score для ЭТОГО заказа (упомяни суть ТЗ)
- matched_skills: только навыки, подтверждённые ТЗ И наличием в github_stack/lightrag_context
- risks: 2–4 риска СПЕЦИФИЧНЫЕ для этого ТЗ (технологии, объём, интеграции, сроки, домен заказчика).
  ЗАПРЕЩЕНЫ шаблонные риски: «отсутствие чёткого описания», «неизвестный бюджет», «недопонимание требований» —
  используй их только если в описании проекта реально пусто (< 50 символов) или бюджет отсутствует в данных
- suggested_project_type: Парсинг | Telegram-бот | AI/RAG | Веб-MVP | Интеграция | Автоматизация | Другое

Верни СТРОГО JSON:
{
  "score": 0,
  "fit": false,
  "reason": "",
  "matched_skills": [],
  "risks": [],
  "suggested_project_type": "",
  "competition_level": "medium",
  "recommendation": "пропустить"
}
"""

_NATIVE_MOBILE_RE = re.compile(
    r"(android|ios|iphone|ipad|swift|kotlin|objective-?c|flutter|"
    r"react\s*native|xamarin|мобильн\w*\s+прилож)",
    re.IGNORECASE,
)

_GENERIC_RISK_PATTERNS = (
    r"отсутствие четкого описания",
    r"отсутствие чёткого описания",
    r"неизвестный бюджет",
    r"недопониман\w*\s+требован",
    r"может привести к недопониман",
    r"ограничить возможности реализации",
)


def load_scoring_system_prompt(prompt_path: Path | None = None) -> str:
    path = prompt_path or DEFAULT_PROMPT_PATH
    if not path.exists():
        return SCORING_SYSTEM_PROMPT
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"\*\*Системный промпт \(шаблон\):\*\*\s*\n+```\s*\n(.*?)\n```",
        text,
        flags=re.DOTALL,
    )
    if match:
        block = match.group(1).strip()
        if len(block) > 400 and "не подходит" in block.lower():
            return block
    return SCORING_SYSTEM_PROMPT


def _extract_json(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


_COMPETITION_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "низкий": "low",
    "низкая": "low",
    "низкое": "low",
    "средний": "medium",
    "средняя": "medium",
    "среднее": "medium",
    "высокий": "high",
    "высокая": "high",
    "высокое": "high",
}


def _project_text(project: ProjectFull) -> str:
    parts = [
        project.title or "",
        project.full_description or "",
        " ".join(project.tags or []),
        project.desired_budget or "",
        project.max_budget or "",
    ]
    return " ".join(parts)


def _filter_generic_risks(risks: list[str], project: ProjectFull) -> list[str]:
    desc_len = len((project.full_description or "").strip())
    has_budget = bool(project.desired_budget or project.max_budget)
    filtered: list[str] = []
    for risk in risks:
        lower = risk.lower()
        if any(re.search(p, lower) for p in _GENERIC_RISK_PATTERNS):
            if "описан" in lower and desc_len >= 80:
                continue
            if "бюджет" in lower and has_budget:
                continue
            if "недопониман" in lower and desc_len >= 120:
                continue
        filtered.append(risk)
    return filtered


def _apply_score_guardrails(
    data: dict[str, Any],
    project: ProjectFull,
    *,
    min_fit_score: int = 7,
) -> dict[str, Any]:
    text = _project_text(project)
    risks = _filter_generic_risks(list(data.get("risks") or []), project)

    if _NATIVE_MOBILE_RE.search(text):
        if int(data.get("score", 0)) > 4:
            data["score"] = min(int(data["score"]), 3)
        data["fit"] = False
        data["recommendation"] = "пропустить"
        mobile_risk = (
            "Нативные Android/iOS — нет в стеке GitHub-репозиториев "
            "(Python/боты/AI/веб-MVP)"
        )
        if not any("android" in r.lower() or "ios" in r.lower() or "мобил" in r.lower() for r in risks):
            risks.insert(0, mobile_risk)
        reason = str(data.get("reason") or "")
        if "мобил" not in reason.lower() and "android" not in reason.lower():
            data["reason"] = (
                f"{reason} Ключевая часть — mobile, в github_stack нет Kotlin/Swift/Flutter."
            ).strip()

    if not risks:
        risks = [
            f"Объём: {project.title[:80]} — проверить реализуемость в одиночку",
        ]
    data["risks"] = risks[:5]
    data["fit"] = bool(data.get("fit")) and int(data.get("score", 0)) >= min_fit_score
    if not data["fit"]:
        data["recommendation"] = "пропустить"
    return data


def _normalize_score_payload(raw: dict[str, Any], project: ProjectFull) -> dict[str, Any]:
    data = dict(raw)

    score_raw = data.get("score", 0)
    if isinstance(score_raw, str):
        score_raw = int(re.sub(r"\D", "", score_raw) or 0)
    data["score"] = max(0, min(10, int(score_raw or 0)))

    fit_val = data.get("fit")
    if isinstance(fit_val, str):
        fit_lower = fit_val.strip().lower()
        if fit_lower in _COMPETITION_MAP and "competition_level" not in data:
            data["competition_level"] = _COMPETITION_MAP[fit_lower]
        if fit_lower in ("true", "yes", "да", "1"):
            data["fit"] = True
        elif fit_lower in ("false", "no", "нет", "0"):
            data["fit"] = False
        elif fit_lower in _COMPETITION_MAP:
            data["fit"] = data["score"] >= 7
        else:
            data["fit"] = data["score"] >= 7
    elif fit_val is None:
        data["fit"] = data["score"] >= 7

    comp = str(data.get("competition_level", "")).strip().lower()
    data["competition_level"] = _COMPETITION_MAP.get(
        comp, comp if comp in _COMPETITION_MAP.values() else "medium"
    )

    rec = str(data.get("recommendation", "")).strip().lower()
    for key in ("откликаться", "пропустить", "наблюдать"):
        if key in rec:
            data["recommendation"] = key
            break
    else:
        data["recommendation"] = "откликаться" if data["fit"] and data["score"] >= 7 else "пропустить"

    for field in ("matched_skills", "risks"):
        val = data.get(field)
        if val is None:
            data[field] = []
        elif isinstance(val, str):
            data[field] = [val] if val.strip() else []

    if not str(data.get("suggested_project_type") or "").strip():
        data["suggested_project_type"] = "Другое"
    if not str(data.get("reason") or "").strip():
        data["reason"] = ""

    return _apply_score_guardrails(data, project)


class GptScorer:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.settings = settings
        self._client = http_client
        self._owns_client = http_client is None
        self._system_prompt = system_prompt or load_scoring_system_prompt()

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def score(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        min_fit_score: int | None = None,
    ) -> GptScoreResult:
        fit_threshold = (
            min_fit_score
            if min_fit_score is not None
            else self.settings.min_gpt_score
        )
        user_payload = {
            "task": (
                "Сопоставь требования заказа (заголовок, описание, теги, бюджет) со стеком "
                "из github_stack (репозитории alexklychnikov-ui). lightrag_context — кейсы по этому ТЗ. "
                "Риски — только по сути ЭТОГО заказа."
            ),
            "project": project.model_dump(mode="json"),
            "scoring_context": lightrag_context,
            "response_examples": examples,
        }
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
        }
        url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "gpt_score project_id=%s platform=%s",
            project.project_id,
            project.platform,
        )
        client = self._get_client()
        last_exc: Exception | None = None
        for attempt in range(4):
            response = client.post(url, headers=headers, json=body)
            if response.status_code == 429 and attempt < 3:
                wait = 2 ** attempt
                logger.warning("gpt_score rate limited, retry in %ss", wait)
                time.sleep(wait)
                continue
            try:
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                raise
        else:
            if last_exc:
                raise last_exc
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = _normalize_score_payload(_extract_json(content), project)
        guarded = _apply_score_guardrails(
            parsed, project, min_fit_score=fit_threshold
        )
        return GptScoreResult.model_validate(guarded)
