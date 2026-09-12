from __future__ import annotations

"""Кворк FullCRM. Не трогает NEW в kwork_listing_skel.py, LANDING и PRICE."""

from deploy.kwork_listing_skel import LIMITS, assemble, validate

CRM: dict = {
    "title": "Соберу CRM со сделками и воронкой на Next.js",
    "category": "Разработка и IT → Создание сайта",
    "type": "Новый сайт",
    "kind": "Интернет-сервис",
    "github_url": "https://github.com/alexklychnikov-ui/FullCRM",
    "price_buyer": 500,
    "days": "5",
    "volume": "1",
    "service_size": "1 организация, компании/контакты/сделки",
    "lead": (
        "Соберу ядро CRM: компании, контакты, сделки и доска этапов. "
        "Стек кабинета — Next.js 15, React 19, TypeScript, Tailwind, API на Python, PostgreSQL."
    ),
    "tasks": [
        "воронка сделок на доске этапов Новая/Квалифицирована/Завершена, не таблица Excel",
        "компании и контакты в одном кабинете рядом со сделками, не в разрозненных файлах",
    ],
    "included": [
        "1 организация, вход по email и паролю",
        "компании, контакты, сделки и доска этапов воронки",
        "исходники Next.js App Router и API на Python",
    ],
    "excluded": [
        "коммуникации, аналитика, ИИ, контейнеры и сервер — опции ниже",
        "Gmail и Calendar live, биллинг, задачи и портал — в базу не входят",
    ],
    "why": [
        "самописный кабинет: воронка, роли и исходники под вашу организацию",
        "до старта фиксирую этапы воронки и роли",
    ],
    "start": (
        "Для старта нужны название организации, этапы воронки или стандарт "
        "Новая/Квалифицирована/Завершена и роли."
    ),
    "instruction_steps": [
        "название организации и роли в кабинете",
        "этапы воронки или стандарт Новая/Квалифицирована/Завершена",
        "домен для выкладки или только исходники",
    ],
    "instruction_note": "Коммуникации, аналитика, ИИ и выкладка — опции, не базовый кворк.",
    "extras": [
        {
            "name": "Лента сообщений на карточках",
            "hint": "Лента и ручные записи на карточке сделки или контакта, без почты live.",
            "price": 1600,
            "days": "1",
        },
        {
            "name": "Сводка воронки и денег",
            "hint": "Сводка этапов воронки: конверсия и средний чек по сделкам.",
            "price": 2400,
            "days": "2",
        },
        {
            "name": "ИИ-подсказки по сделкам",
            "hint": "Вероятность, следующее действие и черновик. Совет, не истина.",
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
            "name": "Ещё один пользователь в кабинете",
            "hint": "Ещё один вход в кабинет. Счётчик опции — сколько людей добавить.",
            "price": 800,
            "days": "1",
        },
        {
            "name": "Срочный запуск в приоритете",
            "hint": "Приоритет в очереди при готовом ТЗ. Базовый срок не растягиваю.",
            "price": 800,
            "days": "0",
        },
    ],
    "faq_scope": (
        "1 организация, вход email/пароль, компании/контакты/сделки, "
        "доска этапов воронки, исходники Next.js и API на Python"
    ),
    "faq_not_in_base": "Коммуникации, аналитика, ИИ, контейнеры и сервер",
    "faq_days": "5 дней",
    "faq_from_buyer": (
        "Название организации, роли, этапы воронки или стандарт "
        "Новая/Квалифицирована/Завершена. Домен — если берёте выкладку."
    ),
    "faq_edits": (
        "Да, один цикл правок полей и этапов в согласованной воронке. "
        "Коммуникации, аналитика, ИИ или выкладка — через опции."
    ),
    "faq_extra_items": [
        {
            "q": "Можно посмотреть пример?",
            "a": (
                "Да, по запросу покажу рабочий кабинет до старта. "
                "База — компании, контакты, сделки, доска этапов. "
                "Коммуникации, аналитика и ИИ — опции."
            ),
        },
        {
            "q": "Это Bitrix, amoCRM или Salesforce?",
            "a": (
                "Нет. Самописный кабинет, не коробка Bitrix, amoCRM или Salesforce."
            ),
        },
        {
            "q": "Нужен свой сервер сразу?",
            "a": (
                "Нет. База — исходники кабинета. "
                "Выкладка контейнерами на ваш сервер — отдельная опция."
            ),
        },
    ],
}


FORBIDDEN_COPY = ("github.com", "alexklyvibe", "testfullcrm", "http://", "https://")


def buyer_copy_problems(built: dict) -> list[str]:
    blob_parts = [
        str(built.get("description") or ""),
        str(built.get("instruction") or ""),
    ]
    for extra in built.get("extras") or []:
        blob_parts.append(str(extra.get("name") or ""))
        blob_parts.append(str(extra.get("hint") or ""))
    for faq in built.get("faqs") or []:
        blob_parts.append(str(faq.get("q") or ""))
        blob_parts.append(str(faq.get("a") or ""))
    blob = "\n".join(blob_parts).lower()
    return [f"buyer copy contains {token}" for token in FORBIDDEN_COPY if token in blob]


if __name__ == "__main__":
    built = assemble(CRM)
    if not str(CRM.get("title") or "").strip():
        print("Fill CRM in deploy/kwork_listing_fullcrm.py, then rerun.")
        print("limits", LIMITS)
        raise SystemExit(0)
    problems = validate(built) + buyer_copy_problems(built)
    print("title", built["title"] or "(empty)")
    print("desc", len(built["description"]), "instr", len(built["instruction"]))
    print("extras", len(built["extras"]), "faqs", len(built["faqs"]))
    if problems:
        print("FAIL")
        for item in problems:
            print("-", item)
        raise SystemExit(1)
    print("OK")
