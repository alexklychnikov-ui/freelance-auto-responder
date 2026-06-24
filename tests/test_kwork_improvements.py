from src.analyzer.response_text import strip_response_markdown
from src.adapters.kwork_delivery import KWORK_DELIVERY_DAY_OPTIONS, snap_delivery_days
from src.adapters.kwork import merge_preview_into_full, parse_project_from_html
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
