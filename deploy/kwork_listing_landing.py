from __future__ import annotations

"""Кворк лендинга MyPortfolio. Не трогает NEW в kwork_listing_skel.py."""

from deploy.kwork_listing_skel import LIMITS, assemble, validate

LANDING: dict = {
    "title": "Сверстаю лендинг-портфолио на Next.js",
    "category": "Разработка и IT → Создание сайта",
    "type": "Новый сайт",
    "kind": "Лендинг",
    "price_buyer": 500,
    "days": "5",
    "volume": "1",
    "service_size": "1 лендинг, до 5 секций",
    "lead": (
        "Соберу одностраничник-портфолио: проекты, услуги, стек и форма. "
        "Стек боевого кейса — Next.js 16, React 19, TypeScript, Tailwind. "
        "Пример в проде: portfolio.hayklyvibelexy.ru"
    ),
    "tasks": [
        "лендинг эксперта, разработчика или студии по ТЗ, не шаблон конструктора",
        "карточки проектов и услуг, которые потом можно менять данными, не правкой вёрстки",
    ],
    "included": [
        "1 лендинг до 5 секций: Hero, Skills, Projects, Services, Contact",
        "адаптив, секции из JSON как в кейсе MyPortfolio",
        "исходники Next.js App Router",
    ],
    "excluded": [
        "Telegram-бот, FastAPI, Docker, VPS и второй язык — опции ниже",
        "магазин, кабинет и тексты с нуля",
    ],
    "why": [
        "кейс в проде: github.com/alexklychnikov-ui/MyPortfolio",
        "до старта фиксирую список секций и состав карточек",
    ],
    "start": (
        "Для старта нужны роль на лендинге, 3–5 проектов, список услуг и референс по виду, если есть."
    ),
    "instruction_steps": [
        "кем показать на лендинге и 1–2 предложения о себе",
        "3–5 проектов: название, стек, ссылка или скрин",
        "услуги и навыки списком; фото или логотип, если есть",
    ],
    "instruction_note": "Бот, API, Docker, второй язык и деплой — опции, не базовый кворк.",
    "extras": [
        {
            "name": "Второй язык: русский и английский",
            "hint": "Полный русский и английский, переключатель языка как в кейсе MyPortfolio.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Форма заявок на почту",
            "hint": "Форма заявок на почту: имя, адрес и сообщение приходят вам.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Бот обновления карточек проектов",
            "hint": "Кидаете ссылки на репозитории в бота: разбор кода и обновление проектов, услуг и навыков.",
            "price": 2400,
            "days": "2",
        },
        {
            "name": "Серверная база и АПИ секций",
            "hint": "Сервер и база: записывает и отдаёт данные секций лендинга.",
            "price": 2400,
            "days": "2",
        },
        {
            "name": "Контейнеры и выкладка на сервер",
            "hint": "Собираю контейнеры, прокси и сертификат, выкладываю сайт на ваш сервер.",
            "price": 2000,
            "days": "2",
        },
        {
            "name": "Ещё одна страница на том же стеке",
            "hint": "Ещё одна страница в том же стеке. Счётчик опции — сколько страниц добавить.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Срочная вёрстка в приоритете",
            "hint": "Приоритет в очереди при готовом ТЗ. Базовый срок не растягиваю.",
            "price": 800,
            "days": "0",
        },
    ],
    "faq_scope": (
        "1 лендинг до 5 секций на Next.js 16, React 19, TypeScript, Tailwind; "
        "секции из JSON; адаптив; исходники"
    ),
    "faq_not_in_base": "Telegram-бот, FastAPI, Docker, VPS и второй язык",
    "faq_days": "5 дней",
    "faq_from_buyer": (
        "Роль на лендинге, 3–5 проектов со стеком и ссылкой/скрином, список услуг, "
        "фото или логотип если есть. Референс по виду — по желанию."
    ),
    "faq_edits": (
        "Да, один цикл правок по секциям и текстам в согласованной структуре. "
        "Новая страница, бот или деплой — через опции."
    ),
    "faq_extra_items": [
        {
            "q": "Можно посмотреть пример?",
            "a": (
                "Да: https://portfolio.hayklyvibelexy.ru и исходники "
                "https://github.com/alexklychnikov-ui/MyPortfolio. "
                "Базовый кворк — фронт лендинга, бот и API как на сайте — опции."
            ),
        },
        {
            "q": "Tilda, WordPress или чистый HTML?",
            "a": (
                "Нет. Самописный Next.js App Router, как в атрибутах кворка. "
                "Конструкторы в базу не входят."
            ),
        },
        {
            "q": "Бот сам меняет любые блоки сайта?",
            "a": (
                "Нет. Опция бота обновляет JSON проектов, услуг и навыков по GitHub-ссылкам, "
                "как в кейсе. Произвольный CMS из чата в кворк не входит."
            ),
        },
    ],
}


if __name__ == "__main__":
    built = assemble(LANDING)
    if not str(LANDING.get("title") or "").strip():
        print("Fill LANDING in deploy/kwork_listing_landing.py, then rerun.")
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
