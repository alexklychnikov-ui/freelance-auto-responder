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


def load_scoring_system_prompt(prompt_path: Path | None = None) -> str:
    path = prompt_path or DEFAULT_PROMPT_PATH
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"Ты — ассистент фрилансера Александра Клычниковова.*?```\s*\n(\{[\s\S]*?\})\s*\n```",
        text,
        flags=re.DOTALL,
    )
    if match:
        json_template = match.group(1)
        intro = text[match.start() : match.start() + 200].split("```")[0].strip()
        return f"{intro}\n\nВерни СТРОГО JSON:\n{json_template}"

    return (
        "Ты — ассистент фрилансера Александра Клычниковова (Python/AI/Telegram/MVP). "
        "Оцени проект на соответствие стеку. Верни СТРОГО JSON с полями: "
        "score, fit, reason, matched_skills, risks, suggested_project_type, "
        "competition_level, recommendation."
    )


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


def _normalize_score_payload(raw: dict[str, Any]) -> dict[str, Any]:
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
    data["competition_level"] = _COMPETITION_MAP.get(comp, comp if comp in _COMPETITION_MAP.values() else "medium")

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

    return data


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
    ) -> GptScoreResult:
        user_payload = {
            "project": project.model_dump(mode="json"),
            "lightrag_context": lightrag_context,
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
            "temperature": 0.2,
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
        parsed = _normalize_score_payload(_extract_json(content))
        return GptScoreResult.model_validate(parsed)
