from __future__ import annotations

from src.analyzer.project_brief import (
    buyer_checklist_issues,
    extract_buyer_checklist,
    extract_buyer_questions,
    extract_tz_facts,
)
from src.models import ProjectFull


def _bots_project() -> ProjectFull:
    desc = """
Доработать 2 Telegram-бота по готовому ТЗ
Проект №1: внутренний бот для учета контактов
Проект №2: клиентский бот поддержки
При отклике укажите:
1. Стоимость.
2. Срок.
3. На чем будете разрабатывать.
4. Готовы ли посмотреть текущий код.
5. Что будет входить в итоговую передачу проекта.
"""
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3204427",
        url="https://kwork.ru/projects/3204427/view",
        title="Доработать 2 Telegram-бота по готовому ТЗ",
        full_description=desc,
        desired_budget="до 35 000 ₽",
        max_budget="до 105 000 ₽",
    )


def _numbered_without_header() -> ProjectFull:
    desc = """
Нужен парсер и бот под уведомления.
Вопросы:
1) Какой срок?
2) Какая цена?
3) На чём пишете?
4) Будет ли админка?
5) Как деплой?
6) Есть ли сопровождение?
"""
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3217391",
        url="https://kwork.ru/projects/3217391/view",
        title="Парсер и Telegram-бот",
        full_description=desc,
    )


def test_two_bots_not_marked_as_parsing() -> None:
    project = _bots_project()
    facts = extract_tz_facts(project)
    assert any("Telegram-бот" in f for f in facts)
    assert not any("парсинга" in f for f in facts)


def test_extract_buyer_checklist() -> None:
    items = extract_buyer_checklist(_bots_project())
    assert len(items) == 5
    assert "Стоимость" in items[0]


def test_extract_buyer_questions_without_checklist_header() -> None:
    items = extract_buyer_questions(_numbered_without_header())
    assert len(items) == 6
    assert "Какой срок" in items[0]
    assert extract_buyer_checklist(_numbered_without_header()) == []


def test_extract_buyer_questions_with_header() -> None:
    items = extract_buyer_questions(_bots_project())
    assert len(items) == 5
    assert items == extract_buyer_checklist(_bots_project())


def test_buyer_checklist_issues_detects_missing() -> None:
    bad = "Задача по парсингу Telegram понятна. Готов ответить в чате Kwork."
    issues = buyer_checklist_issues(_bots_project(), bad)
    assert "checklist:стек" in issues
    assert "checklist:код" in issues


def test_buyer_checklist_issues_ok() -> None:
    ok = (
        "Стоимость: 42000 ₽. Срок: 14 дн. Стек: Python, aiogram. "
        "Готов посмотреть текущий код и наработки. "
        "Передача: исходники, база, инструкция по запуску."
    )
    assert not buyer_checklist_issues(_bots_project(), ok)


def _kwork_3234444() -> ProjectFull:
    desc = (
        "Требуется разработка Telegram-ботаИщу специалиста для разработки Telegram-бота. "
        "Что нужно: * Создать Telegram-бота с удобным интерфейсом. "
        "При отклике прошу указать: Какие технологии будете использовать. "
        "Срок выполнения. Стоимость."
    )
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3234444",
        url="https://kwork.ru/projects/3234444/view",
        title="Разработка чат-бота в телеграмме",
        full_description=desc,
    )


def test_extract_buyer_questions_kwork_colon_list() -> None:
    items = extract_buyer_questions(_kwork_3234444())
    assert len(items) == 3
    assert "технолог" in items[0].lower()
    assert "срок" in items[1].lower()
    assert "стоимост" in items[2].lower()


def test_buyer_checklist_issues_kwork_3234444_missing_stack() -> None:
    bad = (
        "Создам Telegram-бота с меню и кнопками. "
        "Срок выполнения — 10 дней, стоимость — от 12 000 ₽."
    )
    issues = buyer_checklist_issues(_kwork_3234444(), bad)
    assert "checklist:стек" in issues
    assert "checklist:срок" not in issues
    assert "checklist:стоимость" not in issues


def test_buyer_checklist_issues_kwork_3234444_ok() -> None:
    ok = (
        "Технологии: Python, aiogram, PostgreSQL. "
        "Срок — 10–14 дней. Стоимость — от 12 000 ₽."
    )
    assert not buyer_checklist_issues(_kwork_3234444(), ok)


_YANDEX_347BC2FC_DESC = (
    "Нужна разработка простой внутренней веб-системы для учёта рабочего времени "
    "сотрудников компании (около 30 человек).Есть готовое подробное ТЗ."
    "Основные функции:— создание и изменение графиков сотрудников;— QR-отметка "
    "прихода и ухода;— проверка нахождения сотрудника в рабочей Wi-Fi сети;— "
    "автоматический расчёт фактически отработанных часов;— контроль опозданий и "
    "ранних уходов для сотрудников, для которых это предусмотрено;— учёт выходных, "
    "отгулов, отпусков, больничных и других отсутствий;— заявки сотрудников и их "
    "согласование руководителями;— разные права доступа для директора, заместителя "
    "директора, начальника производства и сотрудников;— контроль посещаемости;— "
    "автоматическое формирование месячного табеля;— отчёты и выгрузка в Excel;— "
    "журнал изменений.Отдельное мобильное приложение не требуется. Нужна удобная "
    "адаптивная веб-версия для компьютера и телефона.Важно:Ищем именно разработчика "
    "внутренних веб-систем / CRM / автоматизации, а не специалиста только по сайтам "
    "или настройке готовой CRM.Бюджет ограниченный. Хотим получить максимально "
    "простое и недорогое решение без ежемесячной оплаты за каждого сотрудника."
    "Предпочтительно разработчик из Минска, чтобы при необходимости можно было "
    "встретиться и установить систему на месте.Готовы разделить разработку на 2 "
    "этапа: сначала основной необходимый функционал, затем дополнительные "
    "возможности.Просьба в отклике сразу указать:ориентировочную стоимость "
    "разработки;срок;что входит в стоимость;какие будут обязательные расходы "
    "после запуска ("
)

_YANDEX_347BC2FC_BAD = """\
Здравствуйте!
Предлагаю разработать внутреннюю веб-систему для учёта рабочего времени сотрудников, которая автоматизирует процесс создания графиков, QR-отметку прихода и ухода, а также контроль посещаемости. Это обеспечит точный расчёт отработанных часов и упростит управление отсутствиями, включая отпуска и больничные. Система будет иметь адаптивный интерфейс как для компьютеров, так и для мобильных устройств, что сделает её использование удобным для всех сотрудников.
Предлагаю реализовать функционал поэтапно: сначала основные функции, а затем дополнительные возможности по вашему желанию. Сроки разработки: 14 дней. Ориентировочная стоимость — от 35 000 ₽, что включает все основные функции.
Обязательные расходы после запуска — хостинг и поддержка, которые можно обсудить. Если подход устраивает, дайте знать — уточню детали и оценку.
"""

_YANDEX_347BC2FC_GOLD = """\
Здравствуйте!
Соберу внутреннюю веб-систему учёта времени на ~30 человек: графики, QR прихода/ухода, Wi-Fi-метка, часы, опоздания, отпуска/больничные, заявки с согласованием, роли, табель, Excel и журнал. Нативное приложение не нужно — адаптивный веб, без платы за сотрудника.
Этап 1: графики, QR, Wi-Fi, часы, роли, табель, Excel. Этап 2: заявки, отсутствия, журнал, отчёты. Стек: Python/FastAPI + PostgreSQL, на ваш сервер.
Этап 1 — 35 000 ₽, срок 14 дней. В сумму входит основной функционал, установка на хост, инструкция и проверка сценариев. После запуска: VPS 500–1500 ₽/мес и домен; SaaS за человека нет. Поддержка отдельно.
Работаю удалённо из Иркутска, не Минск; установку закрою удалённо. Если ок — пришлите ТЗ, зафиксирую границы этапа 1.
"""


def _yandex_347bc2fc() -> ProjectFull:
    return ProjectFull(
        platform="yandex_uslugi",
        source_key="yandex_uslugi",
        project_id="347bc2fc",
        url="https://uslugi.yandex.ru/orders/347bc2fc",
        title="Разработать несложную внутреннюю веб-систему для учёта рабочего времени сотрудников",
        full_description=_YANDEX_347BC2FC_DESC,
        desired_budget="199 ₽",
        max_budget="до 40 000",
    )


def test_extract_buyer_questions_yandex_347bc2fc() -> None:
    items = extract_buyer_questions(_yandex_347bc2fc())
    assert len(items) == 4
    blob = " ".join(items).lower()
    assert "стоимост" in blob
    assert "срок" in blob
    assert "входит" in blob
    assert "расход" in blob
    assert not any(item.endswith("(") for item in items)


def test_buyer_checklist_issues_yandex_347bc2fc_bad() -> None:
    issues = buyer_checklist_issues(_yandex_347bc2fc(), _YANDEX_347BC2FC_BAD)
    assert "checklist:входит" in issues
    assert "checklist:расходы" in issues


def test_buyer_checklist_issues_yandex_347bc2fc_gold() -> None:
    assert buyer_checklist_issues(_yandex_347bc2fc(), _YANDEX_347BC2FC_GOLD) == []
    assert len(_YANDEX_347BC2FC_GOLD.strip()) <= 1000
