from __future__ import annotations

import json
import logging

import httpx

from src.config import Settings
from src.models import ProjectFull

logger = logging.getLogger(__name__)

GENERATION_SYSTEM_PROMPT = """\
Напиши отклик на фриланс-проект для Александра Клычниковова (Python/AI/Telegram/MVP).

Правила:
- Первый абзац: внимание + фокус + польза (конкретно про ЭТОТ проект)
- Показать понимание задачи (2-4 буллета)
- Релевантный кейс (1-2 ссылки GitHub/портфолио)
- Сроки/бюджет — ориентир, не выдумывать точную цену без ТЗ
- Тон: уверенный, по делу, без «здравствуйте уважаемые»
- Длина: 1500-2500 знаков
- Верни только текст отклика, без markdown-заголовков
"""


class GptResponseGenerator:
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
            self._client = httpx.Client(timeout=90.0)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def generate(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        platform_label: str | None = None,
    ) -> str:
        platform = platform_label or project.platform
        user_payload = {
            "platform": platform,
            "project": project.model_dump(mode="json"),
            "lightrag_context": lightrag_context,
            "response_examples": examples,
        }
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.4,
        }
        url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "gpt_generate_response project_id=%s platform=%s",
            project.project_id,
            project.platform,
        )
        response = self._get_client().post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()
