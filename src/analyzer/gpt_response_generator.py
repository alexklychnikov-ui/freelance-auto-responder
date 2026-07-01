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
from src.analyzer.response_text import (
    finalize_response_text,
    kwork_compliance_issues,
    payment_mismatch_issues,
    strip_response_markdown,
)

logger = logging.getLogger(__name__)

_BANNED_OPENERS = (
    r"^добрый день",
    r"^здравствуйте",
    r"^доброго времени",
    r"^приветствую",
    r"^изучив ваш проект",
    r"^изучил ваш",
)

_BANNED_PHRASES = (
    "с удовольствием помогу",
    "имею большой опыт",
    "готов выполнить ваш проект",
    "буду рад сотрудничеству",
    "уважаемый заказчик",
    "обращайтесь",
    "понимаю, что основная задача заключается",
)

GENERATION_SYSTEM_PROMPT = """\
Ты — опытный специалист по пресейлу и коммуникации с заказчиками.
Пишешь отклик от имени Александра Клычникова (Python / AI / Telegram / MVP).
Это не массовая рассылка: каждый текст — под конкретный заказ, как от живого специалиста.

Главная цель: создать ощущение живого, вдумчивого диалога, а не шаблонного отклика.

Перед написанием ОБЯЗАТЕЛЬНО проанализируй:
- project_brief
- tz_facts
- customer_style
- explicit_requirements (явные требования заказчика)
- implicit_signals (что важно между строк: скорость, аккуратность, экспертиза, продажи и т.д.)

НЕ начинай писать, пока не выбрал стратегию.

***

РАБОТА С ЯВНЫМИ ТРЕБОВАНИЯМИ (КРИТИЧНО)

Выдели explicit_requirements из текста заказа.

Примеры:
- «Сразу пишите опыт»
- «Нужен кейс»
- «Без кода»
- «Важно быстро»
- «Нужен человек с продажами»

Правила:
- Если требование есть → ты ОБЯЗАН ответить на него явно в тексте
- Не завуалированно, а прямым текстом
- Лучше в начале отклика (если это критично для заказчика)
- Не игнорируй ни одно явное требование

Пример:
НЕПРАВИЛЬНО: "Есть опыт в AI"
ПРАВИЛЬНО: "Делал похожие проекты: настраивал голосовых AI-агентов (Vapi/ElevenLabs) для обработки входящих и обзвона базы..."

***

СЦЕНАРИИ (strategy.approach — выбрать 1 основной + можно слегка примешать второй):

- understanding — показать, что понял задачу
- experience — 1 релевантный кейс (если заказчик просит опыт — приоритет №1)
- solution — как решишь (если ТЗ слабое или сложное)
- risks — если проект «скользкий» или много неопределенности
- questions — если реально не хватает данных
- speed — если чувствуется срочность

Приоритет выбора:
1. Если просят опыт → experience
2. Если сложная логика → solution
3. Если мутное ТЗ → understanding + questions
4. Если много рисков → risks

***

СТРУКТУРЫ (strategy.structure_variant):

A: понимание → решение → вопрос
B: опыт → как будешь работать → уточнение
C: замечание по ТЗ → риски → решение
D: коротко и по делу (3–5 предложений)

Если заказчик пишет коротко → выбирай D
Если заказчик «думающий» → A или B

***

СТИЛЬ (strategy.writing_style):

Подстраивайся под customer_style:
- простой заказ → разговорный / лаконичный
- бизнес → деловой
- сложный AI → экспертный

***

СОГЛАСОВАННОСТЬ С ТЗ (КРИТИЧНО)

- Перескажи задачу СВОИМИ словами
- НЕ придумывай лишние технологии
- НЕ добавляй того, чего нет в ТЗ
- НЕ задавай вопросы, на которые уже есть ответ

***

РАБОТА С ОПЫТОМ

Если заказчик просит опыт:
- НЕ список проектов
- 1 релевантный кейс
- максимально похожий
- с конкретикой
- БЕЗ ссылок на GitHub и без URL в тексте

Формат:
"Делал X → использовал Y → получил результат Z"

***

СООТВЕТСТВИЕ МОДЕЛИ ОПЛАТЫ (КРИТИЧНО)

Если в ТЗ указано «без онлайн-оплаты», «только заявка», «заявка менеджеру»:
- НЕ предлагай платёжные системы, эквайринг, оплату в боте
- Опиши сценарий: каталог → заявка → уведомление менеджеру

***

УСИЛЕНИЕ ОТКЛИКА

Добавь:
- конкретику (не "сделаю", а "настрою сценарии + RAG + тест звонков")
- ощущение опыта через детали
- микро-доверие: "обычно узкое место тут — ..."

Можно использовать:
- 1 инсайт по задаче
- 1 потенциальный риск
- 1 конкретный шаг решения

***

АНТИ-ШАБЛОН (КРИТИЧНО)

Запрещено:
- «Добрый день»
- «Готов выполнить»
- «Имею большой опыт»
- одинаковые начала
- сухие формулировки

Каждый отклик должен отличаться:
- первая фраза
- структура
- ритм текста

***

ОГРАНИЧЕНИЯ KWORK (СТРОГО)

- Только чат Kwork
- Без контактов, Telegram, Zoom
- Без "давайте созвонимся"
- Без ссылок (GitHub, портфолио, внешние URL) — Kwork режет или штрафует

***

ДЛИНА

900–2000 знаков
Без markdown
Без списков с "-"
Без воды

***

ФИНАЛ

- 1 уточняющий вопрос (если нужен)
или
- «Готов обсудить детали в чате Kwork»

***

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
        text = finalize_response_text(text, project)
        text = self._postprocess_with_checks(project, user_payload, body, text)
        return finalize_response_text(text, project)

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
        issues += [f"tz:{v}" for v in payment_mismatch_issues(project, text)]
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
            if any("tz:payment_not_required" in i for i in issues):
                retry_payload["task"] += (
                    " В ТЗ без онлайн-оплаты — только заявки менеджеру. "
                    "Убери платёжные системы и оплату в боте."
                )
            if any(i.startswith("kwork:") for i in issues):
                retry_payload["task"] += " Убери внешние ссылки и GitHub из текста."
            body["messages"][1]["content"] = json.dumps(retry_payload, ensure_ascii=False)
            body["temperature"] = 0.9
            text = self._call_api(body, project.project_id)
            text = finalize_response_text(text, project)

        for attempt in range(2):
            qa = self._qa.validate(project, text)
            rule_issues = rule_check_alignment(project, text)
            all_issues = list(qa.get("issues") or []) + rule_issues
            all_issues += [f"kwork:{v}" for v in kwork_compliance_issues(text)]
            all_issues += [f"checklist:{v}" for v in buyer_checklist_issues(project, text)]
            all_issues += _topic_mismatch_issues(project, text)
            all_issues += [f"tz:{v}" for v in payment_mismatch_issues(project, text)]
            if (
                qa.get("aligned", True)
                and not rule_issues
                and not kwork_compliance_issues(text)
                and not buyer_checklist_issues(project, text)
                and not _topic_mismatch_issues(project, text)
                and not payment_mismatch_issues(project, text)
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
            text = finalize_response_text(text, project)

        return finalize_response_text(text, project)


_PARSE_HALLUCINATION_RE = re.compile(r"парс\w*|скрап\w*", re.I)
_BOT_BRIEF_RE = re.compile(r"telegram[- ]?бот|aiogram|телеграм[- ]?бот", re.I)


def _topic_mismatch_issues(project: ProjectFull, text: str) -> list[str]:
    brief = build_project_brief(project)
    if not brief or re.search(r"парс\w*|скрап\w*", brief, re.I):
        return []
    if _BOT_BRIEF_RE.search(brief) and _PARSE_HALLUCINATION_RE.search(text):
        return ["topic:парсинг_не_в_тз"]
    return []

