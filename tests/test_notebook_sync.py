from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from openpyxl import Workbook

from src.journal.notebook_sync import (
    GS_HEADERS,
    JournalRow,
    decide_upsert,
    filter_rows,
    find_worksheet_ci,
    format_gs_date,
    iter_journal_rows,
    match_key,
    month_sheet_name,
    normalize_journal_link,
    parse_journal_date,
    short_order_title,
    sync_journal_to_notebook,
    window_bounds,
)
from src.journal.writer import JOURNAL_COLUMNS


def test_normalize_journal_link_kwork() -> None:
    assert (
        normalize_journal_link("https://kwork.ru/projects/123456/view?x=1")
        == "kwork:123456"
    )
    assert (
        normalize_journal_link("https://www.kwork.ru/projects/123456/")
        == "kwork:123456"
    )


def test_normalize_journal_link_flru_and_yandex() -> None:
    assert (
        normalize_journal_link("https://www.fl.ru/projects/5514790/title/")
        == "flru:5514790"
    )
    oid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert (
        normalize_journal_link(f"https://uslugi.yandex.ru/order/{oid}")
        == f"yandex:{oid}"
    )


def test_month_sheet_name_ru() -> None:
    assert month_sheet_name(date(2026, 9, 1)) == "Сентябрь 2026"
    assert month_sheet_name(date(2026, 10, 5)) == "Октябрь 2026"


def test_short_order_title_prefers_type() -> None:
    assert short_order_title("Telegram-бот", "Длинный текст заказа\nещё") == "Telegram-бот"
    assert short_order_title("", "Первая строка заказа\nвторая") == "Первая строка заказа"


def test_find_worksheet_ci() -> None:
    ws = MagicMock()
    ws.title = "октябрь 2026"
    ss = MagicMock()
    ss.worksheets.return_value = [ws]
    assert find_worksheet_ci(ss, "Октябрь 2026") is ws
    assert find_worksheet_ci(ss, "Ноябрь 2026") is None


def test_parse_journal_date_formats() -> None:
    assert parse_journal_date("05.10.2026") == date(2026, 10, 5)
    assert parse_journal_date("2026-09-15") == date(2026, 9, 15)
    assert parse_journal_date(date(2026, 9, 1)) == date(2026, 9, 1)
    assert parse_journal_date(None) is None


def test_match_key() -> None:
    assert match_key("kwork:1", date(2026, 9, 3)) == ("kwork:1", "03.09.2026")


def test_window_bounds_respects_min_date() -> None:
    start, end = window_bounds(
        10,
        today=date(2026, 9, 5),
        min_date=date(2026, 9, 1),
    )
    assert end == date(2026, 9, 5)
    assert start == date(2026, 9, 1)


def test_filter_rows_inclusive() -> None:
    rows = [
        JournalRow(
            response_date=date(2026, 9, 1),
            link="https://kwork.ru/projects/1",
            norm_link="kwork:1",
            vacancy="A",
            response_text="t1",
            comment="",
        ),
        JournalRow(
            response_date=date(2026, 8, 31),
            link="https://kwork.ru/projects/2",
            norm_link="kwork:2",
            vacancy="B",
            response_text="t2",
            comment="",
        ),
        JournalRow(
            response_date=date(2026, 9, 10),
            link="https://kwork.ru/projects/3",
            norm_link="kwork:3",
            vacancy="C",
            response_text="t3",
            comment="",
        ),
    ]
    out = filter_rows(rows, date(2026, 9, 1), date(2026, 9, 5))
    assert [r.norm_link for r in out] == ["kwork:1"]


def test_decide_upsert_insert_defaults() -> None:
    row = JournalRow(
        response_date=date(2026, 10, 1),
        link="https://kwork.ru/projects/9/view",
        norm_link="kwork:9",
        vacancy="Bot",
        response_text="Cover letter",
        comment="Client request text",
    )
    decision = decide_upsert(row, existing=None, next_number=7)
    assert decision.action == "insert"
    assert decision.values[0] == 7
    assert decision.values[1] == "Bot"
    assert decision.values[4] == "Отклик ушел"
    assert decision.values[5] == "Client request text"
    assert decision.values[6] == "Cover letter"
    assert decision.values[7] == ""


def test_decide_upsert_update_preserves_status_and_vip() -> None:
    row = JournalRow(
        response_date=date(2026, 10, 1),
        link="https://kwork.ru/projects/9/view",
        norm_link="kwork:9",
        vacancy="New title",
        response_text="NEW LETTER",
        comment="request from journal",
    )
    existing = [
        "3",
        "Old vacancy",
        "https://kwork.ru/projects/9/view",
        "01.10.2026",
        "В работе",
        "old request",
        "OLD LETTER",
        "TRUE",
    ]
    decision = decide_upsert(row, existing=existing, sheet_row=5)
    assert decision.action == "update"
    assert decision.values[1] == "Old vacancy"  # vacancy kept when filled
    assert decision.values[4] == "В работе"
    assert decision.values[5] == "request from journal"  # request always refreshed
    assert decision.values[6] == "NEW LETTER"
    assert decision.values[7] == "TRUE"


def test_decide_upsert_fills_empty_status_default() -> None:
    row = JournalRow(
        response_date=date(2026, 10, 1),
        link="https://kwork.ru/projects/9/view",
        norm_link="kwork:9",
        vacancy="V",
        response_text="NEW",
        comment="C",
    )
    existing = [
        "3",
        "V",
        "https://kwork.ru/projects/9/view",
        "01.10.2026",
        "",
        "C",
        "OLD",
        "",
    ]
    decision = decide_upsert(row, existing=existing, sheet_row=5)
    assert decision.action == "update"
    assert decision.values[4] == "Отклик ушел"
    assert decision.values[6] == "NEW"


def test_decide_upsert_fills_empty_comment_and_vacancy() -> None:
    row = JournalRow(
        response_date=date(2026, 10, 1),
        link="https://kwork.ru/projects/9/view",
        norm_link="kwork:9",
        vacancy="Filled vacancy",
        response_text="L",
        comment="Filled comment",
    )
    existing = [
        "3",
        "",
        "https://kwork.ru/projects/9/view",
        "01.10.2026",
        "Отклик ушел",
        "",
        "old",
        "",
    ]
    decision = decide_upsert(row, existing=existing, sheet_row=4)
    assert decision.action == "update"
    assert decision.values[1] == "Filled vacancy"
    assert decision.values[5] == "Filled comment"
    assert decision.values[6] == "L"


def test_decide_upsert_skip_when_unchanged() -> None:
    row = JournalRow(
        response_date=date(2026, 10, 1),
        link="https://kwork.ru/projects/9/view",
        norm_link="kwork:9",
        vacancy="V",
        response_text="same",
        comment="c",
    )
    existing = [
        "1",
        "V",
        "https://kwork.ru/projects/9/view",
        "01.10.2026",
        "Отклик ушел",
        "c",
        "same",
        "",
    ]
    decision = decide_upsert(row, existing=existing, sheet_row=2)
    assert decision.action == "skip"


def _write_mini_journal(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Отклики"
    for col, header in enumerate(JOURNAL_COLUMNS, start=1):
        ws.cell(row=1, column=col, value=header)
    ws.cell(row=2, column=1, value=1)
    ws.cell(row=2, column=2, value=date(2026, 9, 15))
    ws.cell(row=2, column=3, value="Kwork")
    ws.cell(row=2, column=4, value="https://kwork.ru/projects/555/view")
    ws.cell(row=2, column=5, value="Telegram-бот")
    ws.cell(row=2, column=7, value="Жду ответа")
    ws.cell(row=2, column=8, value="Нужен бот для заявок\nдетали")
    ws.cell(row=2, column=9, value="Текст отклика")
    wb.save(path)
    wb.close()


def test_iter_journal_rows(tmp_path: Path) -> None:
    path = tmp_path / "j.xlsx"
    _write_mini_journal(path)
    rows = iter_journal_rows(path)
    assert len(rows) == 1
    assert rows[0].norm_link == "kwork:555"
    assert rows[0].response_date == date(2026, 9, 15)
    assert rows[0].vacancy == "Telegram-бот"  # Тип проекта → short title
    assert rows[0].comment.startswith("Нужен бот")  # Предложения → request
    assert rows[0].response_text == "Текст отклика"


def test_sync_dry_run_with_mock_spreadsheet(tmp_path: Path) -> None:
    path = tmp_path / "j.xlsx"
    _write_mini_journal(path)

    ws = MagicMock()
    ws.get_all_values.return_value = [list(GS_HEADERS)]
    ss = MagicMock()
    ss.worksheets.return_value = []
    ss.add_worksheet.return_value = ws

    result = sync_journal_to_notebook(
        path,
        n_days=60,
        min_date=date(2026, 9, 1),
        dry_run=True,
        spreadsheet=ss,
        tz_name="Asia/Irkutsk",
    )
    assert result.dry_run is True
    assert result.added == 1
    assert result.updated == 0
    assert not result.errors
    ss.add_worksheet.assert_not_called()
    ws.append_rows.assert_not_called()
    ws.update.assert_not_called()


def test_format_gs_date() -> None:
    assert format_gs_date(date(2026, 9, 1)) == "01.09.2026"
