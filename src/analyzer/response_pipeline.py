"""Multi-agent selling response pipeline: DraftWriter → LogicCritic → ExpertReviewer."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.analyzer.gpt_scorer import _extract_json
from src.analyzer.project_brief import (
    build_project_brief,
    buyer_checklist_issues,
    extract_buyer_checklist,
    extract_buyer_questions,
    extract_tz_facts,
)
from src.analyzer.response_text import (
    finalize_response_text,
    kwork_compliance_issues,
    payment_mismatch_issues,
)
from src.config import Settings
from src.models import ProjectFull

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]
ProgressFn = Callable[[str], None]

MSG_DRAFT = "✍️ [1/3] Пишу продающий отклик…"
MSG_LOGIC = "🧠 [2/3] Проверяю логику и полноту…"
MSG_EXPERT = "🎓 [3/3] Экспертная рецензия…"
MSG_REVISE = "🔄 Доработка после рецензии (цикл {n}/2)…"
MSG_DONE = "✅ Отклик готов (прошёл ExpertReview)"
MSG_LIMIT = "⚠️ Отклик сдан после лимита циклов"

MAX_REVISION_CYCLES = 2

# Bare openers only — named «Ирина, здравствуйте!» is allowed (does not match ^).
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
    "готов выполнить",
    "буду рад сотрудничеству",
    "уважаемый заказчик",
    "обращайтесь",
    "понимаю, что основная задача заключается",
    "ознакомился с тз",
    "ознакомился с заданием",
    "изучил заказ",
    "изучил тз",
    "заинтересовал проект",
    "заинтересовал ваш проект",
    "наткнулся",
    "работаю более",
)

_NAMED_HELLO_RE = re.compile(
    r"^[А-ЯЁA-Z][\w\-]*(?:\s+[А-ЯЁA-Z][\w\-]*)?,\s*здравствуйте\s*[!.]?",
    re.I | re.U,
)

DRAFT_SYSTEM_PROMPT = """\
Ты — DraftWriter: пишешь продающий отклик от имени Александра Клычникова \
(Python / AI / Telegram / MVP) для биржи Kwork.

Это не массовая рассылка: текст под конкретный заказ.

*** АЛГОРИТМ ПРОДАЮЩЕГО ОТКЛИКА (СТРОГО) ***

0. Сначала определи, что заказчик ценит больше всего (скорость / цена / качество / \
автоматизация / надёжность / экспертиза и т.д.) — это база первой фразы.

1. Если в project.buyer есть имя — начни с приветствия: «Ирина, здравствуйте!» \
(подставь реальное имя). Если имени нет — НЕ выдумывай и НЕ пиши голое «Здравствуйте!».

2. Первая содержательная фраза = РЕШЕНИЕ / результат / выгода / понимание задачи. \
НИКОГДА про себя («изучил ТЗ», «ознакомился», «готов выполнить», «заинтересовал»).

3. Одно предложение — КАК ты решишь задачу (конкретный подход).

4. Одна полезная рекомендация — ТОЛЬКО если она реально полезна по ТЗ. Не выдумывай.

5. Срок — можно ориентировочно: «Срок — 5–7 дней.» (используй days_hint / default_days).

6. Цена — диапазон или «от»: «Стоимость — от 20 000 ₽.» \
(используй price_hint или desired_budget / max_budget проекта, если есть).

7. CTA: предложи обсудить: «Предлагаю обсудить детали и приступить.»

8. Если buyer_questions не пуст — ОБЯЗАТЕЛЬНО ответь на КАЖДЫЙ пункт явно \
(короткие ответы ок). Нельзя уходить в общие абзацы без покрытия пунктов списка.

*** ЗАПРЕЩЕНО НАВСЕГДА ***
- ознакомился с ТЗ / изучил заказ / заинтересовал проект / наткнулся
- готов выполнить / работаю более N лет / буду рад сотрудничеству / имею большой опыт
- массовые шаблонные открывашки без имени
- голое «Здравствуйте!» / «Добрый день» без имени заказчика
- markdown-списки, внешние URL / GitHub / портфолио-ссылки
- созвоны, мессенджеры, контакты вне Kwork
- игнорировать buyer_questions / отвечать в общих словах без пунктов

*** РАЗРЕШЕНО ***
- «Имя, здравствуйте!» если buyer известен

*** АНТИ-ШАБЛОН ***
Смотри recent_responses / recent_openings — НЕ повторяй те же первые фразы. \
Варьируй ритм и начало.

*** KWORK ***
Только чат площадки. Длина ~700–1600 знаков. Русский. Без markdown.

Если в feedback / critique / expert_notes есть замечания — учти их и перепиши.

Верни ТОЛЬКО текст отклика.
"""

LOGIC_CRITIC_PROMPT = """\
Ты — LogicCritic: проверяешь структуру, стиль и полноту продающего отклика.

Проверь:
1) Есть ли приветствие по имени ТОЛЬКО если buyer задан; нет голого «Здравствуйте!»
2) Первая содержательная фраза — про решение/выгоду/понимание, не про «изучил ТЗ»
3) Есть подход к решению
4) Срок и цена присутствуют (хотя бы ориентир)
5) Есть CTA обсудить
6) Нет запрещённых клише и нарушений Kwork (ссылки, созвоны, markdown-списки)
7) Длина разумная (~700–1600), текст по делу
8) ОБЯЗАТЕЛЬНО: если buyer_questions не пуст — у КАЖДОГО пункта есть явный ответ \
в тексте. Если хотя бы один пункт без ответа → verdict=fail и перечисли \
неотвеченные в missing. Нельзя ставить pass при неполном покрытии вопросов.

Верни СТРОГО JSON:
{
  "verdict": "pass" | "fail",
  "issues": ["..."],
  "missing": ["..."],
  "style_notes": "..."
}
"""

EXPERT_REVIEWER_PROMPT = """\
Ты — ExpertReviewer: финальный гейт качества продающего отклика на Kwork.

Оцени, выглядит ли текст как живой пресейл сильного специалиста, а не шаблон.
Учитывай critique (если есть) и соответствие ТЗ.

verdict:
- "pass" — можно сдавать
- "revise_draft" — вернуть DraftWriter с must_fix
- "revise_logic" — редко: структура ок, но LogicCritic пропустил важное; укажи в feedback

score: целое 1–10.

Верни СТРОГО JSON:
{
  "verdict": "pass" | "revise_draft" | "revise_logic",
  "score": 8,
  "feedback": "...",
  "must_fix": ["..."]
}
"""


def soft_banned_issues(text: str) -> list[str]:
    """Named «Имя, здравствуйте!» allowed; bare «Здравствуйте!» and clichés banned."""
    issues: list[str] = []
    stripped = text.strip()
    lower = stripped.lower()
    if not _NAMED_HELLO_RE.match(stripped):
        for pattern in _BANNED_OPENERS:
            if re.search(pattern, lower):
                issues.append(f"opener:{pattern}")
    for phrase in _BANNED_PHRASES:
        if phrase in lower:
            issues.append(f"phrase:{phrase}")
    return issues


def draft_too_short_for_questions(draft: str, questions: list[str]) -> bool:
    """Heuristic: many buyer questions but response clearly too short to cover them."""
    n = len(questions)
    if n <= 0:
        return False
    length = len((draft or "").strip())
    if n >= 5 and length < 400:
        return True
    min_len = int(80 * n * 0.35)
    return length < min_len


def force_logic_fail_for_questions(
    draft: str,
    project: ProjectFull,
    *,
    verdict: str,
    missing: list[str],
) -> list[str]:
    """Local hard-fails when draft cannot cover buyer_questions."""
    questions = extract_buyer_questions(project)
    forced: list[str] = []
    checklist_miss = buyer_checklist_issues(project, draft)
    for issue in checklist_miss:
        forced.append(issue)
    if (
        questions
        and verdict == "pass"
        and not missing
        and draft_too_short_for_questions(draft, questions)
    ):
        forced.append("too_short_for_all_questions")
    return forced


def _buyer_first_name(buyer: str | None) -> str | None:
    if not buyer:
        return None
    name = buyer.strip()
    if not name or name.lower() in {"неизвестно", "unknown", "-", "—"}:
        return None
    # drop hire-rate suffixes like "Ирина · 80%"
    name = re.split(r"[·|•,/]", name, maxsplit=1)[0].strip()
    token = name.split()[0].strip()
    if len(token) < 2 or not re.search(r"[А-ЯЁA-Z]", token, re.I):
        return None
    return token


def _price_from_project(project: ProjectFull) -> str | None:
    for raw in (project.desired_budget, project.max_budget):
        if not raw:
            continue
        digits = re.sub(r"\D", "", raw)
        if digits:
            return digits
    return None


class ResponsePipeline:
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

    def _openai_text(
        self,
        *,
        system: str,
        user: dict[str, Any],
        project_id: str,
        temperature: float = 0.75,
    ) -> str:
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(user, ensure_ascii=False),
                },
            ],
            "temperature": temperature,
        }
        return self._post_chat(body, project_id)

    def _openai_json(
        self,
        *,
        system: str,
        user: dict[str, Any],
        project_id: str,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        body = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(user, ensure_ascii=False),
                },
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        raw = self._post_chat(body, project_id)
        try:
            return _extract_json(raw)
        except Exception:
            logger.exception("response_pipeline_json_parse_failed project_id=%s", project_id)
            return {}

    def _post_chat(self, body: dict[str, Any], project_id: str) -> str:
        url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        client = self._get_client()
        response: httpx.Response | None = None
        for attempt in range(4):
            response = client.post(url, headers=headers, json=body)
            if response.status_code == 429 and attempt < 3:
                wait = 2 ** attempt
                logger.warning(
                    "response_pipeline rate limited, retry in %ss project_id=%s",
                    wait,
                    project_id,
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        if response is None:
            raise RuntimeError("response_pipeline: no response")
        return str(response.json()["choices"][0]["message"]["content"]).strip()

    def _build_draft_payload(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        recent_responses: Any = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
        feedback: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        buyer_name = _buyer_first_name(project.buyer)
        budget_digits = _price_from_project(project)
        return {
            "task": "Напиши продающий отклик по алгоритму DraftWriter.",
            "platform": project.platform,
            "project": project.model_dump(mode="json"),
            "buyer_name": buyer_name,
            "project_brief": build_project_brief(project),
            "tz_facts": extract_tz_facts(project),
            "buyer_checklist": extract_buyer_checklist(project),
            "buyer_questions": extract_buyer_questions(project),
            "lightrag_context": lightrag_context,
            "response_examples": examples,
            "recent_responses": recent_responses
            if recent_responses is not None
            else {"count": 0},
            "price_hint": price_hint or budget_digits,
            "days_hint": days_hint or self.settings.default_offer_days,
            "feedback": feedback,
        }

    def _draft(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        recent_responses: Any = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
        feedback: dict[str, Any] | str | None = None,
    ) -> str:
        payload = self._build_draft_payload(
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses,
            price_hint=price_hint,
            days_hint=days_hint,
            feedback=feedback,
        )
        logger.info("response_pipeline draft project_id=%s", project.project_id)
        text = self._openai_text(
            system=DRAFT_SYSTEM_PROMPT,
            user=payload,
            project_id=project.project_id,
            temperature=0.82,
        )
        text = finalize_response_text(text, project)
        banned = soft_banned_issues(text) + [
            f"kwork:{v}" for v in kwork_compliance_issues(text)
        ] + [
            f"checklist:{v}" for v in buyer_checklist_issues(project, text)
        ] + [f"tz:{v}" for v in payment_mismatch_issues(project, text)]
        if banned:
            logger.info(
                "response_pipeline draft soft_retry project_id=%s issues=%s",
                project.project_id,
                banned,
            )
            retry = dict(payload)
            retry["feedback"] = {
                "banned_detected": banned,
                "note": (
                    "Перепиши: убери клише. Имя+здравствуйте — только если buyer_name есть; "
                    "голое Здравствуйте запрещено. Без URL/созвонов."
                ),
            }
            text = self._openai_text(
                system=DRAFT_SYSTEM_PROMPT,
                user=retry,
                project_id=project.project_id,
                temperature=0.9,
            )
            text = finalize_response_text(text, project)
        return text

    def _critique_logic(self, draft: str, project: ProjectFull) -> dict[str, Any]:
        buyer_questions = extract_buyer_questions(project)
        payload = {
            "project_brief": build_project_brief(project),
            "buyer_name": _buyer_first_name(project.buyer),
            "buyer_questions": buyer_questions,
            "response_text": draft,
            "local_issues": soft_banned_issues(draft)
            + [f"kwork:{v}" for v in kwork_compliance_issues(draft)],
        }
        data = self._openai_json(
            system=LOGIC_CRITIC_PROMPT,
            user=payload,
            project_id=project.project_id,
            temperature=0.1,
        )
        verdict = str(data.get("verdict") or "fail").lower().strip()
        if verdict not in {"pass", "fail"}:
            verdict = "fail"
        result = {
            "verdict": verdict,
            "issues": list(data.get("issues") or []),
            "missing": list(data.get("missing") or []),
            "style_notes": str(data.get("style_notes") or ""),
        }
        if payload["local_issues"] and result["verdict"] == "pass":
            result["verdict"] = "fail"
            result["issues"] = list(result["issues"]) + list(payload["local_issues"])
        forced = force_logic_fail_for_questions(
            draft,
            project,
            verdict=str(result["verdict"]),
            missing=list(result["missing"]),
        )
        if forced:
            result["verdict"] = "fail"
            result["missing"] = list(result["missing"]) + [
                m for m in forced if m not in result["missing"]
            ]
            result["issues"] = list(result["issues"]) + [
                f"buyer_q:{m}" for m in forced if f"buyer_q:{m}" not in result["issues"]
            ]
        return result

    def _expert_review(
        self,
        draft: str,
        critique: dict[str, Any],
        project: ProjectFull,
    ) -> dict[str, Any]:
        payload = {
            "project_brief": build_project_brief(project),
            "buyer_name": _buyer_first_name(project.buyer),
            "response_text": draft,
            "critique": critique,
        }
        data = self._openai_json(
            system=EXPERT_REVIEWER_PROMPT,
            user=payload,
            project_id=project.project_id,
        )
        verdict = str(data.get("verdict") or "revise_draft").lower().strip()
        if verdict not in {"pass", "revise_draft", "revise_logic"}:
            verdict = "revise_draft"
        try:
            score = int(data.get("score") or 5)
        except (TypeError, ValueError):
            score = 5
        score = max(1, min(10, score))
        return {
            "verdict": verdict,
            "score": score,
            "feedback": str(data.get("feedback") or ""),
            "must_fix": list(data.get("must_fix") or []),
        }

    def generate(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        recent_responses: Any = None,
        progress: ProgressFn | None = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
    ) -> str:
        def _notify(msg: str) -> None:
            if progress is not None:
                progress(msg)

        async def _run() -> str:
            async def notify(msg: str) -> None:
                _notify(msg)

            return await self.generate_with_progress(
                project,
                lightrag_context,
                notify=notify,
                examples=examples,
                recent_responses=recent_responses,
                price_hint=price_hint,
                days_hint=days_hint,
                threaded=False,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        # Already in async loop — run sync stage loop without nesting asyncio.run
        return self._generate_sync(
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses,
            progress=progress,
            price_hint=price_hint,
            days_hint=days_hint,
        )

    @staticmethod
    def _safe_progress(progress: ProgressFn | None, msg: str) -> None:
        if progress is None:
            return
        try:
            progress(msg)
        except Exception:
            logger.warning("response_pipeline progress notify failed", exc_info=True)

    @staticmethod
    async def _safe_notify(notify: NotifyFn, msg: str) -> None:
        try:
            await notify(msg)
        except Exception:
            logger.warning("response_pipeline TG notify failed", exc_info=True)

    def _generate_sync(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        recent_responses: Any = None,
        progress: ProgressFn | None = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
    ) -> str:
        def notify(msg: str) -> None:
            self._safe_progress(progress, msg)

        notify(MSG_DRAFT)
        draft = self._draft(
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses,
            price_hint=price_hint,
            days_hint=days_hint,
        )
        best = draft
        best_score = 0

        for cycle in range(1, MAX_REVISION_CYCLES + 1):
            notify(MSG_LOGIC)
            critique = self._critique_logic(draft, project)
            if critique.get("verdict") != "pass":
                notify(MSG_REVISE.format(n=cycle))
                draft = self._draft(
                    project,
                    lightrag_context,
                    examples=examples,
                    recent_responses=recent_responses,
                    price_hint=price_hint,
                    days_hint=days_hint,
                    feedback={"role": "LogicCritic", **critique},
                )
                continue

            notify(MSG_EXPERT)
            expert = self._expert_review(draft, critique, project)
            score = int(expert.get("score") or 0)
            if score >= best_score:
                best_score = score
                best = draft

            if expert.get("verdict") == "pass":
                notify(MSG_DONE)
                return finalize_response_text(draft, project)

            notify(MSG_REVISE.format(n=cycle))
            draft = self._draft(
                project,
                lightrag_context,
                examples=examples,
                recent_responses=recent_responses,
                price_hint=price_hint,
                days_hint=days_hint,
                feedback={"role": "ExpertReviewer", **expert},
            )

        notify(MSG_LIMIT)
        return finalize_response_text(best, project)

    async def generate_with_progress(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        notify: NotifyFn,
        examples: str = "",
        recent_responses: Any = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
        threaded: bool = True,
    ) -> str:
        async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
            if threaded:
                return await asyncio.to_thread(fn, *args, **kwargs)
            return fn(*args, **kwargs)

        await self._safe_notify(notify, MSG_DRAFT)
        draft = await _call(
            self._draft,
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses,
            price_hint=price_hint,
            days_hint=days_hint,
        )
        best = draft
        best_score = 0

        for cycle in range(1, MAX_REVISION_CYCLES + 1):
            await self._safe_notify(notify, MSG_LOGIC)
            critique = await _call(self._critique_logic, draft, project)
            if critique.get("verdict") != "pass":
                await self._safe_notify(notify, MSG_REVISE.format(n=cycle))
                draft = await _call(
                    self._draft,
                    project,
                    lightrag_context,
                    examples=examples,
                    recent_responses=recent_responses,
                    price_hint=price_hint,
                    days_hint=days_hint,
                    feedback={"role": "LogicCritic", **critique},
                )
                continue

            await self._safe_notify(notify, MSG_EXPERT)
            expert = await _call(self._expert_review, draft, critique, project)
            score = int(expert.get("score") or 0)
            if score >= best_score:
                best_score = score
                best = draft

            if expert.get("verdict") == "pass":
                await self._safe_notify(notify, MSG_DONE)
                return finalize_response_text(draft, project)

            await self._safe_notify(notify, MSG_REVISE.format(n=cycle))
            draft = await _call(
                self._draft,
                project,
                lightrag_context,
                examples=examples,
                recent_responses=recent_responses,
                price_hint=price_hint,
                days_hint=days_hint,
                feedback={"role": "ExpertReviewer", **expert},
            )

        await self._safe_notify(notify, MSG_LIMIT)
        return finalize_response_text(best, project)
