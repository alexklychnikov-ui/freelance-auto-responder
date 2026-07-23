from src.analyzer.response_text import strip_response_markdown
from src.adapters.kwork_delivery import KWORK_DELIVERY_DAY_OPTIONS, snap_delivery_days
from src.adapters.kwork import (
    _is_weak_description,
    merge_preview_into_full,
    parse_listing_from_html,
    parse_project_from_html,
)
from src.models import ProjectFull, ProjectPreview


def test_strip_response_markdown() -> None:
    assert strip_response_markdown("**Выделение** текста") == "Выделение текста"
    assert strip_response_markdown("обычный __жирный__ текст") == "обычный жирный текст"
    assert strip_response_markdown("хвост **") == "хвост"


def test_snap_delivery_days() -> None:
    assert snap_delivery_days(9) == 10
    assert snap_delivery_days(8) == 7
    assert snap_delivery_days(1) == 1
    assert snap_delivery_days(60) == 60
    assert snap_delivery_days(100) == 60
    assert set(KWORK_DELIVERY_DAY_OPTIONS) == {1, 2, 3, 4, 5, 6, 7, 10, 14, 21, 30, 60}


def test_parse_project_meta_from_plain_text() -> None:
    html = """
    <html><body>
    <h1>Выгрузка продаж из SpeechXplore</h1>
    <div>Желаемый бюджет: до 1 000 ₽ Допустимый: до 3 000 ₽</div>
    <div>Предложений: 19</div>
    <a href="/user/Evdohaanna2403">Evdohaanna2403</a>
    <div>9 ч. 46 мин.</div>
  </body></html>
    """
    data = parse_project_from_html(html, project_id="3203479")
    assert data["title"] == "Выгрузка продаж из SpeechXplore"
    assert "1 000" in (data["desired_budget"] or "")
    assert "3 000" in (data["max_budget"] or "")
    assert data["offers_count"] == 19
    assert data["buyer"] == "Evdohaanna2403"
    assert data["time_left"]


def test_parse_project_dopustimy_not_stolen_by_desired() -> None:
    """3218832-like: max must be Допустимый 45k, not Желаемый 15k."""
    from src.adapters.kwork_pricing import parse_budget_ceiling_rub
    from src.models import ProjectFull

    html = """
    <html><body>
    <h1>Telegram-бот</h1>
    <div class="wants-card__description-text">Нужен бот под задачу.</div>
    <div>Желаемый бюджет: до 15 000 ₽</div>
    <div>Допустимый: до 45 000 ₽</div>
    <div>Предложений: 2</div>
  </body></html>
    """
    data = parse_project_from_html(html, project_id="3218832")
    assert "15 000" in (data["desired_budget"] or "")
    assert "45 000" in (data["max_budget"] or "")
    assert "15 000" not in (data["max_budget"] or "")
    project = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3218832",
        url="https://kwork.ru/projects/3218832",
        title=data["title"],
        full_description=data["full_description"] or "",
        desired_budget=data["desired_budget"],
        max_budget=data["max_budget"],
    )
    assert parse_budget_ceiling_rub(project) == 45_000


def test_parse_project_budget_do_only_no_labels() -> None:
    """Checko-like page: only «до 1 500 ₽» without желаемый/допустимый labels."""
    html = """
    <html><body>
    <h1>Интеграция Checko на Python</h1>
    <div class="wants-card__description-text">Нужен парсер и API.</div>
    <div>Бюджет до 1 500 ₽</div>
    <div>Предложений: 4</div>
  </body></html>
    """
    data = parse_project_from_html(html, project_id="3218308")
    assert "1 500" in (data["max_budget"] or "")
    assert data["desired_budget"]  # filled from max fallback
    assert "1 500" in (data["desired_budget"] or "")


def test_parse_project_budget_cena_do_colon() -> None:
    """Live Kwork wording: «Цена до: 1 500 ₽»."""
    html = """
    <html><body>
    <h1>Python</h1>
    <div>son-xlsx 16 07 2026.xlsx Цена до: 1 500 ₽ N Покупатель: nixserver</div>
  </body></html>
    """
    data = parse_project_from_html(html, project_id="3218308")
    assert data["max_budget"] == "до 1 500 ₽"
    assert "1 500" in (data["desired_budget"] or "")


def test_merge_preview_into_full() -> None:
    full = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1/view",
        title="",
        full_description="",
    )
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="1",
        url="https://kwork.ru/projects/1",
        title="Telegram-бот СДЭК",
        budget_text="до 5 000 ₽",
        responses_count=12,
    )
    merged = merge_preview_into_full(full, preview)
    assert merged.title == "Telegram-бот СДЭК"
    assert merged.offers_count == 12
    assert "5 000" in (merged.desired_budget or "")
    assert merged.max_budget == "до 5 000 ₽"


def test_merge_preview_keeps_page_title() -> None:
    full = ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3217158",
        url="https://kwork.ru/projects/3217158/view",
        title="Ретушь фото для каталога",
        full_description="Нужна цветокоррекция и удаление фона на 40 фото.",
        desired_budget="до 3 000 ₽",
        max_budget="до 5 000 ₽",
    )
    preview = ProjectPreview(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="3217158",
        url="https://kwork.ru/projects/3217158",
        title="Отметки пользователей в Telegram Stories",
        budget_text="до 15 000 ₽",
        responses_count=3,
    )
    merged = merge_preview_into_full(full, preview)
    assert merged.title == "Ретушь фото для каталога"
    assert "цветокоррекция" in merged.full_description
    assert merged.desired_budget == "до 3 000 ₽"
    assert merged.max_budget == "до 5 000 ₽"
    assert merged.offers_count == 3


def test_listing_prefers_title_link_over_junk_href() -> None:
    html = """
    <article class="project-card" data-project-id="999">
      <a href="https://kwork.ru/projects/999">Misleading related</a>
      <a href="https://kwork.ru/projects/3201949">
        <h2 class="project-card__title">Telegram-бот для парсинга</h2>
      </a>
      <span class="project-card__budget">до 5 000 ₽</span>
      <span class="project-card__responses" data-responses="16">16</span>
    </article>
    """
    cards = parse_listing_from_html(html)
    assert len(cards) == 1
    assert cards[0]["project_id"] == "3201949"
    assert cards[0]["title"] == "Telegram-бот для парсинга"


def test_is_weak_description() -> None:
    assert _is_weak_description("Title", "") is True
    assert _is_weak_description("Title", "Title") is True
    # title-as-desc even when longer than 40 chars
    long_title = "Нужен Telegram-бот для учёта заявок и CRM интеграции"
    assert len(long_title) > 40
    assert _is_weak_description(long_title, long_title) is True
    assert _is_weak_description(long_title, long_title.lower()) is True
    assert (
        _is_weak_description(
            "Ретушь фотографий для каталога маркетплейса",
            "Ретушь фотографий",
        )
        is True
    )
    assert (
        _is_weak_description(
            "Ретушь",
            "Нужна детальная цветокоррекция и ретушь портретов для маркетплейса.",
        )
        is False
    )


def test_parse_project_prefers_description_text_over_title_breakwords() -> None:
    title = "Telegram-бот для учёта заявок и CRM (короткий заголовок)"
    assert len(title) > 40
    long_desc = (
        "Нужен бот на Python/aiogram. "
        "1. Стоимость?\n"
        "2. Срок?\n"
        "3. Стек?\n"
        "4. Готовы ли смотреть код?\n"
        "5. Что в передаче?\n"
        "6. Есть ли опыт с CRM?\n"
        "7. Как поддержка?\n"
        "8. Нужен ли webhook?\n"
        "9. Деплой на VPS?\n"
        "10. Документация?\n"
    )
    assert len(long_desc) > 200
    html = f"""
    <main class="project-page" data-project-id="3217391">
      <div class="wants-card__header-title breakwords">
        <a href="/projects/3217391">{title}</a>
      </div>
      <div class="wants-card__description-text">{long_desc}</div>
      <span class="desired-budget">до 25 000 ₽</span>
    </main>
    """
    data = parse_project_from_html(html, project_id="3217391")
    assert data["title"] == title
    assert len(data["full_description"]) > 200
    assert "Стоимость" in data["full_description"]
    assert data["full_description"] != title

