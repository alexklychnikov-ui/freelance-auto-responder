from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.store.scan_reports import ScanCycleStats, ScanReportStore
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


def test_format_scan_reports_message_empty() -> None:
    text = format_scan_reports_message([], timezone_name="Asia/Irkutsk")
    assert "нет данных" in text.lower()


def test_format_scan_reports_message_with_rows(store: ScanReportStore) -> None:
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
