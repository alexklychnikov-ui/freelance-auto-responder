from __future__ import annotations

"""Скелет нового кворка. Копируй NEW, заполни, проверь: python deploy/kwork_listing_skel.py"""

LIMITS = {
    "title": (1, 70),
    "description": (100, 1200),
    "instruction": (100, 500),
    "extra_name": (1, 40),
    "extra_hint": (1, 100),
    "faq_q": (1, 80),
    "faq_a": (1, 300),
}

# Покупатель видит price; в select value = buyer * 5 // 4 (комиссия 20%).
# Срок опции: "0" | "1" | "2" | ...

GITHUB_CASE_MIN_BUYER = 500
EXTRA_BUYER_GRID = (800, 1200, 1600, 2000, 2400)  # сетка extra, buyer 500 в extra нет


def buyer_to_select_value(buyer_rub: int) -> str:
    return str(int(buyer_rub) * 5 // 4)


def build_description(
    *,
    lead: str,
    tasks: list[str],
    included: list[str],
    excluded: list[str],
    why: list[str],
    start: str,
) -> str:
    lines = [lead.strip(), "", "Какие задачи закрывает"]
    lines.extend(f"• {x}" for x in tasks)
    lines.extend(["", "Что входит"])
    lines.extend(f"• {x}" for x in included)
    lines.extend(["", "Что не входит"])
    lines.extend(f"• {x}" for x in excluded)
    lines.extend(["", "Почему заказать здесь"])
    lines.extend(f"• {x}" for x in why)
    lines.extend(["", start.strip()])
    return "\n".join(lines)


def build_instruction(steps: list[str], note: str = "") -> str:
    body = "Пришлите:\n" + "\n".join(f"{i}) {s}" for i, s in enumerate(steps, 1))
    note = note.strip()
    return f"{body}\n{note}" if note else body


SECTION_TITLES = (
    "Какие задачи закрывает",
    "Что входит",
    "Что не входит",
    "Почему заказать здесь",
    "Пришлите:",
)


def to_kwork_html(plain: str) -> str:
    """Абзацы для Trumbowyg: каждый блок свой <p>. URL не экранировать. Не ul."""
    parts: list[str] = []
    for raw in (plain or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("•"):
            line = "— " + line.lstrip("•").strip()
        parts.append(f"<p>{line}</p>")
    return "".join(parts)


def faq_core(*, scope: str, not_in_base: str, days: str, from_buyer: str, edits: str) -> list[dict[str, str]]:
    return [
        {
            "q": "Что входит в стоимость?",
            "a": f"{scope}. {not_in_base} в базовый кворк не входят.",
        },
        {
            "q": "Какие сроки выполнения?",
            "a": f"Базовый кворк — {days} после входных данных. Опции добавляют 0–2 дня, срок указан в карточке опции.",
        },
        {
            "q": "Что нужно от меня для старта?",
            "a": from_buyer,
        },
        {
            "q": "Можно ли внести правки после сдачи?",
            "a": edits,
        },
    ]


def faq_extra(*, login_captcha: str, deliverable: str, more_volume: str) -> list[dict[str, str]]:
    return [
        {"q": "Сайт с логином или капчей. Возьмёте?", "a": login_captcha},
        {"q": "Что придёт на выходе: файл или исходник?", "a": deliverable},
        {"q": "Нужен больший объём или повтор?", "a": more_volume},
    ]


# --- заполняй это ---
NEW: dict = {
    "title": "Создам аналитический отчёт по данным",
    "category": "Бизнес и жизнь → Персональный помощник",
    "type": "Анализ информации",
    "kind": "",
    "price_buyer": 500,
    "days": "1",
    "volume": "1",
    "service_size": "1 датасет, до 2 000 строк",
    "lead": (
        "Разберу ваш Excel, CSV или таблицу и соберу короткий отчёт: что происходит в цифрах, "
        "где просадка и что делать дальше. Без простыни сырых строк — выводы и графики."
    ),
    "tasks": [
        "продажи, реклама, заявки, остатки — увидеть динамику и узкие места",
        "отчёт для себя, заказчика или планёрки, а не ещё одна таблица",
    ],
    "included": [
        "1 файл: Excel, CSV или Google Sheets",
        "до 2 000 строк и до 8 показателей",
        "выводы, 3–5 графиков, файл отчёта PDF или Excel",
    ],
    "excluded": [
        "парсинг сайтов и вход в рекламные кабинеты под ключ",
        "дашборд, автообновление и несколько источников — опции ниже",
    ],
    "why": [
        "до старта фиксирую вопросы отчёта и список метрик",
        "базовый кворк даёт готовый файл; углубление — отдельными шагами",
    ],
    "start": "Для старта нужны файл или ссылка на таблицу, 3–5 вопросов к данным и пример желаемого вида, если есть.",
    "instruction_steps": [
        "файл Excel/CSV или ссылку на Google Sheets",
        "3–5 вопросов, на которые должен ответить отчёт",
        "пример или скрин желаемого вида, если есть",
    ],
    "instruction_note": "Несколько источников, дашборд и автообновление — опции, не базовый кворк.",
    "extras": [
        {
            "name": "Второй источник в тот же отчёт",
            "hint": "Ещё один файл или таблица в тот же отчёт, без новых кабинетов.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Больше метрик и графиков",
            "hint": "Доп. показатели сверх 8 и дополнительные графики в том же файле.",
            "price": 1200,
            "days": "1",
        },
        {
            "name": "Очистка и проверка данных",
            "hint": "Дубли, пустые, единый формат дат и сумм, короткая пометка о качестве.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Ещё один отчёт по той же структуре",
            "hint": "Тот же объём и метрики, новый период или файл. Счётчик опции = сколько ещё отчётов.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Краткий дайджест в Telegram",
            "hint": "Короткое сообщение с выводами, когда отчёт готов.",
            "price": 1600,
            "days": "1",
        },
        {
            "name": "Интерактивный дашборд",
            "hint": "Онлайн-дашборд по тем же метрикам плюс ссылка и краткая инструкция.",
            "price": 2400,
            "days": "2",
        },
        {
            "name": "Срочный отчёт в тот же день",
            "hint": "Приоритет в очереди: отдам отчёт в течение дня при готовых данных.",
            "price": 800,
            "days": "0",
        },
    ],
    "faq_scope": "1 файл Excel/CSV/Google Sheets, до 2 000 строк, до 8 показателей, выводы и 3–5 графиков, PDF или Excel",
    "faq_not_in_base": "Парсинг сайтов, кабинеты рекламы, дашборд, автообновление и второй источник",
    "faq_days": "1 день",
    "faq_from_buyer": "Файл или ссылка на таблицу, 3–5 вопросов к данным и пример вида, если есть. Если ТЗ нет — напишите, что хотите увидеть в отчёте, зафиксирую объём до старта.",
    "faq_edits": "Да, один цикл правок по выводам и графикам в рамках оговорённых метрик. Новый источник или другой формат — через опции. 3 дня отвечаю по файлу.",
    "faq_extra_items": [
        {
            "q": "Какие данные подойдут?",
            "a": "Excel, CSV или Google Sheets, которые вы уже выгрузили. Доступ в рекламные кабинеты и CRM — отдельно после описания, в базу не входит.",
        },
        {
            "q": "Что будет на выходе?",
            "a": "Файл отчёта PDF или Excel: выводы, 3–5 графиков и цифры по согласованным метрикам. Дашборд и рассылка в Telegram — опции.",
        },
        {
            "q": "Нужно больше строк или повтор?",
            "a": "Да, через опцию «Ещё один отчёт по той же структуре»: крутите количество. Второй источник и больше метрик — отдельные опции. Смена постановки после сдачи в правки не входит.",
        },
    ],
}


def assemble(listing: dict | None = None) -> dict:
    src = listing or NEW
    description = build_description(
        lead=src["lead"],
        tasks=[x for x in src["tasks"] if x],
        included=[x for x in src["included"] if x],
        excluded=[x for x in src["excluded"] if x],
        why=[x for x in src["why"] if x],
        start=src["start"],
    )
    instruction = build_instruction(src["instruction_steps"], src.get("instruction_note") or "")
    extras = [e for e in src["extras"] if str(e.get("name", "")).strip()]
    extra_faqs = [x for x in (src.get("faq_extra_items") or []) if x.get("q") and x.get("a")]
    if extra_faqs:
        more = extra_faqs
    else:
        more = faq_extra(
            login_captcha=src.get("faq_login") or "",
            deliverable=src.get("faq_deliverable") or "",
            more_volume=src.get("faq_more") or "",
        )
    faqs = faq_core(
        scope=src["faq_scope"],
        not_in_base=src["faq_not_in_base"],
        days=src["faq_days"],
        from_buyer=src["faq_from_buyer"],
        edits=src["faq_edits"],
    ) + more
    return {
        "title": src["title"],
        "category": src["category"],
        "type": src["type"],
        "kind": src["kind"],
        "price_buyer": src["price_buyer"],
        "price_select": buyer_to_select_value(int(src["price_buyer"])),
        "days": src["days"],
        "volume": src["volume"],
        "service_size": src["service_size"],
        "description": description,
        "instruction": instruction,
        "extras": extras,
        "faqs": faqs,
        "github_url": src.get("github_url") or "",
    }


def _len_ok(text: str, bounds: tuple[int, int]) -> bool:
    n = len(text.strip())
    lo, hi = bounds
    return lo <= n <= hi


def github_case_problems(built: dict) -> list[str]:
    url = str(built.get("github_url") or "")
    if "github.com/" not in url.lower():
        return []
    err: list[str] = []
    buyer = int(built["price_buyer"])
    if buyer != GITHUB_CASE_MIN_BUYER:
        err.append(f"github case: price_buyer {buyer} != {GITHUB_CASE_MIN_BUYER}")
    extras = built["extras"]
    n_ex = len(extras)
    if n_ex < 5 or n_ex > 7:
        err.append(f"github case: extras {n_ex}, want 5–7")
    extra_sum = 0
    for i, extra in enumerate(extras):
        price = int(extra["price"])
        extra_sum += price
        if price not in EXTRA_BUYER_GRID or price <= GITHUB_CASE_MIN_BUYER:
            err.append(f"github case: extra[{i}].price {price} not in {EXTRA_BUYER_GRID}")
    if extra_sum < 4000:
        err.append(f"github case: extras sum {extra_sum} < 4000")
    blob_parts = [str(built.get("description") or "")]
    excluded = built.get("excluded")
    if isinstance(excluded, list):
        blob_parts.extend(str(x) for x in excluded)
    elif excluded:
        blob_parts.append(str(excluded))
    if "опци" not in "\n".join(blob_parts).lower():
        err.append("github case: description+excluded missing «опци»")
    return err


def validate(built: dict) -> list[str]:
    err: list[str] = []
    if not _len_ok(built["title"], LIMITS["title"]):
        err.append(f"title {len(built['title'])} not in {LIMITS['title']}")
    if not _len_ok(built["description"], LIMITS["description"]):
        err.append(f"description {len(built['description'])} not in {LIMITS['description']}")
    if not _len_ok(built["instruction"], LIMITS["instruction"]):
        err.append(f"instruction {len(built['instruction'])} not in {LIMITS['instruction']}")
    n_ex = len(built["extras"])
    if n_ex < 5 or n_ex > 7:
        err.append(f"extras {n_ex}, want 5–7")
    for i, extra in enumerate(built["extras"]):
        if not _len_ok(extra["name"], LIMITS["extra_name"]):
            err.append(f"extra[{i}].name {len(extra['name'])} not in {LIMITS['extra_name']}")
        if not _len_ok(extra["hint"], LIMITS["extra_hint"]):
            err.append(f"extra[{i}].hint {len(extra['hint'])} not in {LIMITS['extra_hint']}")
    if not 4 <= len(built["faqs"]) <= 8:
        err.append(f"faqs {len(built['faqs'])}, want 4–8")
    qs = [f["q"] for f in built["faqs"]]
    if len(qs) != len(set(qs)):
        err.append("duplicate FAQ questions")
    for i, faq in enumerate(built["faqs"]):
        if not _len_ok(faq["q"], LIMITS["faq_q"]):
            err.append(f"faq[{i}].q {len(faq['q'])} not in {LIMITS['faq_q']}")
        if not _len_ok(faq["a"], LIMITS["faq_a"]):
            err.append(f"faq[{i}].a {len(faq['a'])} not in {LIMITS['faq_a']}")
    err.extend(github_case_problems(built))
    return err


if __name__ == "__main__":
    built = assemble()
    if not str(NEW.get("title") or "").strip():
        print("Fill NEW in deploy/kwork_listing_skel.py, then rerun.")
        print("limits", LIMITS)
        raise SystemExit(0)
    problems = validate(built)
    print("title", built["title"] or "(empty)")
    print("desc", len(built["description"]), "instr", len(built["instruction"]))
    print("extras", len(built["extras"]), "faqs", len(built["faqs"]))
    if problems:
        print("FAIL")
        for item in problems:
            print("-", item)
        raise SystemExit(1)
    print("OK")
