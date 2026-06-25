from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from src.analyzer.project_brief import (
    build_project_brief,
    buyer_checklist_issues,
    extract_buyer_checklist,
    extract_tz_facts,
)
from src.analyzer.response_qa import ResponseQaValidator, rule_check_alignment
from src.analyzer.response_strategy import build_generation_strategy
from src.config import Settings
from src.models import ProjectFull
from src.analyzer.response_text import kwork_compliance_issues, strip_response_markdown
from src.responses.portfolio_link import PORTFOLIO_URL, ensure_portfolio_link

logger = logging.getLogger(__name__)

_BANNED_OPENERS = (
    r"^добрый день",
    r"^здравствуйте",
    r"^доброго времени",
    r"^приветствую",
)

_BANNED_PHRASES = (
    "с удовольствием помогу",
    "имею большой опыт",
    "готов выполнить ваш проект",
    "буду рад сотрудничеству",
    "уважаемый заказчик",
    "обращайтесь",
)

GENERATION_SYSTEM_PROMPT = f"""\
Ты — опытный специалист по пресейлу и коммуникации с заказчиками.
Пишешь отклик от имени Александра Клычниковова (Python / AI / Telegram / MVP).
Это не массовая рассылка: каждый текст — под конкретный заказ, как от живого специалиста.

Главная цель: ощущение реального диалога, а не бота с шаблонами.

Перед текстом (внутренне) проанализируй задачу и выбери подход из strategy — не лепи один шаблон.

Сценарии (выбирается один, см. strategy.approach):
- understanding — показать, что задачу понял своими словами
- experience — 1 релевантный кейс, без простыни портфолио
- solution — как именно решишь, по шагам кратко
- risks — честные нюансы/риски и как их закроешь
- questions — 1–2 умных уточнения ТОЛЬКО по реальным пробелам в ТЗ (не задавай то, что уже написано)
- speed — как организуешь работу и сроки

Структуры (strategy.structure_variant):
A: понимание → решение → вопрос
B: опыт → организация → обсудить детали
C: замечание по ТЗ → риски → решение
D: 3–5 предложений, без вступительной воды

Стиль (strategy.writing_style): деловой / дружелюбный / экспертный / лаконичный / разговорный —
подстрой под customer_style в payload.

Запрещено начинать одинаково и использовать штампы:
«Добрый день», «С удовольствием помогу», «Имею большой опыт», «Готов выполнить ваш проект»,
«Буду рад сотрудничеству» и похожие клише.

Не копируй recent_responses: другие вступления, структуру и финал.

Согласованность с ТЗ (критично):
- Сначала прочитай project_brief и tz_facts в payload.
- Опиши суть задачи своими словами строго по ТЗ. Не придумывай парсинг/скрапинг, если их нет в project_brief.
- Если в ТЗ уже указаны источник (LinkedIn, сайт), что собирать (ссылки, посты) — НЕ спрашивай «откуда» и «какие данные».
- Вопрос в конце — только если в ТЗ реально не хватает детали (авторизация, объём, периодичность, формат файла).

Чеклист заказчика (если buyer_checklist не пуст):
- Ответь на КАЖДЫЙ пункт явно, коротким абзацем или фразой с меткой (Стоимость / Срок / Стек / Код / Передача).
- Стоимость и срок — в тексте тоже (дублируй поля формы: «Стоимость: … ₽», «Срок: … дней»).
- Стек: Python, aiogram, БД (PostgreSQL/SQLite) — по задаче.
- Готовность смотреть код: да, сначала аудит наработок.
- Передача: исходники, БД, инструкция по запуску, тест основных сценариев.

Из lightrag_context / github — только 1–2 факта, реально относящиеся к ЭТОМУ заказу.
Портфолио: {PORTFOLIO_URL} — один раз, органично, не отдельным блоком «моё портфолио:».

Длина: 900–2000 знаков (Kwork минимум ~150). Без markdown (**жирный**, заголовки #, ссылки [текст](url)).
Без списков с «- » в начале каждой строки (можно короткие абзацы). Без цены в тексте — цена в форме отдельно.

Правила Kwork (критично — иначе предупреждение от поддержки):
- Вся коммуникация только внутри Kwork. НЕ предлагай созвон, звонок, Zoom, Meet, Telegram/WhatsApp для связи.
- НЕ проси обменяться контактами, email, телефоном, «напишите напрямую».
- НЕ упоминай комиссию Kwork.
- GitHub и портфолио — только как примеры работ (URL текстом), не как способ связи.
- Финал: уточняющий вопрос по ТЗ или «готов ответить на вопросы в этом чате Kwork».

Верни только текст отклика.
"""


def _soft_banned_check(text: str) -> list[str]:
    issues: list[str] = []
    lower = text.lower().strip()
    for pattern in _BANNED_OPENERS:
        if re.search(pattern, lower):
            issues.append(f"opener:{pattern}")
    for phrase in _BANNED_PHRASES:
        if phrase in lower:
            issues.append(f"phrase:{phrase}")
    return issues


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
        self._owns_client = http_client is None
        self._qa = qa_validator or ResponseQaValidator(
            settings, http_client=http_client
        )
        self._owns_qa = qa_validator is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=90.0)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
        if self._owns_qa:
            self._qa.close()

    def _call_api(self, body: dict[str, Any], project_id: str) -> str:
        url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        client = self._get_client()
        last_exc: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(4):
            response = client.post(url, headers=headers, json=body)
            if response.status_code == 429 and attempt < 3:
                wait = 2 ** attempt
                logger.warning(
                    "gpt_generate_response rate limited, retry in %ss project_id=%s",
                    wait,
                    project_id,
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        else:
            if last_exc:
                raise last_exc
        if response is None:
            raise RuntimeError("gpt_generate_response: no response")
        return str(response.json()["choices"][0]["message"]["content"]).strip()

    def generate(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        recent_responses: dict[str, Any] | None = None,
        platform_label: str | None = None,
    ) -> str:
        platform = platform_label or project.platform
        strategy = build_generation_strategy(project)
        brief = build_project_brief(project)
        tz_facts = extract_tz_facts(project)
        buyer_checklist = extract_buyer_checklist(project)
        user_payload = {
            "task": (
                "Напиши уникальный отклик под этот заказ. Следуй strategy и tz_facts. "
                "Не задавай вопросы о том, что уже есть в project_brief. "
                "Не повторяй recent_responses."
            ),
            "platform": platform,
            "project": project.model_dump(mode="json"),
            "project_brief": brief,
            "tz_facts": tz_facts,
            "buyer_checklist": buyer_checklist,
            "strategy": strategy,
            "lightrag_context": lightrag_context,
            "response_examples": examples,
            "recent_responses": recent_responses or {"count": 0},
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
            "temperature": 0.82,
        }

        logger.info(
            "gpt_generate_response project_id=%s approach=%s variant=%s",
            project.project_id,
            strategy.get("approach"),
            strategy.get("structure_variant"),
        )

        text = self._call_api(body, project.project_id)
        text = strip_response_markdown(text)
        text = self._postprocess_with_checks(project, user_payload, body, text)
        return ensure_portfolio_link(strip_response_markdown(text))

    def _postprocess_with_checks(
        self,
        project: ProjectFull,
        user_payload: dict[str, Any],
        body: dict[str, Any],
        text: str,
    ) -> str:
        issues = _soft_banned_check(text) + [
            f"kwork:{v}" for v in kwork_compliance_issues(text)
        ] + [
            f"checklist:{v}" for v in buyer_checklist_issues(project, text)
        ] + _topic_mismatch_issues(project, text)
        if issues:
            logger.info(
                "gpt_generate_response retry banned phrases project_id=%s %s",
                project.project_id,
                issues,
            )
            retry_payload = dict(user_payload)
            retry_payload["banned_detected"] = issues
            retry_payload["task"] += (
                " Перепиши: убери клише и смени вступление/структуру."
            )
            if any(i.startswith("kwork:") for i in issues):
                retry_payload["task"] += (
                    " Убери созвон/звонок/мессенджеры/контакты вне Kwork. "
                    "Финал — вопрос по ТЗ или готовность ответить в чате Kwork."
                )
            if any("checklist" in i for i in issues):
                retry_payload["task"] += (
                    " Ответь на все пункты buyer_checklist явно "
                    "(стоимость, срок, стек, аудит кода, состав передачи)."
                )
            if any("topic:" in i for i in issues):
                retry_payload["task"] += (
                    " Убери парсинг/скрапинг — их нет в ТЗ. Опиши задачу как в project_brief."
                )
            body["messages"][1]["content"] = json.dumps(retry_payload, ensure_ascii=False)
            body["temperature"] = 0.9
            text = self._call_api(body, project.project_id)
            text = strip_response_markdown(text)

        for attempt in range(2):
            qa = self._qa.validate(project, text)
            rule_issues = rule_check_alignment(project, text)
            all_issues = list(qa.get("issues") or []) + rule_issues
            all_issues += [f"kwork:{v}" for v in kwork_compliance_issues(text)]
            all_issues += [f"checklist:{v}" for v in buyer_checklist_issues(project, text)]
            all_issues += _topic_mismatch_issues(project, text)
            if (
                qa.get("aligned", True)
                and not rule_issues
                and not kwork_compliance_issues(text)
                and not buyer_checklist_issues(project, text)
                and not _topic_mismatch_issues(project, text)
            ):
                return text
            logger.info(
                "gpt_generate_response qa_retry project_id=%s issues=%s attempt=%s",
                project.project_id,
                all_issues,
                attempt,
            )
            retry_payload = dict(user_payload)
            retry_payload["qa_issues"] = all_issues
            retry_payload["task"] += (
                " Перепиши отклик: убери вопросы о том, что уже указано в project_brief/tz_facts. "
                "Начни с демонстрации понимания задачи из ТЗ. "
                "Соблюдай правила Kwork: без созвонов, мессенджеров и контактов вне площадки. "
                f"Проблемы проверки: {'; '.join(all_issues[:5])}"
            )
            body["messages"][1]["content"] = json.dumps(retry_payload, ensure_ascii=False)
            body["temperature"] = 0.65
            text = self._call_api(body, project.project_id)
            text = strip_response_markdown(text)

        return strip_response_markdown(text)


_PARSE_HALLUCINATION_RE = re.compile(r"парс\w*|скрап\w*", re.I)
_BOT_BRIEF_RE = re.compile(r"telegram[- ]?бот|aiogram|телеграм[- ]?бот", re.I)


def _topic_mismatch_issues(project: ProjectFull, text: str) -> list[str]:
    brief = build_project_brief(project)
    if not brief or re.search(r"парс\w*|скрап\w*", brief, re.I):
        return []
    if _BOT_BRIEF_RE.search(brief) and _PARSE_HALLUCINATION_RE.search(text):
        return ["topic:парсинг_не_в_тз"]
    return []

