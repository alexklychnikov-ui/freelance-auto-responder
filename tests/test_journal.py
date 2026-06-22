from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.journal.writer import JOURNAL_COLUMNS, JournalWriter
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
    wb.close()


def test_journal_append_row(journal_path: Path, project: ProjectFull, score: GptScoreResult) -> None:
    writer = JournalWriter(journal_path)
    row = writer.append_submission(project, score, "Текст отклика для теста")

    wb = load_workbook(journal_path)
    ws = wb.active
    assert row == 2
    assert ws.cell(row=2, column=2).value == date.today().isoformat()
    assert ws.cell(row=2, column=3).value == "Kwork"
    assert ws.cell(row=2, column=4).value == project.url
    assert ws.cell(row=2, column=5).value == "Telegram-бот"
    assert ws.cell(row=2, column=6).value == "Отправлен"
    wb.close()


def test_journal_skips_merged_cells(
    journal_path: Path, project: ProjectFull, score: GptScoreResult
) -> None:
    wb = load_workbook(journal_path)
    ws = wb.active
    ws.merge_cells("A2:H2")
    wb.save(journal_path)
    wb.close()

    writer = JournalWriter(journal_path)
    row = writer.append_prepared(project, score, "prepared text", price="5000", delivery_days=14)

    assert row == 3
    wb = load_workbook(journal_path)
    ws = wb.active
    assert ws.cell(row=3, column=6).value == "Подготовлен"
    assert "Test project" in str(ws.cell(row=3, column=8).value)
    assert "5000" in str(ws.cell(row=3, column=8).value)
    wb.close()


def test_format_offer_notes() -> None:
    from src.journal.writer import format_offer_notes

    text = format_offer_notes("Сайт", price="8000", delivery_days=10)
    assert text == "Сайт\nЦена: 8000 ₽ · Срок: 10 дн."


def test_project_ids_in_journal(journal_path: Path, project: ProjectFull, score: GptScoreResult) -> None:
    writer = JournalWriter(journal_path)
    assert writer.project_ids_in_journal() == set()

    writer.append_prepared(project, score, "text", price="1000")
    assert writer.project_ids_in_journal() == {"100"}


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
