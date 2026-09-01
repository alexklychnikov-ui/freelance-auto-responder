from __future__ import annotations

"""Кворк мониторинга цен. Не трогает NEW в skel и LANDING в kwork_listing_landing.py."""

from deploy.kwork_listing_skel import LIMITS, assemble, validate

FORBIDDEN_EXTRA = ("telegram", "github", "docker", "vps", "grafana")
ALLOWED_EXTRA_PRICES = {800, 1200, 1600, 2000, 2400}

PRICE: dict = {
    "title": "Соберу мониторинг цен с историей в базе",
    "category": "Разработка и IT → Скрипты, боты и mini apps",
    "type": "Парсеры",
    "kind": "Написание и доработка",
    "price_buyer": 500,
    "days": "5",
    "volume": "1",
    "service_size": "1 открытый сайт, каталог и история цен",
    "lead": (
        "Соберу сбор цен с открытой витрины в каталог с историей: было/стало, порог изменения, API. "
        "Стек кейса — FastAPI, PostgreSQL, Celery, Redis. "
        "Пример в проде: alexklyvibe.ru"
    ),
    "tasks": [
        "следить за ценами конкурента на открытой витрине, не копировать руками",
        "видеть историю и скачки, а не разовый Excel",
    ],
    "included": [
        "1 открытый каталог без логина и капчи",
        "запись товаров и истории цен в PostgreSQL, выдача через FastAPI",
        "исходники парсера и API",
    ],
    "excluded": [
        "чат-бот, веб-кабинет, панель метрик, контейнеры и сервер — опции ниже",
        "закрытые кабинеты, капча и антибот — в базу не входят",
    ],
    "why": [
        "кейс в проде: github.com/alexklychnikov-ui/PriceMonitoring",
        "до старта фиксирую URL, поля карточки и порог алерта",
    ],
    "start": (
        "Для старта нужны ссылка на витрину или категорию, какие поля собирать "
        "и порог изменения цены в процентах, если нужен."
    ),
    "instruction_steps": [
        "ссылку на открытую витрину или категорию без логина",
        "какие поля: название, цена, бренд, размер, ссылка на карточку",
        "порог изменения в процентах, если нужны алерты позже",
    ],
    "instruction_note": (
        "Чат-бот, веб-кабинет, панель метрик и выкладка на сервер — опции, не базовый кворк. "
        "Доступ по SSH в базу не входит."
    ),
    "extras": [
        {
            "name": "Ещё один магазин в ту же систему",
            "hint": "Ещё одна открытая витрина в тот же каталог. Счётчик опции — сколько магазинов.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Оповещения в чат-боте",
            "hint": "Бот: поиск, подписка на товар, алерты при скачке цены, статус сбора.",
            "price": 2400,
            "days": "2",
        },
        {
            "name": "Веб-кабинет каталога и графиков",
            "hint": "Кабинет на React: каталог, фильтры, карточка товара, график истории.",
            "price": 2400,
            "days": "2",
        },
        {
            "name": "Панель метрик и графиков",
            "hint": "Отдельная панель: объём каталога, алерты, запуски сбора за сутки.",
            "price": 1600,
            "days": "1",
        },
        {
            "name": "Контейнеры и выкладка на сервер",
            "hint": "Сборка контейнеров, прокси и SSL, сайт и API на вашем сервере.",
            "price": 2000,
            "days": "2",
        },
        {
            "name": "ИИ-разбор скачков цены",
            "hint": "Краткий разбор динамики и рекомендации по порогу, без обещания LLM всегда.",
            "price": 1600,
            "days": "1",
        },
        {
            "name": "Срочный запуск в приоритете",
            "hint": "Приоритет в очереди при готовых URL и полях. Базовый срок не растягиваю.",
            "price": 800,
            "days": "0",
        },
    ],
    "faq_scope": (
        "1 открытый каталог без логина, товары и история цен в PostgreSQL, "
        "выдача через FastAPI, исходники"
    ),
    "faq_not_in_base": "Чат-бот, веб-кабинет, панель метрик, контейнеры и сервер",
    "faq_days": "5 дней",
    "faq_from_buyer": (
        "Ссылка на открытую витрину, список полей карточки, порог изменения в процентах если нужен. "
        "SSH и токен бота — только если берёте опции сервера или бота."
    ),
    "faq_edits": (
        "Да, один цикл правок полей и селекторов в рамках согласованного сайта. "
        "Новый магазин, бот или кабинет — через опции."
    ),
    "faq_extra_items": [
        {
            "q": "Можно посмотреть пример?",
            "a": (
                "Да: https://alexklyvibe.ru и исходники "
                "https://github.com/alexklychnikov-ui/PriceMonitoring. "
                "Базовый кворк — парсер и API одного каталога; бот, кабинет и сервер — опции."
            ),
        },
        {
            "q": "С любых сайтов соберёте цены?",
            "a": (
                "Нет. Кейс — открытые шинные витрины как в репозитории. "
                "Другой открытый каталог — после оценки страницы до старта. "
                "Логин, капча и антибот в базу не входят."
            ),
        },
        {
            "q": "Нужен свой сервер сразу?",
            "a": (
                "Нет. База — исходники парсера и API. "
                "Выкладка контейнерами на ваш сервер — отдельная опция."
            ),
        },
    ],
}


def ru_pct(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    ru = [c for c in letters if "а" <= c.lower() <= "я" or c.lower() == "ё"]
    return 100.0 * len(ru) / len(letters)


def extra_problems(src: dict, built: dict) -> list[str]:
    err: list[str] = []
    desc = built["description"]
    low_desc = desc.lower()
    if "любые сайты" in low_desc or "любых сайтах" in low_desc:
        err.append("description contains «любые сайты»")
    if "prometheus" in desc.lower():
        err.append("description mentions Prometheus")
    inst = built["instruction"]
    if not inst.startswith("Пришлите"):
        err.append("instruction must start with Пришлите")
    if "vps" in inst.lower() and "опци" not in inst.lower():
        err.append("instruction puts VPS in base")
    qs = [f["q"] for f in built["faqs"]]
    if not any("пример" in q.lower() for q in qs):
        err.append("FAQ missing example question")
    if not any("любых сайтов" in q.lower() for q in qs):
        err.append("FAQ missing «С любых сайтов»")
    for i, extra in enumerate(built["extras"]):
        name = extra["name"]
        low = name.lower()
        if ru_pct(name) < 35:
            err.append(f"extra[{i}].name cyrillic {ru_pct(name):.0f}%")
        if any(w in low for w in FORBIDDEN_EXTRA):
            err.append(f"extra[{i}].name forbidden token: {name}")
        price = int(extra["price"])
        if price not in ALLOWED_EXTRA_PRICES:
            err.append(f"extra[{i}].price {price} not in {sorted(ALLOWED_EXTRA_PRICES)}")
        if price == 500:
            err.append(f"extra[{i}].price 500 not in extra select")
    return err


if __name__ == "__main__":
    built = assemble(PRICE)
    if not str(PRICE.get("title") or "").strip():
        print("Fill PRICE in deploy/kwork_listing_price_monitor.py, then rerun.")
        print("limits", LIMITS)
        raise SystemExit(0)
    problems = validate(built) + extra_problems(PRICE, built)
    print("title", built["title"] or "(empty)")
    print("desc", len(built["description"]), "instr", len(built["instruction"]))
    print("extras", len(built["extras"]), "faqs", len(built["faqs"]))
    for i, extra in enumerate(built["extras"]):
        print(
            f"extra[{i}]",
            len(extra["name"]),
            f"ru={ru_pct(extra['name']):.0f}%",
            extra["name"],
            extra["price"],
            "select",
            int(extra["price"]) * 5 // 4,
        )
    if problems:
        print("FAIL")
        for item in problems:
            print("-", item)
        raise SystemExit(1)
    print("OK")
