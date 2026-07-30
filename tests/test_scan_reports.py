from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.store.scan_reports import ScanCycleStats, ScanReport, ScanReportStore
from src.telegram_bot.scan_report import format_scan_reports_message


@pytest.fixture
def store(tmp_path: Path) -> ScanReportStore:
    return ScanReportStore(tmp_path / "test.db")


def test_scan_report_store_roundtrip(store: ScanReportStore) -> None:
    stats = ScanCycleStats(
        seen=20,
        checked=3,
        rejected_stack=2,
        rejected_budget=1,
        notified=0,
    )
    store.save(
        stats,
        scanned_at=datetime(2025, 6, 26, 2, 30, tzinfo=timezone.utc),
    )
    reports = store.list_recent(limit=3)
    assert len(reports) == 1
    assert reports[0].seen == 20
    assert reports[0].checked == 3
    assert reports[0].rejected_stack == 2
    assert reports[0].rejected_budget == 1
    assert reports[0].by_platform == {}


def test_scan_report_store_by_platform_roundtrip(store: ScanReportStore) -> None:
    totals = ScanCycleStats(
        seen=60,
        checked=5,
        rejected_stack=1,
        rejected_budget=1,
        notified=3,
    )
    by_platform = {
        "kwork": ScanCycleStats(
            seen=50, checked=2, rejected_stack=0, rejected_budget=0, notified=2
        ),
        "yandex_uslugi": ScanCycleStats(
            seen=10, checked=3, rejected_stack=1, rejected_budget=1, notified=1
        ),
    }
    store.save(
        totals,
        by_platform=by_platform,
        scanned_at=datetime(2026, 7, 30, 10, 50, tzinfo=timezone.utc),
    )
    report = store.list_recent(1)[0]
    assert report.seen == 60
    assert report.checked == 5
    assert set(report.by_platform) == {"kwork", "yandex_uslugi"}
    assert report.by_platform["kwork"].seen == 50
    assert report.by_platform["kwork"].notified == 2
    assert report.by_platform["yandex_uslugi"].checked == 3


def test_list_recent_skips_corrupt_platform_values(store: ScanReportStore) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO scan_reports (
                scanned_at, seen, checked, rejected_stack, rejected_budget,
                notified, platforms_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-30T10:50:00+00:00",
                10,
                1,
                0,
                0,
                1,
                '{"kwork": {"seen": "x", "checked": 1},'
                ' "flru": {"seen": 5, "checked": null},'
                ' "yandex_uslugi": {"seen": 3, "checked": 1,'
                ' "rejected_stack": 0, "rejected_budget": 0, "notified": 0}}',
            ),
        )
    report = store.list_recent(1)[0]
    assert set(report.by_platform) == {"yandex_uslugi"}
    assert report.by_platform["yandex_uslugi"].seen == 3
    assert report.checked == 1


def test_scan_report_store_migrates_old_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE scan_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at TEXT NOT NULL,
                seen INTEGER NOT NULL DEFAULT 0,
                checked INTEGER NOT NULL DEFAULT 0,
                rejected_stack INTEGER NOT NULL DEFAULT 0,
                rejected_budget INTEGER NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO scan_reports (
                scanned_at, seen, checked, rejected_stack, rejected_budget, notified
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("2025-06-26T02:30:00+00:00", 15, 4, 1, 2, 0),
        )
    store = ScanReportStore(db_path)
    reports = store.list_recent(1)
    assert len(reports) == 1
    assert reports[0].by_platform == {}
    assert reports[0].checked == 4


def test_format_scan_reports_message_empty() -> None:
    text = format_scan_reports_message([], timezone_name="Asia/Irkutsk")
    assert "нет данных" in text.lower()


def test_format_scan_reports_message_legacy(store: ScanReportStore) -> None:
    store.save(
        ScanCycleStats(seen=15, checked=4, rejected_stack=1, rejected_budget=2),
        scanned_at=datetime(2025, 6, 26, 2, 30, tzinfo=timezone.utc),
    )
    text = format_scan_reports_message(
        store.list_recent(1),
        timezone_name="Asia/Irkutsk",
    )
    assert "Проверено: 4" in text
    assert "не стек: 1" in text
    assert "не бюджет: 2" in text
    assert "10:30" in text
    assert "Итого:" not in text
    assert "• Kwork" not in text


def test_format_scan_reports_message_with_platforms() -> None:
    report = ScanReport(
        scanned_at="2026-07-30T10:50:00+00:00",
        seen=66,
        checked=2,
        rejected_stack=0,
        rejected_budget=0,
        notified=2,
        by_platform={
            "flru": ScanCycleStats(seen=6, checked=0),
            "kwork": ScanCycleStats(
                seen=50, checked=2, rejected_stack=0, rejected_budget=0, notified=2
            ),
            "yandex_uslugi": ScanCycleStats(seen=10, checked=0),
        },
    )
    text = format_scan_reports_message([report], timezone_name="Asia/Irkutsk")
    assert "• Kwork — проверено 2 (лента 50)" in text
    assert "• Яндекс Услуги — проверено 0 (лента 10)" in text
    assert "• FL.ru — проверено 0 (лента 6)" in text
    assert "Итого: проверено 2 (лента 66)" in text
    assert "в TG 2" in text
    kwork_pos = text.index("• Kwork")
    yandex_pos = text.index("• Яндекс Услуги")
    flru_pos = text.index("• FL.ru")
    assert kwork_pos < yandex_pos < flru_pos
