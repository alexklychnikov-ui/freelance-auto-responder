from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from src.adapters.kwork_delivery import snap_delivery_days
from src.adapters.kwork_pricing import clamp_price_to_budget, suggest_offer_price
from src.analyzer.gpt_scorer import _extract_json
from src.config import Settings
from src.models import OfferTerms, ProjectFull

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Ты оцениваешь коммерческое предложение фрилансера на Kwork.

По названию, описанию проекта, бюджету заказчика и тексту отклика:
1) Составь краткий план выполнения (plan_summary, 1-2 предложения).
2) Предложи реалистичную цену price_rub (целое число в рублях).
3) Предложи срок delivery_days (целое число дней) — только из списка Kwork: 1,2,3,4,5,6,7,10,14,21,30,60.

Правила:
- Цена из объёма работ, не константа. Учитывай desired/max бюджет заказчика.
- Если max бюджет указан — не превышай его.
- Если бюджет не указан — оцени рынок по описанию (не занижай без причины).
- Срок из плана: простые задачи 1-3 дня, средние 5-10, сложные 14-21.
- Если в отклике уже назван срок — согласуй delivery_days с ним.

Верни СТРОГО JSON:
{"price_rub": 0, "delivery_days": 0, "plan_summary": ""}
"""

MARKET_COST_SYSTEM_PROMPT = """\
Оцени реалистичную рыночную стоимость выполнения проекта фрилансером (Python / AI / Telegram / интеграции).

По названию, описанию и контексту оцени объём работ и цену price_rub (целое число в рублях).
НЕ подстраивайся под бюджет заказчика — нужна честная рыночная оценка трудозатрат.

Верни СТРОГО JSON:
{"price_rub": 0, "plan_summary": ""}
"""


def _days_from_response_text(text: str) -> int | None:
    match = re.search(
        r"(\d{1,2})\s*(?:рабочих?\s+)?(?:дн|дня|дней|day|days)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return max(1, min(30, int(match.group(1))))


def _normalize_terms(raw: dict[str, Any], project: ProjectFull) -> OfferTerms:
    price_raw = raw.get("price_rub", raw.get("price", 0))
    if isinstance(price_raw, str):
        price_raw = int(re.sub(r"\D", "", price_raw) or 0)
    price = int(price_raw or 0)

    days_raw = raw.get("delivery_days", raw.get("days", 14))
    if isinstance(days_raw, str):
        days_raw = int(re.sub(r"\D", "", days_raw) or 14)
    days = snap_delivery_days(int(days_raw or 14))

    if price < 500:
        price = int(suggest_offer_price(project))
    price = clamp_price_to_budget(price, project)

    summary = str(raw.get("plan_summary") or "").strip()
    return OfferTerms(price_rub=price, delivery_days=days, plan_summary=summary)


class GptOfferEstimator:
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

    def estimate(
        self,
        project: ProjectFull,
        response_text: str,
        *,
        lightrag_context: str = "",
    ) -> OfferTerms:
        user_payload = {
            "project": project.model_dump(mode="json"),
            "response_text": response_text,
            "lightrag_context": lightrag_context,
        }
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
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
            "gpt_estimate_offer project_id=%s platform=%s",
            project.project_id,
            project.platform,
        )
        try:
            response = self._get_client().post(url, headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return _normalize_terms(_extract_json(content), project)
        except Exception:
            logger.exception("gpt_estimate_offer_failed project_id=%s", project.project_id)
            return self.fallback(project, response_text)

    def estimate_market_cost(
        self,
        project: ProjectFull,
        lightrag_context: str = "",
    ) -> int:
        user_payload = {
            "project": project.model_dump(mode="json"),
            "lightrag_context": lightrag_context,
        }
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": MARKET_COST_SYSTEM_PROMPT},
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
            "gpt_estimate_market_cost project_id=%s platform=%s",
            project.project_id,
            project.platform,
        )
        try:
            response = self._get_client().post(url, headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            raw = _extract_json(content)
            price_raw = raw.get("price_rub", raw.get("price", 0))
            if isinstance(price_raw, str):
                price_raw = int(re.sub(r"\D", "", price_raw) or 0)
            return max(0, int(price_raw or 0))
        except Exception:
            logger.exception(
                "gpt_estimate_market_cost_failed project_id=%s", project.project_id
            )
            return int(suggest_offer_price(project))

    def fallback(self, project: ProjectFull, response_text: str) -> OfferTerms:
        price = int(suggest_offer_price(project))
        days = _days_from_response_text(response_text) or self.settings.default_offer_days
        return OfferTerms(
            price_rub=price,
            delivery_days=snap_delivery_days(days),
            plan_summary="",
        )
