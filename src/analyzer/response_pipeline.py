"""Multi-agent selling response pipeline: DraftWriter → LogicCritic → ExpertReviewer."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Generator
from typing import Any

import httpx

from src.adapters.kwork_pricing import budget_mismatch_issues, ensure_budget_mismatch_note
from src.analyzer.gpt_scorer import _extract_json
from src.analyzer.openai_compat import post_chat
from src.analyzer.project_brief import (
    build_project_brief,
    buyer_checklist_issues,
    checklist_rule_for_question,
    extract_buyer_checklist,
    extract_buyer_questions,
    extract_tz_facts,
)
from src.analyzer.response_text import (
    buyer_first_name as _buyer_first_name,
    finalize_response_text,
    kwork_compliance_issues,
    payment_mismatch_issues,
)
from src.config import Settings
from src.evidence.usage import compact_verified_evidence, evidence_usage_issues
from src.models import ProjectFull

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Awaitable[None]]
ProgressFn = Callable[[str], None]

MSG_DRAFT = "✍️ [1/3] Пишу продающий отклик…"
MSG_LOGIC = "🧠 [2/3] Проверяю логику и полноту…"
MSG_EXPERT = "🎓 [3/3] Экспертная рецензия…"
MSG_REVISE = "🔄 Доработка после рецензии (цикл {n}/{total})…"
MSG_DONE = "✅ Отклик готов (прошёл ExpertReview)"
MSG_LIMIT = "⚠️ Сдан лучший вариант (циклов: {cycles}). Осталось: {issues}"

LIMIT_ISSUES_SHOWN = 3
LIMIT_ISSUES_MAX_CHARS = 120

REVISE_SYSTEM_PROMPT = """\
Ты правишь готовый отклик по инструкциям фрилансера. \
Абзацы разделяй РЕАЛЬНЫМИ переносами строк, никогда не пиши символы \\n или \\r как текст. \
Красная строка = 4 пробела в начале абзаца (кроме первого, если так уже принято). \
Не добавляй markdown. Верни ТОЛЬКО текст отклика."""

MAX_REVISION_CYCLES = 2

_STEP_NOTIFY = "notify"
_STEP_CALL = "call"

_PLATFORM_POLICIES: dict[str, dict[str, Any]] = {
    "kwork": {
        "policy_id": "kwork",
        "required_rules": [
            "Начало строго с «Здравствуйте!»",
            "Kwork-only: без внешних контактов/ссылок/созвонов",
            "Учитывать kwork-compliance проверки",
        ],
        "quality_rules": [
            "Ясно и по делу",
            "Срок и цена цифрами",
            "Без воды и выдуманных технологий",
            "Ответить на buyer_questions",
        ],
    },
    "default": {
        "policy_id": "default",
        "required_rules": [
            "Без platform-specific обязательств Kwork",
        ],
        "quality_rules": [
            "Ясно и по делу",
            "Срок и цена цифрами",
            "Без воды и выдуманных технологий",
            "Ответить на buyer_questions",
        ],
    },
    "yandex_uslugi": {
        "policy_id": "yandex_uslugi",
        "max_chars": 1000,
        "required_rules": [
            "Жёсткий лимит 1000 символов включая пробелы и переносы",
            "Не обрезать чеклист — укоротить воду",
        ],
        "quality_rules": [
            "Ясно и по делу",
            "Срок и цена цифрами",
            "Что входит и расходы после запуска — явно",
            "Без воды и выдуманных технологий",
            "Ответить на buyer_questions",
        ],
    },
}

_QUESTION_TOKEN_STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "по",
    "с",
    "со",
    "к",
    "ко",
    "о",
    "об",
    "от",
    "до",
    "из",
    "за",
    "для",
    "ли",
    "а",
    "или",
    "что",
    "как",
    "какой",
    "какие",
    "какая",
    "каком",
    "какую",
    "когда",
    "будет",
    "будут",
    "нужно",
    "нужен",
    "нужна",
    "нужны",
    "where",
    "what",
    "how",
    "is",
    "are",
    "the",
    "a",
    "an",
}

# Bare openers — «Здравствуйте!» required; other greetings banned.
_BANNED_OPENERS = (
    r"^добрый день",
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
    "понимаю, что вам",
    "понимаю что вам",
    "понимаю вашу задачу",
    "понимаю вашу потребность",
    "понимаю, что основная задача заключается",
    "ознакомился с тз",
    "ознакомился с заданием",
    "изучил заказ",
    "изучил тз",
    "заинтересовал проект",
    "заинтересовал ваш проект",
    "наткнулся",
    "работаю более",
    "предлагаю обсудить детали и приступить",
    "задача понятна",
    "я занимаюсь",
    "я специализируюсь",
    "по договоренности",
    "по договорённости",
    "обсудим стоимость",
    "если интересно — пишите",
    "если интересно-пишите",
    "когда удобно созвониться",
)

_NAMED_HELLO_RE = re.compile(
    r"^[А-ЯЁA-Z][\w\-]*(?:\s+[А-ЯЁA-Z][\w\-]*)?,\s*здравствуйте\s*[!.]?",
    re.I | re.U,
)


def _normalize_platform(platform: str | None) -> str:
    value = (platform or "").strip().lower()
    return value if value in _PLATFORM_POLICIES else "default"


def _platform_policy(platform: str | None) -> dict[str, Any]:
    return _PLATFORM_POLICIES[_normalize_platform(platform)]


def _normalize_match_text(value: str) -> str:
    low = (value or "").lower()
    low = re.sub(r"[^\w\s]", " ", low, flags=re.U)
    return re.sub(r"\s+", " ", low, flags=re.U).strip()


def _question_keywords(question: str) -> set[str]:
    normalized = _normalize_match_text(question)
    tokens = {
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in _QUESTION_TOKEN_STOPWORDS
    }
    return tokens


def _question_numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text or ""))


def _is_question_covered(question: str, response_text: str) -> bool:
    q_norm = _normalize_match_text(question)
    r_norm = _normalize_match_text(response_text)
    if not q_norm:
        return True
    q_nums = _question_numbers(q_norm)
    if q_nums and not q_nums.issubset(_question_numbers(r_norm)):
        return False
    q_tokens = _question_keywords(q_norm)
    if not q_tokens:
        return q_norm in r_norm
    r_tokens = _question_keywords(r_norm)
    overlap = q_tokens & r_tokens
    required_overlap = 1 if len(q_tokens) <= 2 else 2
    return len(overlap) >= required_overlap


def _uncovered_buyer_questions(
    draft: str, questions: list[str]
) -> list[str]:
    missing: list[str] = []
    for idx, question in enumerate(questions, start=1):
        if not _is_question_covered(question, draft):
            missing.append(f"uncovered:{idx}")
    return missing

DRAFT_SYSTEM_PROMPT = """\
Ты — DraftWriter: эксперт по продающим откликам на биржах фриланса. \
Пишешь от имени Александра Клычникова (Python / AI / Telegram / MVP) для Kwork.

Главная цель: за 10–15 секунд заказчик думает \
«Этот человек уже понял задачу и знает, как её решить.» \
Не продавай себя — продавай решение проблемы клиента.

Это не массовая рассылка: текст только под этот заказ.

*** ПЕРЕД НАПИСАНИЕМ ***
Проанализируй заказ и определи: что хочет получить; главную боль; \
что важнее всего (скорость / стоимость / качество / автоматизация / простота / \
надёжность / масштабируемость / удобство / интеграция / безопасность / \
экономия времени / другое). Эта мысль — основа первого предложения.

*** АЛГОРИТМ (СТРОГО) ***

0. Ценность заказчика (см. выше) → база первой фразы.

1. Обращение: ВСЕГДА начинай с «Здравствуйте!» — это первое слово отклика. \
Без имени заказчика, без «Добрый день». Не выдумывай имена.

2. Первое предложение — самое важное. Сразу: решение / результат / выгода / \
экспертная мысль по ЭТОМУ заказу. Чередуй способы (не один шаблон):
- через решение: «Настрою …, который …»
- через результат: «После запуска … сможет …»
- через выгоду: «Здесь ключевое — сократить …»
- через экспертную мысль: «В таких проектах важнее …, чем просто …»
НЕ через парафраз ТЗ («Понимаю, что вам…»). \
Глагол действия ротируй; НЕ дефолт «Соберу». Если recent_openings с «Соберу» — другой глагол.

3. Одно предложение — КАК решишь задачу. Не пересказывай ТЗ. Покажи понимание.

4. Экспертная рекомендация — ОДНА короткая, только если реально полезна. \
Иначе пропусти. Не выдумывай ради шаблона.

5. Срок — всегда, хотя бы ориентир: «Срок — 5–7 дней.» (days_hint / default_days). \
Если days_hint задан — используй его (одно число или узкий диапазон ±1 день), \
не раздувай срок из‑за объёма каталога.

6. Стоимость — всегда: «от … ₽» или диапазон. ЗАПРЕЩЕНО: «по договорённости», \
«обсудим стоимость» без цифры. (price_hint / бюджет проекта.) Цена = price_hint \
в коридоре заказа (желаемый), не рыночный fair и не допустимый максимум.

7. CTA — мягкий следующий шаг. ЗАПРЕЩЕНО дословно: \
«Предлагаю обсудить детали и приступить» (+ «…к работе»). Варианты (ротируй):
- «Напишите, с чего удобнее начать — уточню срок и стоимость.»
- «Готов разобрать объём по вашему ТЗ и зафиксировать этапы.»
- «Если подход ок — напишите, согласуем старт.»
- «Могу сначала набросать план работ под вашу базу/сценарии.»
- «Дайте знать удобный следующий шаг — уточню оценку.»

8. Если buyer_questions не пуст — ответь на КАЖДЫЙ пункт явно.

9. budget_mismatch: цена = price_hint / коридор заказа. Одной короткой фразой — \
что входит в эту сумму (MVP / основной сценарий). Полный объём — только как \
опциональное расширение (ориентир fair_price), не как цена отклика. \
ЗАПРЕЩЕНО: «бюджет занижен», «обсудить сумму» как главный CTA, цена = только fair.

*** ЯЗЫК И ФОРМАТ ***
- 5–7 предложений. Простой человеческий язык. Без воды, биографии, длинных вступлений.
- Абзацы: новый смысловой блок — с новой строки (реальный перенос, НЕ символы \\n в тексте), \
БЕЗ пустой строки. Каждый абзац начинай с красной строки (4 пробела в начале). \
Не сваливай весь текст в одну простыню.
- Каждое предложение помогает получить заказ.
- Используй слова заказчика из ТЗ (Telegram, WordPress, MAX, ChatGPT, amoCRM и т.д.).
- Не придумывай технологии, которых нет в заказе.
- Не перечисляй стек без необходимости.
- Не задавай больше одного уточняющего вопроса.
- Не рассказывай о себе и не перечисляй опыт списком.
- Похожий кейс — максимум одна короткая фраза, только если уместно.

*** ЗАПРЕЩЕНО НАВСЕГДА ***
- ознакомился с ТЗ / изучил заказ / наткнулся / заинтересовал проект / задача понятна
- готов выполнить / работаю более N лет / буду рад сотрудничеству / имею большой опыт
- я занимаюсь… / я специализируюсь…
- «Понимаю, что вам…» / любая «Понимаю, что…» после hello
- голое «Добрый день» / «Приветствую» вместо «Здравствуйте!»
- отклик без «Здравствуйте!» в начале
- markdown-списки, URL / GitHub / портфолио-ссылки
- созвоны, мессенджеры, контакты вне Kwork
- «если интересно — пишите» / «когда удобно созвониться?»
- игнор buyer_questions; цена = только fair при budget_mismatch; «бюджет занижен»

*** АНТИ-ШАБЛОН ***
Смотри recent_responses / recent_openings / recent_closings. \
Не повторяй первые/последние фразы. Не открывай снова с «Соберу». \
Не заканчивай «Предлагаю обсудить детали и приступить.» \
Не штампуй одно и то же «Сделаю…» / «Разработаю…» / «Для такого проекта важно…» \
в каждом отклике. Живой текст, не шаблон нейросети.

*** VERIFIED EVIDENCE (если verified_evidence в payload) ***
Если есть verified_evidence:
- первое содержательное предложение — наблюдение/отличие ЭТОГО заказа по фактам; \
НЕ начинай с Реализую/Создам/Сделаю/Разработаю/Соберу
- в первых 3 предложениях: ≥1 fact (claim), ≥1 insight.implementation_consequence, \
≥2 разных anchors из verified_evidence
- не утверждай, что открыл/проверил/изучил источник со status≠verified
- не выдумывай API endpoints вне evidence
- CTA — из реального пробела (missing param), если виден в evidence/ТЗ
- без внешних http(s) URL в тексте

*** KWORK ***
Только чат площадки. ~700–1600 знаков ≈ 5–7 предложений. Русский. Без markdown.

Если в feedback / critique / expert_notes есть замечания — учти и перепиши.
Верни ТОЛЬКО текст отклика.
"""

LOGIC_CRITIC_PROMPT = """\
Ты — LogicCritic: структура, стиль и полнота продающего отклика.

Чеклист (fail → issues / missing):
1) Текст начинается с «Здравствуйте!» (после отступа абзаца, если есть). \
Fail: другое приветствие или нет «Здравствуйте!» в начале. Fail: «Имя, здравствуйте!»
2) Первое содержательное предложение цепляет: решение/результат/выгода/экспертная мысль; \
НЕ парафраз ТЗ; НЕ «Понимаю, что…». \
Fail если первый глагол «Соберу» (или снова «Соберу» при recent_openings с «Соберу»).
3) Есть короткое «как решим» без пересказа ТЗ
4) Срок есть (ориентир days_hint); цена есть цифрой («от»/диапазон, ориентир \
price_hint). Fail: «по договорённости», «обсудим стоимость» без суммы
5) CTA есть. Fail: «Предлагаю обсудить детали и приступить» (+ «к работе»)
6) Нет клише/био/перечня опыта; нет стопки стека без нужды; ≤1 уточняющий вопрос
7) Нет нарушений Kwork (ссылки, созвоны, markdown-списки)
8) Длина ~5–7 предложений / ~700–1600 знаков, по делу
9) buyer_questions: у каждого пункта явный ответ, иначе fail + missing
10) budget_mismatch: цена = price_hint / коридор заказа; коротко что входит в сумму \
(основной сценарий); полный объём только как опция. Fail: «бюджет занижен»; \
fail: только fair как цена отклика
11) Используются ключевые слова из ТЗ (если в заказе Telegram/WordPress/… — они в тексте, \
если уместно); нет выдуманных технологий вне заказа
12) Если в payload есть verified_evidence: fail при generic opener \
(Реализую/Создам/Сделаю/Разработаю/Соберу, в т.ч. «Я …»); fail без ≥2 anchors \
(или всех, если их меньше); fail при «открыл/проверил …» для status≠verified; \
fail при внешних http(s) URL. Учитывай local_issues с префиксом evidence:

Верни СТРОГО JSON:
{
  "verdict": "pass" | "fail",
  "issues": ["..."],
  "missing": ["..."],
  "style_notes": "..."
}
"""

EXPERT_REVIEWER_PROMPT = """\
Ты — ExpertReviewer: финальный гейт. Текст должен читаться как живой пресейл, \
не как шаблон GPT.

Тест 10–15 секунд: заказчик думает \
«Этот исполнитель уже знает, как решить мою задачу»? Если нет → revise_draft.

Учитывай critique, соответствие tz_facts и явные ответы на buyer_questions.

Авто-revise_draft (must_fix):
- «Понимаю, что…» / парафраз ТЗ вместо результата
- opener «Соберу…» (особенно vs recent_openings)
- CTA «Предлагаю обсудить детали и приступить» (+ «к работе»)
- рассказ о себе / список опыта / стопка стека без нужды
- цена без цифры («по договорённости»)
- выдуманные технологии не из заказа
- шаблонность vs recent_openings/closings
- budget_mismatch без listed/price_hint; «бюджет занижен»; fair как единственная цена
- verified_evidence в payload, но opener Реализую/Создам/Сделаю/Разработаю/Соберу \
(в т.ч. «Я …»); нет ≥2 anchors; осмотр источника status≠verified; внешний URL

verdict:
- "pass" — можно сдавать
- "revise_draft" — вернуть DraftWriter с must_fix
- "revise_logic" — редко: структура ок, LogicCritic пропустил важное

score: целое 1–10.

Верни СТРОГО JSON:
{
  "verdict": "pass" | "revise_draft" | "revise_logic",
  "score": 8,
  "feedback": "...",
  "must_fix": ["..."]
}
"""

DEFAULT_DRAFT_SYSTEM_PROMPT = """\
Ты — DraftWriter: эксперт по продающим откликам на биржах фриланса. \
Пишешь от имени Александра Клычникова (Python / AI / Telegram / MVP).

Главная цель: за 10–15 секунд заказчик думает \
«Этот человек уже понял задачу и знает, как её решить.» \
Не продавай себя — продавай решение проблемы клиента.

*** АЛГОРИТМ (СТРОГО) ***

1. Обращение: ВСЕГДА начинай с «Здравствуйте!» — это первое слово отклика. \
Без имени заказчика, без «Добрый день».

2. Первое предложение — результат/решение словами из ТЗ. Сразу конкретные \
сущности заказа (графики, QR, роли, табель — что есть в ТЗ). \
ЗАПРЕЩЕНО: парафраз «Предлагаю разработать …, которая автоматизирует…»; \
«Понимаю, что вам…»; вода без слов заказчика.

3. Одно короткое КАК: этапы (этап 1 / этап 2), не пересказ всего ТЗ.

4. Срок — всегда цифрой (дни). Стоимость — всегда цифрой (₽).

5. Если buyer_questions не пуст — ответь на КАЖДЫЙ пункт явно: \
стоимость, срок, что входит в стоимость, расходы после запуска.

6. Что ВХОДИТ в цену — конкретно (этап, установка/деплой, инструкция, \
исходники, блоки ТЗ). ЗАПРЕЩЕНО откупаться «основные функции».

7. Обязательные расходы после запуска — с цифрами (хостинг/VPS/домен, ₽/мес). \
ЗАПРЕЩЕНО: «хостинг можно обсудить» / «можно обсудить» без суммы.

8. CTA — мягкий следующий шаг.

*** ЯЗЫК И ФОРМАТ ***
- 4–7 предложений. Живой деловой язык. Без воды, биографии, саморекламы.
- Без markdown. Верни только текст отклика.
- Не выдумывай технологии и факты вне заказа.
- Используй слова заказчика из ТЗ.
- Если platform_policy.max_chars задан (Яндекс Услуги = 1000) — весь текст \
строго не длиннее этого лимита. Чеклист не режь, режь воду.

*** ЗАПРЕЩЕНО ***
- «основные функции» как единственное содержание цены
- «можно обсудить» без цифр про хостинг/расходы после запуска
- парафраз ТЗ вместо результата
- игнор buyer_questions
- цена или срок без цифры

*** VERIFIED EVIDENCE (если verified_evidence в payload) ***
Если есть verified_evidence:
- первое содержательное предложение — наблюдение/отличие ЭТОГО заказа по фактам; \
НЕ начинай с Реализую/Создам/Сделаю/Разработаю/Соберу
- в первых 3 предложениях: ≥1 fact (claim), ≥1 insight.implementation_consequence, \
≥2 разных anchors из verified_evidence
- не утверждай, что открыл/проверил/изучил источник со status≠verified
- не выдумывай API endpoints вне evidence
- CTA — из реального пробела (missing param), если виден в evidence/ТЗ
- без внешних http(s) URL в тексте

Если в feedback / critique / expert_notes есть замечания — учти и перепиши.
Верни ТОЛЬКО текст отклика.
"""

DEFAULT_LOGIC_CRITIC_PROMPT = """\
Ты — LogicCritic: структура, стиль и полнота продающего отклика.

Чеклист (fail → issues / missing):
1) Текст начинается с «Здравствуйте!». Fail: другое приветствие или нет hello.
2) Первое содержательное предложение — результат/решение словами ТЗ; \
НЕ парафраз «Предлагаю разработать … которая автоматизирует»; НЕ «Понимаю, что…».
3) Короткое КАК через этапы, без пересказа ТЗ.
4) Срок есть цифрой; цена есть цифрой. Fail: «по договорённости» без суммы.
5) buyer_questions: у каждого пункта явный ответ (стоимость / срок / \
что входит / расходы после запуска), иначе fail + missing.
6) Что входит в цену — конкретно, не только «основные функции».
7) Расходы после запуска — с цифрой (хостинг/VPS/домен, ₽/мес). \
Fail: «хостинг обсудим» без числа.
8) Нет воды, клише и выдуманных технологий. 4–7 предложений, без markdown.
9) Если platform_policy.max_chars задан — fail при превышении (Яндекс: 1000).
10) Если в payload есть verified_evidence: fail при generic opener \
(Реализую/Создам/Сделаю/Разработаю/Соберу, в т.ч. «Я …»); fail без ≥2 anchors \
(или всех, если их меньше); fail при осмотре источника status≠verified; \
fail при внешних http(s) URL. Учитывай local_issues с префиксом evidence:

Верни СТРОГО JSON:
{
  "verdict": "pass" | "fail",
  "issues": ["..."],
  "missing": ["..."],
  "style_notes": "..."
}
"""

DEFAULT_EXPERT_REVIEWER_PROMPT = """\
Ты — ExpertReviewer: финальный гейт. Текст должен читаться как живой пресейл, \
не как шаблон GPT.

Тест 10–15 секунд: заказчик думает \
«Этот исполнитель уже знает, как решить мою задачу»? Если нет → revise_draft.

Авто-revise_draft (must_fix):
- «Предлагаю разработать … которая автоматизирует» / парафраз ТЗ вместо результата
- нет «Здравствуйте!» в начале
- нет срока или цены цифрами
- buyer_questions не закрыты по пунктам (стоимость / срок / что входит / расходы)
- «основные функции» без конкретного состава цены
- «можно обсудить» про хостинг/расходы без цифры
- выдуманные технологии не из заказа
- длина > platform_policy.max_chars (Яндекс Услуги: 1000)
- вода и шаблонность
- verified_evidence в payload, но opener Реализую/Создам/Сделаю/Разработаю/Соберу \
(в т.ч. «Я …»); нет ≥2 anchors; осмотр источника status≠verified; внешний URL

verdict:
- "pass" — можно сдавать
- "revise_draft" — вернуть DraftWriter с must_fix
- "revise_logic" — редко: структура ок, LogicCritic пропустил важное

score: целое 1–10.

Верни СТРОГО JSON:
{
  "verdict": "pass" | "revise_draft" | "revise_logic",
  "score": 8,
  "feedback": "...",
  "must_fix": ["..."]
}
"""


def _prompts_for_platform(platform: str | None) -> dict[str, str]:
    normalized = _normalize_platform(platform)
    if normalized == "kwork":
        return {
            "draft": DRAFT_SYSTEM_PROMPT,
            "logic": LOGIC_CRITIC_PROMPT,
            "expert": EXPERT_REVIEWER_PROMPT,
        }
    return {
        "draft": DEFAULT_DRAFT_SYSTEM_PROMPT,
        "logic": DEFAULT_LOGIC_CRITIC_PROMPT,
        "expert": DEFAULT_EXPERT_REVIEWER_PROMPT,
    }


def _response_opener(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    return raw.split("\n", 1)[0].strip()


def _content_after_greeting(text: str) -> str:
    """Strip «Здравствуйте!» / «Имя, здравствуйте!» so opener checks see content verb."""
    stripped = _response_opener(text)
    match = _NAMED_HELLO_RE.match(stripped)
    if match:
        return stripped[match.end() :].lstrip()
    if re.match(r"^здравствуйте", stripped, re.I):
        return re.sub(r"^здравствуйте\s*[!.,]?\s*", "", stripped, count=1, flags=re.I)
    return stripped


def _missing_hello_issue(text: str) -> str | None:
    body = _response_opener(text)
    if re.match(r"^здравствуйте", body, re.I):
        return None
    if _NAMED_HELLO_RE.match(body):
        return "opener:named_hello_instead_of_bare"
    return "opener:missing_hello"


def soft_banned_issues(text: str) -> list[str]:
    """«Здравствуйте!» required at start; clichés banned."""
    issues: list[str] = []
    opener = _response_opener(text)
    hello_issue = _missing_hello_issue(text)
    if hello_issue:
        issues.append(hello_issue)
    if not _NAMED_HELLO_RE.match(opener):
        for pattern in _BANNED_OPENERS:
            if re.search(pattern, opener.lower()):
                issues.append(f"opener:{pattern}")
    body = _content_after_greeting(text)
    if body.lower().startswith("соберу"):
        issues.append("opener:^соберу")
    lower_full = (text or "").lower()
    for phrase in _BANNED_PHRASES:
        if phrase in lower_full:
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
    max_chars = _platform_policy(project.platform).get("max_chars")
    if isinstance(max_chars, int) and len((draft or "").strip()) > max_chars:
        forced.append("too_long")
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
    for uncovered in _uncovered_buyer_questions(draft, questions):
        idx = int(uncovered.split(":", 1)[1])
        if 1 <= idx <= len(questions) and checklist_rule_for_question(questions[idx - 1]):
            continue
        forced.append(uncovered)
    return forced


def _price_from_project(project: ProjectFull) -> str | None:
    for raw in (project.desired_budget, project.max_budget):
        if not raw:
            continue
        digits = re.sub(r"\D", "", raw)
        if digits:
            return digits
    return None


def format_limit_message(cycles: int, issues: list[str]) -> str:
    """MSG_LIMIT with the leftover issues, HTML-escaped for Telegram."""
    joined = ", ".join(issues[:LIMIT_ISSUES_SHOWN])
    if len(joined) > LIMIT_ISSUES_MAX_CHARS:
        joined = joined[: LIMIT_ISSUES_MAX_CHARS - 1].rstrip(" ,") + "…"
    return MSG_LIMIT.format(
        cycles=cycles,
        issues=html.escape(joined) if joined else "нет замечаний",
    )


def _issue_signature(*groups: list[str]) -> frozenset[str]:
    return frozenset(
        _normalize_match_text(str(item))
        for group in groups
        for item in group
        if str(item).strip()
    )


def _pick_best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Fewest local issues wins; then highest expert score; then the latest draft."""
    return max(
        enumerate(candidates),
        key=lambda pair: (
            -len(pair[1]["local_issues"]),
            int(pair[1]["expert_score"] or 0),
            pair[0],
        ),
    )[1]


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
        self._last_evidence: Any | None = None

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
        model: str | None = None,
    ) -> str:
        body = {
            "model": model or self.settings.openai_model,
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
        model: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "model": model or self.settings.openai_model,
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
            response = post_chat(client, url, headers=headers, body=body)
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
        budget_mismatch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        buyer_name = _buyer_first_name(project.buyer)
        budget_digits = _price_from_project(project)
        platform = _normalize_platform(project.platform)
        policy = _platform_policy(platform)
        payload: dict[str, Any] = {
            "task": "Напиши продающий отклик по алгоритму DraftWriter.",
            "platform": platform,
            "platform_policy": policy,
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
        if budget_mismatch:
            payload["budget_mismatch"] = budget_mismatch
        verified = compact_verified_evidence(self._last_evidence)
        if verified is not None:
            payload["verified_evidence"] = verified
        return payload

    def _local_issues(
        self,
        draft: str,
        project: ProjectFull,
        budget_mismatch: dict[str, Any] | None = None,
    ) -> list[str]:
        """Deterministic issues of a draft — single source for draft/critique/cycle."""
        platform = _normalize_platform(project.platform)
        issues: list[str] = []
        if platform == "kwork":
            issues += soft_banned_issues(draft)
            issues += [f"kwork:{v}" for v in kwork_compliance_issues(draft)]
        issues += list(buyer_checklist_issues(project, draft))
        issues += list(payment_mismatch_issues(project, draft))
        issues += budget_mismatch_issues(draft, budget_mismatch)
        issues += evidence_usage_issues(draft, self._last_evidence)
        return issues

    def _repair_local_issues(
        self,
        draft: str,
        project: ProjectFull,
        budget_mismatch: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """Fix mechanically repairable issues before spending a revision cycle."""
        issues = self._local_issues(draft, project, budget_mismatch)
        if "budget_mismatch:no_scope_note" not in issues:
            return draft, issues
        repaired = finalize_response_text(
            ensure_budget_mismatch_note(draft, budget_mismatch), project
        )
        if repaired == draft:
            return draft, issues
        logger.info(
            "response_pipeline deterministic_repair project_id=%s applied=budget_scope_note",
            project.project_id,
        )
        return repaired, self._local_issues(repaired, project, budget_mismatch)

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
        budget_mismatch: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        platform = _normalize_platform(project.platform)
        prompts = _prompts_for_platform(platform)
        payload = self._build_draft_payload(
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses,
            price_hint=price_hint,
            days_hint=days_hint,
            feedback=feedback,
            budget_mismatch=budget_mismatch,
        )
        model = model or self.settings.model_for("draft")
        logger.info(
            "response_pipeline draft project_id=%s model=%s",
            project.project_id,
            model,
        )
        text = self._openai_text(
            system=prompts["draft"],
            user=payload,
            project_id=project.project_id,
            temperature=0.82,
            model=model,
        )
        text = finalize_response_text(text, project)
        local = self._local_issues(text, project, budget_mismatch)
        evidence_issues = [i for i in local if i.startswith("evidence:")]
        banned = [i for i in local if not i.startswith("evidence:")]
        if banned or evidence_issues:
            logger.info(
                "response_pipeline draft soft_retry project_id=%s banned=%s evidence=%s",
                project.project_id,
                banned,
                evidence_issues,
            )
            retry = dict(payload)
            note = (
                "Перепиши: убери клише. Начни с «Здравствуйте!». "
                "Не открывай с «Соберу». "
                "Не заканчивай «Предлагаю обсудить детали и приступить.» "
                "Без URL/созвонов."
            )
            if budget_mismatch:
                note += (
                    " При budget_mismatch: цена = price_hint / бюджет заказа; "
                    "одной фразой что входит в сумму (основной сценарий). "
                    "Полный объём — только как опция расширения. "
                    "Не пиши «бюджет занижен» и не ставь рыночную цену как единственную."
                )
            if evidence_issues:
                note += (
                    " Есть verified_evidence: первое содержательное предложение — "
                    "наблюдение/отличие по фактам этого заказа (не Реализую/Создам/"
                    "Сделаю/Разработаю/Соберу); вставь ≥2 anchors и следствие из insight; "
                    "не утверждай осмотр источников со status≠verified."
                )
            feedback_payload: dict[str, Any] = {"note": note}
            if banned:
                feedback_payload["banned_detected"] = banned
            if evidence_issues:
                feedback_payload["evidence_usage_issues"] = evidence_issues
            retry["feedback"] = feedback_payload
            text = self._openai_text(
                system=prompts["draft"],
                user=retry,
                project_id=project.project_id,
                temperature=0.9,
                model=model,
            )
            text = finalize_response_text(text, project)
        return text

    def _critique_logic(
        self,
        draft: str,
        project: ProjectFull,
        *,
        budget_mismatch: dict[str, Any] | None = None,
        recent_responses: Any = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
    ) -> dict[str, Any]:
        buyer_questions = extract_buyer_questions(project)
        platform = _normalize_platform(project.platform)
        prompts = _prompts_for_platform(platform)
        local_issues = self._local_issues(draft, project, budget_mismatch)
        payload: dict[str, Any] = {
            "platform": platform,
            "platform_policy": _platform_policy(platform),
            "project_brief": build_project_brief(project),
            "buyer_name": _buyer_first_name(project.buyer),
            "buyer_questions": buyer_questions,
            "recent_responses": recent_responses
            if recent_responses is not None
            else {"count": 0},
            "price_hint": price_hint or _price_from_project(project),
            "days_hint": days_hint or self.settings.default_offer_days,
            "response_text": draft,
            "local_issues": local_issues,
        }
        if budget_mismatch:
            payload["budget_mismatch"] = budget_mismatch
        verified = compact_verified_evidence(self._last_evidence)
        if verified is not None:
            payload["verified_evidence"] = verified
        model = self.settings.model_for("logic")
        logger.info(
            "response_pipeline logic project_id=%s model=%s",
            project.project_id,
            model,
        )
        data = self._openai_json(
            system=prompts["logic"],
            user=payload,
            project_id=project.project_id,
            temperature=0.1,
            model=model,
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
        *,
        budget_mismatch: dict[str, Any] | None = None,
        recent_responses: Any = None,
    ) -> dict[str, Any]:
        platform = _normalize_platform(project.platform)
        prompts = _prompts_for_platform(platform)
        payload: dict[str, Any] = {
            "platform": platform,
            "platform_policy": _platform_policy(platform),
            "project_brief": build_project_brief(project),
            "buyer_name": _buyer_first_name(project.buyer),
            "tz_facts": extract_tz_facts(project),
            "buyer_questions": extract_buyer_questions(project),
            "recent_responses": recent_responses
            if recent_responses is not None
            else {"count": 0},
            "response_text": draft,
            "critique": critique,
        }
        if budget_mismatch:
            payload["budget_mismatch"] = budget_mismatch
        verified = compact_verified_evidence(self._last_evidence)
        if verified is not None:
            payload["verified_evidence"] = verified
        model = self.settings.model_for("expert")
        logger.info(
            "response_pipeline expert project_id=%s model=%s",
            project.project_id,
            model,
        )
        data = self._openai_json(
            system=prompts["expert"],
            user=payload,
            project_id=project.project_id,
            model=model,
        )
        verdict = str(data.get("verdict") or "revise_draft").lower().strip()
        if verdict not in {"pass", "revise_draft", "revise_logic"}:
            verdict = "revise_draft"
        mismatch = budget_mismatch_issues(draft, budget_mismatch)
        if mismatch and verdict == "pass":
            verdict = "revise_draft"
        evidence_issues = evidence_usage_issues(draft, self._last_evidence)
        if evidence_issues and verdict == "pass":
            verdict = "revise_draft"
        try:
            score = int(data.get("score") or 5)
        except (TypeError, ValueError):
            score = 5
        score = max(1, min(10, score))
        must_fix = list(data.get("must_fix") or [])
        if mismatch:
            must_fix = list(must_fix) + mismatch
        if evidence_issues:
            must_fix = list(must_fix) + evidence_issues
        return {
            "verdict": verdict,
            "score": score,
            "feedback": str(data.get("feedback") or ""),
            "must_fix": must_fix,
        }

    def revise(
        self,
        project: ProjectFull,
        current_text: str,
        instruction: str,
    ) -> str:
        user = {
            "task": "Исправь отклик по инструкции фрилансера.",
            "current_text": current_text,
            "instruction": instruction,
            "title": project.title,
            "tz_snippet": (project.full_description or "")[:800],
        }
        return self._openai_text(
            system=REVISE_SYSTEM_PROMPT,
            user=user,
            project_id=project.project_id,
            temperature=0.4,
            model=self.settings.model_for("revise"),
        ).strip()

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
        budget_mismatch: dict[str, Any] | None = None,
        evidence: Any | None = None,
    ) -> str:
        self._last_evidence = evidence

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
                budget_mismatch=budget_mismatch,
                evidence=evidence,
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
            budget_mismatch=budget_mismatch,
            evidence=evidence,
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

    def _pipeline_steps(
        self,
        project: ProjectFull,
        lightrag_context: str,
        *,
        examples: str = "",
        recent_responses: Any = None,
        price_hint: int | str | None = None,
        days_hint: int | None = None,
        budget_mismatch: dict[str, Any] | None = None,
    ) -> Generator[tuple[str, Any], Any, str]:
        """Single revision loop; drivers execute the yielded notify/call steps."""
        draft_kwargs: dict[str, Any] = {
            "examples": examples,
            "recent_responses": recent_responses,
            "price_hint": price_hint,
            "days_hint": days_hint,
            "budget_mismatch": budget_mismatch,
        }
        logic_kwargs: dict[str, Any] = {
            "budget_mismatch": budget_mismatch,
            "recent_responses": recent_responses,
            "price_hint": price_hint,
            "days_hint": days_hint,
        }
        expert_kwargs: dict[str, Any] = {
            "budget_mismatch": budget_mismatch,
            "recent_responses": recent_responses,
        }
        max_cycles = max(
            1, int(getattr(self.settings, "response_max_cycles", MAX_REVISION_CYCLES))
        )
        max_seconds = float(getattr(self.settings, "response_max_seconds", 0.0) or 0.0)
        stagnation_limit = max(
            1, int(getattr(self.settings, "response_stagnation_limit", 2))
        )
        started = time.monotonic()

        yield (_STEP_NOTIFY, MSG_DRAFT)
        draft = yield (
            _STEP_CALL,
            (self._draft, (project, lightrag_context), draft_kwargs),
        )
        draft, local = self._repair_local_issues(draft, project, budget_mismatch)
        candidates: list[dict[str, Any]] = [
            {"text": draft, "local_issues": local, "expert_score": None}
        ]

        prev_signature: frozenset[str] | None = None
        stagnation = 0
        escalated = False
        critique: dict[str, Any] = {"verdict": "fail"}
        cycle = 0

        while True:
            cycle += 1
            current = candidates[-1]
            yield (_STEP_NOTIFY, MSG_LOGIC)
            critique = yield (
                _STEP_CALL,
                (
                    self._critique_logic,
                    (current["text"], project),
                    logic_kwargs,
                ),
            )
            logic_pass = critique.get("verdict") == "pass"

            expert: dict[str, Any] | None = None
            if logic_pass:
                yield (_STEP_NOTIFY, MSG_EXPERT)
                expert = yield (
                    _STEP_CALL,
                    (
                        self._expert_review,
                        (current["text"], critique, project),
                        expert_kwargs,
                    ),
                )
                current["expert_score"] = int(expert.get("score") or 0)

            logger.info(
                "response_pipeline cycle project_id=%s n=%s logic=%s "
                "expert_score=%s local_issues=%s",
                project.project_id,
                cycle,
                critique.get("verdict"),
                current["expert_score"],
                current["local_issues"],
            )

            if expert is not None and expert.get("verdict") == "pass":
                yield (_STEP_NOTIFY, MSG_DONE)
                return finalize_response_text(current["text"], project)

            signature = _issue_signature(
                current["local_issues"],
                list(critique.get("issues") or []),
                list(critique.get("missing") or []),
                list((expert or {}).get("must_fix") or []),
            )
            stagnation = stagnation + 1 if signature == prev_signature else 0
            prev_signature = signature
            stagnant = stagnation >= stagnation_limit
            timed_out = max_seconds > 0 and time.monotonic() - started >= max_seconds

            if stagnant:
                logger.info(
                    "response_pipeline stagnation project_id=%s cycle=%s "
                    "issues=%s escalated=%s",
                    project.project_id,
                    cycle,
                    sorted(signature),
                    "false" if escalated else "true",
                )
            if cycle >= max_cycles or timed_out or (stagnant and escalated):
                break

            model: str | None = None
            if stagnant:
                escalated = True
                model = self.settings.model_for("escalation")
            feedback = (
                {"role": "ExpertReviewer", **expert}
                if expert is not None
                else {"role": "LogicCritic", **critique}
            )
            yield (_STEP_NOTIFY, MSG_REVISE.format(n=cycle, total=max_cycles))
            revised = yield (
                _STEP_CALL,
                (
                    self._draft,
                    (project, lightrag_context),
                    {**draft_kwargs, "feedback": feedback, "model": model},
                ),
            )
            revised, local = self._repair_local_issues(
                revised, project, budget_mismatch
            )
            candidates.append(
                {"text": revised, "local_issues": local, "expert_score": None}
            )

        best = _pick_best_candidate(candidates)
        if best["expert_score"] is None:
            yield (_STEP_NOTIFY, MSG_EXPERT)
            final_expert = yield (
                _STEP_CALL,
                (
                    self._expert_review,
                    (best["text"], critique, project),
                    expert_kwargs,
                ),
            )
            best["expert_score"] = int(final_expert.get("score") or 0)
        yield (_STEP_NOTIFY, format_limit_message(cycle, best["local_issues"]))
        return finalize_response_text(best["text"], project)

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
        budget_mismatch: dict[str, Any] | None = None,
        evidence: Any | None = None,
    ) -> str:
        self._last_evidence = evidence
        steps = self._pipeline_steps(
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses,
            price_hint=price_hint,
            days_hint=days_hint,
            budget_mismatch=budget_mismatch,
        )
        sent: Any = None
        try:
            while True:
                kind, payload = steps.send(sent)
                sent = None
                if kind == _STEP_NOTIFY:
                    self._safe_progress(progress, payload)
                else:
                    fn, args, kwargs = payload
                    sent = fn(*args, **kwargs)
        except StopIteration as stop:
            return str(stop.value)

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
        budget_mismatch: dict[str, Any] | None = None,
        threaded: bool = True,
        evidence: Any | None = None,
    ) -> str:
        self._last_evidence = evidence

        async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
            if threaded:
                return await asyncio.to_thread(fn, *args, **kwargs)
            return fn(*args, **kwargs)

        steps = self._pipeline_steps(
            project,
            lightrag_context,
            examples=examples,
            recent_responses=recent_responses,
            price_hint=price_hint,
            days_hint=days_hint,
            budget_mismatch=budget_mismatch,
        )
        sent: Any = None
        try:
            while True:
                kind, payload = steps.send(sent)
                sent = None
                if kind == _STEP_NOTIFY:
                    await self._safe_notify(notify, payload)
                else:
                    fn, args, kwargs = payload
                    sent = await _call(fn, *args, **kwargs)
        except StopIteration as stop:
            return str(stop.value)
