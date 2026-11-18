from __future__ import annotations

from src.adapters.kwork import _strip_stage_title_leakage
from src.analyzer.response_text import (
    diversify_mini_proof,
    ensure_hello_greeting,
    finalize_response_text,
    payment_mismatch_issues,
    strip_github_links,
    strip_portfolio_footer,
    tz_requires_lead_only,
)
from src.models import ProjectFull


def _project(desc: str) -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Telegram-магазин",
        full_description=desc,
    )


def test_strip_stage_title_leakage_from_start() -> None:
    stages = [
        ("Анализ ТЗ и реализация основной части", 11000),
        ("Тестирование, правки и передача проекта", 9000),
    ]
    raw = (
        "Тестирование, правки и передача проекта"
        "Анализ ТЗ и реализация основной части"
        "Изучив ваш проект, готов взяться."
    )
    cleaned = _strip_stage_title_leakage(raw, stages)
    assert cleaned.startswith("Изучив")


def test_unescape_literal_backslash_n() -> None:
    text = (
        "Умею создавать агентов.\\n Важно не только агента.\n"
        "Я предлагаю систему.\\n Использую Python.\n"
        "Сроки гибкие.\\n\\nПо объёму — от 24 000 ₽."
    )
    out = finalize_response_text(text, _project("агенты память"))
    assert "\\n" not in out
    assert "Важно не только" in out
    assert "\n" in out


def test_strip_github_and_portfolio_footer() -> None:
    text = (
        "Кейс по боту.\n"
        "GitHub: github.com/alexklychnikov-ui (https://github.com/alexklychnikov-ui).\n"
        "Портфолио: https://portfolio.hayklyvibelexy.ru/"
    )
    out = finalize_response_text(text)
    assert "github" not in out.lower()
    assert "portfolio.hayklyvibelexy.ru" not in out


def test_payment_mismatch_when_tz_lead_only() -> None:
    project = _project("оформление заявки без онлайн-оплаты; заявка менеджеру")
    assert tz_requires_lead_only(project)
    issues = payment_mismatch_issues(
        project,
        "Настрою интеграцию с API платёжных систем.",
    )
    assert issues == ["tz:payment_not_required"]


def test_strip_portfolio_footer_only() -> None:
    text = "Готов обсудить.\n\nПортфолио: https://portfolio.hayklyvibelexy.ru/"
    assert strip_portfolio_footer(text) == "Готов обсудить."


def test_strip_github_links() -> None:
    text = "См. github.com/alexklychnikov-ui и https://github.com/foo/bar"
    assert strip_github_links(text) == "См. и"


def test_strip_hallucinated_irina_when_no_buyer() -> None:
    project = _project("Нужен парсер hh.ru")
    project = project.model_copy(update={"buyer": None})
    text = (
        "Ирина, здравствуйте! Предлагаю связать API hh.ru с ChatGPT. "
        "Сделаю поиск и анализ резюме. Срок — 14 дней, стоимость — от 120000 руб. "
        "Дайте знать, с чего удобнее начать."
    )
    out = finalize_response_text(text, project)
    assert out.lstrip().startswith("Здравствуйте!")
    assert "ирина" not in out.lower().split("здравствуйте", 1)[0]
    assert "\n\n" in out
    assert out.startswith("    ")
    assert all(line.startswith("    ") for line in out.split("\n") if line.strip())
    assert "hh.ru" in out


def test_keep_real_buyer_greeting() -> None:
    project = _project("бот").model_copy(update={"buyer": "Ирина · 80%"})
    text = "Ирина, здравствуйте! Сделаю бота под заявки. Срок — 5 дней."
    out = finalize_response_text(text, project)
    assert out.lstrip().startswith("Здравствуйте!")
    assert "Ирина, здравствуйте!" not in out
    assert out.startswith("    Здравствуйте!")


def test_buyer_first_name_rejects_ui_noise() -> None:
    from src.analyzer.response_text import buyer_first_name

    assert buyer_first_name("Чаты") is None
    assert buyer_first_name("Ирина · 80%") == "Ирина"


def test_reject_self_account_dump_as_buyer() -> None:
    from src.analyzer.response_text import buyer_first_name, finalize_response_text

    dump = (
        "Александр КлычниковУведомлений0Выйти из аккаунта"
        "Александр Клычниковalexander.klichnikov@yandex.ru"
    )
    assert buyer_first_name(dump) is None
    project = _project("AI картинки").model_copy(update={"buyer": dump})
    text = (
        "Александр, здравствуйте!\n\n"
        "Для вашего проекта по генерации AI-картинок сделаю тестовый вариант. "
        "Срок — 5–7 дней, стоимость от 199 ₽. Если ок — напишите."
    )
    out = finalize_response_text(text, project)
    assert out.lstrip().startswith("Здравствуйте!")
    assert "\n\n" in out
    assert out.startswith("    Здравствуйте!")
    assert all(line.startswith("    ") for line in out.split("\n") if line.strip())


def test_ensure_hello_greeting_idempotent() -> None:
    assert ensure_hello_greeting("Сделаю бота.") == "Здравствуйте!\nСделаю бота."
    assert ensure_hello_greeting("Здравствуйте! Уже есть.") == "Здравствуйте!\nУже есть."
    assert ensure_hello_greeting("Ирина, здравствуйте! Текст.") == "Здравствуйте!\nТекст."


def test_finalize_breaks_wall_into_blank_line_paragraphs() -> None:
    """Any Kwork response wall must get greeting / essence / details / CTA blocks."""
    project = _project("парсер API").model_copy(update={"project_id": "3270732"})
    wall = (
        "Здравствуйте! Сделаю парсер под вашу выгрузку и свяжу с API. "
        "Разложу на этапы: сбор, нормализация и отчёт в нужном формате. "
        "Стоимость — 5 000 ₽. Срок — 7 дней. "
        "Если подход ок — напишите, согласуем старт."
    )
    out = finalize_response_text(wall, project)
    paras = [p for p in out.split("\n\n") if p.strip()]
    assert len(paras) >= 3
    assert paras[0].strip().startswith("Здравствуйте!")
    assert all(p.startswith("    ") for p in paras)
    assert "Стоимость" in out
    # Blank lines between blocks — not a single wall.
    assert "\n\n" in out
    assert out.count("\n\n") >= 2


def test_diversify_mini_proof_stock_opener() -> None:
    text = (
        "Здравствуйте!\n"
        "Сделаю бота под FAQ и тикеты.\n"
        "Могу показать похожий demo/кейс в чате Kwork. Срок — 7 дн."
    )
    out = diversify_mini_proof(text, seed="kwork:3262983")
    assert "Могу показать похожий demo/кейс в чате Kwork" not in out
    assert ("в чате" in out.lower()) or ("в переписке" in out.lower())
    # Same seed → same variant
    assert diversify_mini_proof(text, seed="kwork:3262983") == out


def test_finalize_replaces_stock_mini_proof() -> None:
    project = _project("бот").model_copy(update={"project_id": "3262991"})
    text = (
        "Здравствуйте!\n"
        "Сделаю Telegram-бота оценки блюд.\n"
        "Могу показать близкий demo/кейс в чате Kwork. Срок — 5 дн."
    )
    out = finalize_response_text(text, project)
    assert "demo/кейс в чате Kwork" not in out
    assert "Здравствуйте!" in out


def test_finalize_clamps_yandex_to_1000_chars() -> None:
    from src.analyzer.response_text import clamp_yandex_response_length

    long = "А" * 1200
    assert len(clamp_yandex_response_length(long)) <= 1000

    project = ProjectFull(
        platform="yandex_uslugi",
        source_key="yandex_manual",
        project_id="6b4fe021-6dbf-4e3e-ade5-57e645f681b7",
        url="https://uslugi.yandex.ru/order/6b4fe021-6dbf-4e3e-ade5-57e645f681b7",
        title="Создать панель управления",
        full_description="панель",
    )
    para = (
        "Здравствуйте! Сделаю панель управления с навигацией и экранами. "
        * 40
    )
    out = finalize_response_text(para, project)
    assert len(out) <= 1000
    assert "Здравствуйте" in out
