from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from src.analyzer.gpt_scorer import _extract_json
from src.analyzer.project_brief import build_project_brief, extract_tz_facts
from src.config import Settings
from src.models import ProjectFull

logger = logging.getLogger(__name__)

_FORBIDDEN_WHEN_SPECIFIED = (
    r"какие именно данные",
    r"что именно (?:вы )?планируете парс",
    r"что (?:именно )?парс",
    r"откуда (?:вы )?планируете",
    r"какие данные (?:вы )?планируете",
    r"какой источник",
    r"с какого сайта",
    r"что нужно собирать",
)

_TZ_KEYWORDS = (
    "linkedin",
    "парс",
    "скрап",
    "ссылк",
    "публикац",
    "telegram",
    "avito",
)

QA_SYSTEM_PROMPT = """\
Ты проверяешь согласованность отклика фрилансера с текстом заказа (ТЗ).

Найди несостыковки:
- отклик спрашивает то, что уже явно указано в ТЗ (источник, что парсить, формат);
- отклик игнорирует ключевую суть ТЗ;
- отклик противоречит ТЗ.

Уточняющий вопрос допустим ТОЛЬКО по реальным пробелам: авторизация, объём, периодичность, формат выгрузки — если этого нет в ТЗ.

Верни СТРОГО JSON:
{
  "aligned": true,
  "issues": [],
  "tz_facts_used": [],
  "redundant_questions": []
}
"""


def rule_check_alignment(project: ProjectFull, response: str) -> list[str]:
    brief = build_project_brief(project).lower()
    resp = response.lower()
    if len(brief) < 20:
        return []
    if not any(k in brief for k in _TZ_KEYWORDS):
        return []
    issues: list[str] = []
    for pattern in _FORBIDDEN_WHEN_SPECIFIED:
        if re.search(pattern, resp):
            issues.append(f"redundant_question: {pattern}")
    return issues


class ResponseQaValidator:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def validate(self, project: ProjectFull, response: str) -> dict[str, Any]:
        issues = rule_check_alignment(project, response)
        if issues and len(build_project_brief(project)) < 120:
            return {"aligned": False, "issues": issues, "redundant_questions": issues}

        facts = extract_tz_facts(project)
        if not facts:
            return {"aligned": True, "issues": [], "redundant_questions": []}

        payload = {
            "project_brief": build_project_brief(project),
            "tz_facts": facts,
            "response_text": response,
        }
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": QA_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._get_client().post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = _extract_json(resp.json()["choices"][0]["message"]["content"])
            gpt_issues = list(data.get("issues") or [])
            redundant = list(data.get("redundant_questions") or [])
            all_issues = issues + gpt_issues + redundant
            aligned = bool(data.get("aligned", True)) and not all_issues
            return {
                "aligned": aligned,
                "issues": all_issues,
                "redundant_questions": redundant,
                "tz_facts_used": data.get("tz_facts_used") or [],
            }
        except Exception:
            logger.exception("response_qa_gpt_failed project_id=%s", project.project_id)
            return {
                "aligned": len(issues) == 0,
                "issues": issues,
                "redundant_questions": issues,
            }
