from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.journal.writer import (
    JOURNAL_COLUMNS,
    JournalWriter,
    extract_journal_project_id,
    relevance_tier,
    response_kind_label,
)
from src.models import GptScoreResult, ProjectFull


@pytest.fixture
def journal_path(tmp_path: Path) -> Path:
    path = tmp_path / "journal.xlsx"
    JournalWriter.create_template_copy(path)
    return path


@pytest.fixture
def project() -> ProjectFull:
    return ProjectFull(
        platform="kwork",
        source_key="kwork_dev_it",
        project_id="100",
        url="https://kwork.ru/projects/100",
        title="Test project",
        full_description="desc",
        desired_budget="15000",
        max_budget="20000",
    )


@pytest.fixture
def score() -> GptScoreResult:
    return GptScoreResult(
        score=8,
        fit=True,
        reason="ok",
        matched_skills=["Python"],
        risks=[],
        suggested_project_type="Telegram-бот",
        competition_level="low",
        recommendation="откликаться",
    )


def test_journal_template_has_columns(journal_path: Path) -> None:
    wb = load_workbook(journal_path)
    ws = wb.active
    headers = [ws.cell(row=1, column=c).value for c in range(1, len(JOURNAL_COLUMNS) + 1)]
    assert headers == JOURNAL_COLUMNS
    assert len(JOURNAL_COLUMNS) == 15
    assert JOURNAL_COLUMNS[9:15] == [
        "Бюджет заказчика",
        "Моя цена",
        "Релевантность",
        "Похожий кейс",
        "Тип отклика",
        "Причина отказа",
    ]
    assert ws.column_dimensions["H"].width == 73
    assert ws.column_dimensions["I"].width == 73
    wb.close()


def test_journal_append_row(journal_path: Path, project: ProjectFull, score: GptScoreResult) -> None:
    writer = JournalWriter(journal_path)
    row = writer.append_submission(project, score, "Текст отклика для теста")

    wb = load_workbook(journal_path)
    ws = wb.active
    assert row == 2
    cell_date = ws.cell(row=2, column=2).value
    expected = date.today()
    if isinstance(cell_date, datetime):
        assert cell_date.date() == expected
    else:
        assert cell_date == expected.isoformat()
    assert ws.cell(row=2, column=3).value == "Kwork"
    assert ws.cell(row=2, column=4).value == project.url
    assert ws.cell(row=2, column=4).hyperlink is not None
    assert ws.cell(row=2, column=5).value == "Telegram-бот"
    assert ws.cell(row=2, column=6).value == "Отправлен"
    wb.close()


def test_append_prepared_fills_kpi_columns(
    journal_path: Path, project: ProjectFull, score: GptScoreResult
) -> None:
    personal = (
        "По задаче с Telegram-ботом сделаю приём заявок, админку и выгрузку в Google Sheets. "
        "Сначала подключу webhook и авторизацию, затем формы и уведомления менеджеру. "
        "Могу показать похожий кейс по боту для заявок в чате."
    )
    assert len(personal) > 200
    writer = JournalWriter(journal_path)
    writer.append_prepared(project, score, personal, price="12000", delivery_days=7)

    wb = load_workbook(journal_path)
    ws = wb.active
    assert ws.cell(row=2, column=1).value == 1
    assert ws.cell(row=2, column=6).value == "Подготовлен"
    assert ws.cell(row=2, column=7).value == "Жду ответа"
    assert ws.cell(row=2, column=10).value == "15000"
    assert ws.cell(row=2, column=11).value == "12000"
    assert ws.cell(row=2, column=12).value == "A"
    assert ws.cell(row=2, column=13).value == "Да"
    assert ws.cell(row=2, column=14).value == "Персональный"
    assert ws.cell(row=2, column=15).value in (None, "")
    wb.close()


def test_relevance_tier_and_response_kind() -> None:
    high = GptScoreResult(
        score=8,
        fit=True,
        reason="ok",
        matched_skills=["Python"],
        risks=[],
        suggested_project_type="AI/RAG",
        competition_level="low",
        recommendation="откликаться",
    )
    mid = high.model_copy(update={"score": 5, "fit": False, "matched_skills": []})
    low = high.model_copy(update={"score": 3, "fit": False, "matched_skills": []})
    assert relevance_tier(high) == "A"
    assert relevance_tier(mid) == "B"
    assert relevance_tier(low) == "C"
    assert relevance_tier(low, acceptance_tier="quick_win") == "B"
    assert response_kind_label("короткий текст") == "Шаблон"
    assert response_kind_label("Сделаю парсер и demo в чате " + "детали " * 40) == (
        "Персональный + demo"
    )


def test_journal_skips_merged_cells(
    journal_path: Path, project: ProjectFull, score: GptScoreResult
) -> None:
    wb = load_workbook(journal_path)
    ws = wb.active
    ws.merge_cells("A2:I2")
    wb.save(journal_path)
    wb.close()

    writer = JournalWriter(journal_path)
    row = writer.append_prepared(project, score, "prepared text", price="5000", delivery_days=14)

    assert row == 3
    wb = load_workbook(journal_path)
    ws = wb.active
    assert ws.cell(row=3, column=6).value == "Подготовлен"
    assert ws.cell(row=3, column=8).value == "desc"
    assert "prepared text" in str(ws.cell(row=3, column=9).value)
    assert "5000" in str(ws.cell(row=3, column=9).value)
    wb.close()


def test_infer_project_type() -> None:
    from src.journal.writer import infer_project_type

    assert infer_project_type("Доработать 2 Telegram-бота") == "Telegram-бот"
    assert infer_project_type("Парсер объявлений") == "Парсинг"
    assert infer_project_type("Сайт на WordPress") == "Веб-MVP"
    assert infer_project_type("Что-то непонятное") == "Другое"


def test_repair_row_fills_type_and_hyperlink(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal.xlsx"
    JournalWriter.create_template_copy(journal_path)
    wb = load_workbook(journal_path)
    ws = wb.active
    ws.cell(row=2, column=4, value="https://kwork.ru/projects/3204427")
    ws.cell(row=2, column=5, value="—")
    ws.cell(row=2, column=8, value="Доработать 2 Telegram-бота по готовому ТЗ")
    wb.save(journal_path)
    wb.close()

    writer = JournalWriter(journal_path)
    assert writer.repair_row_by_project_id("3204427") is True

    wb = load_workbook(journal_path)
    ws = wb.active
    assert ws.cell(row=2, column=4).hyperlink is not None
    assert ws.cell(row=2, column=5).value == "Telegram-бот"
    wb.close()


def test_format_response_payload() -> None:
    from src.journal.writer import format_response_payload

    text = format_response_payload("Сделаю за вечер", price="8000", delivery_days=10)
    assert text == "Отклик\nСделаю за вечер\n\nОбщий бюджет: 8000 ₽\nСрок: 10 дн."


def test_project_ids_in_journal(journal_path: Path, project: ProjectFull, score: GptScoreResult) -> None:
    writer = JournalWriter(journal_path)
    assert writer.project_ids_in_journal() == set()

    writer.append_prepared(project, score, "text", price="1000")
    assert writer.project_ids_in_journal() == {"100"}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("tz://tz_123", "tz_123"),
        ("tz_123", "tz_123"),
        ("prefix tz_1785490763952 suffix", "tz_1785490763952"),
    ],
)
def test_extract_journal_project_id_tz(text: str, expected: str) -> None:
    assert extract_journal_project_id(text) == expected


def test_project_ids_in_journal_tz_url(journal_path: Path, score: GptScoreResult) -> None:
    project = ProjectFull(
        platform="telegram",
        source_key="tz_manual",
        project_id="tz_1785490763952",
        url="tz://tz_1785490763952",
        title="TZ project",
        full_description="desc",
    )
    writer = JournalWriter(journal_path)
    writer.append_prepared(project, score, "tz text", price="1000")
    assert writer.project_ids_in_journal() == {"tz_1785490763952"}


def test_set_url_cell_empty_has_no_hyperlink(journal_path: Path) -> None:
    wb = load_workbook(journal_path)
    ws = wb.active
    JournalWriter._set_url_cell(ws, 2, "")
    assert ws.cell(row=2, column=4).value is None
    assert ws.cell(row=2, column=4).hyperlink is None
    wb.close()


def test_project_ids_in_journal_yandex_and_flru(tmp_path: Path) -> None:
    from src.adapters.flru_urls import flru_project_url
    from src.adapters.yandex_urls import yandex_order_url

    journal_path = tmp_path / "journal.xlsx"
    JournalWriter.create_template_copy(journal_path)
    writer = JournalWriter(journal_path)

    yandex_id = "342870b9-bea4-40b7-a616-81061dc845db"
    flru_id = "5514795"

    wb = load_workbook(journal_path)
    ws = wb.active
    ws.cell(row=2, column=4, value=yandex_order_url(yandex_id))
    ws.cell(row=3, column=4, value=flru_project_url(flru_id))
    wb.save(journal_path)
    wb.close()

    assert writer.project_ids_in_journal() == {yandex_id, flru_id}


def test_update_status_by_project_id(
    journal_path: Path, project: ProjectFull, score: GptScoreResult
) -> None:
    writer = JournalWriter(journal_path)
    writer.append_prepared(project, score, "text", price="1000")

    changed = writer.update_status_by_project_id(
        project.project_id,
        status="Отправлен",
        result="Жду ответа",
    )
    assert changed is True

    wb = load_workbook(journal_path)
    ws = wb.active
    assert ws.cell(row=2, column=6).value == "Отправлен"
    assert ws.cell(row=2, column=7).value == "Жду ответа"
    wb.close()

    assert (
        writer.update_status_by_project_id(
            project.project_id,
            status="Отправлен",
            result="Жду ответа",
        )
        is False
    )


def test_journal_template_header_row(
    tmp_path: Path, project: ProjectFull, score: GptScoreResult
) -> None:
    from openpyxl import Workbook

    path = tmp_path / "template.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.cell(row=4, column=2, value="Дата отклика")
    ws.cell(row=4, column=1, value="№")
    ws.cell(row=5, column=1, value=1)
    ws.cell(row=5, column=2, value=date.today())
    ws.cell(row=5, column=4, value="https://example.com/1")
    wb.save(path)
    wb.close()

    writer = JournalWriter(path)
    row = writer.append_prepared(project, score, "text", price="1000")

    assert row == 6
    wb = load_workbook(path)
    ws = wb.active
    assert ws.cell(row=6, column=1).value == 2
    assert ws.cell(row=6, column=3).value == "Kwork"
    wb.close()

def test_append_prepared_custom_status(journal_path: Path, project: ProjectFull, score: GptScoreResult) -> None:
    writer = JournalWriter(journal_path)
    writer.append_prepared(
        project,
        score,
        "text",
        price="1000",
        status="Отправлен",
        result="Жду ответа",
    )

    wb = load_workbook(journal_path)
    ws = wb.active
    assert ws.cell(row=2, column=6).value == "Отправлен"
    assert ws.cell(row=2, column=7).value == "Жду ответа"
    wb.close()


def test_journal_status_for_confirm() -> None:
    from src.pipeline.manual_copy import journal_status_for_confirm

    assert journal_status_for_confirm("yandex_uslugi") == ("Отправлен", "Жду ответа")
    assert journal_status_for_confirm("flru") == ("Отправлен", "Жду ответа")
    assert journal_status_for_confirm("kwork") == ("Подготовлен", "Жду ответа")

