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
    "title": "",
    "category": "",  # Разработка и IT → ...
    "type": "",  # Парсеры / Скрипты / ...
    "kind": "Готовые",
    "price_buyer": 500,
    "days": "1",
    "volume": "1",
    "service_size": "",  # объём 1 кворка, как в UI
    "lead": "",
    "tasks": ["", ""],
    "included": ["", "", ""],
    "excluded": ["", ""],
    "why": ["", ""],
    "start": "Для старта нужны: ...",
    "instruction_steps": [
        "...",
        "...",
        "пример 3–5 строк желаемого результата, если есть",
    ],
    "instruction_note": "Если нужен больший объём — это опции, не базовый кворк.",
    "extras": [
        {"name": "", "hint": "", "price": 800, "days": "1"},
        {"name": "", "hint": "", "price": 1200, "days": "1"},
        {"name": "", "hint": "", "price": 800, "days": "1"},
        {"name": "", "hint": "", "price": 2000, "days": "2"},
        {"name": "", "hint": "", "price": 1600, "days": "1"},
        {"name": "", "hint": "", "price": 2400, "days": "2"},
        {"name": "", "hint": "", "price": 800, "days": "0"},
    ],
    "faq_scope": "",
    "faq_not_in_base": "",
    "faq_days": "1 день",
    "faq_from_buyer": "",
    "faq_edits": "Да, один цикл правок в рамках оговорённого объёма. Новое сверх базы — через опции. 3 дня отвечаю по результату.",
    "faq_login": "База — без входа и капчи. Логин/капча/антибот — отдельно после ссылки.",
    "faq_deliverable": "В базе — согласованный файл результата. Скрипт, сервер, Telegram, расписание — опции.",
    "faq_more": "Да, через опции в карточке. Смена вёрстки источника — отдельная задача, не входит в правки.",
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
    faqs = faq_core(
        scope=src["faq_scope"],
        not_in_base=src["faq_not_in_base"],
        days=src["faq_days"],
        from_buyer=src["faq_from_buyer"],
        edits=src["faq_edits"],
    ) + faq_extra(
        login_captcha=src["faq_login"],
        deliverable=src["faq_deliverable"],
        more_volume=src["faq_more"],
    )
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
    }


def _len_ok(text: str, bounds: tuple[int, int]) -> bool:
    n = len(text.strip())
    lo, hi = bounds
    return lo <= n <= hi


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
