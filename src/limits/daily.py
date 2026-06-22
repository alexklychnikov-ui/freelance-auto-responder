from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import load_workbook


def count_today_responses(journal_path: str | Path) -> int:
    path = Path(journal_path)
    if not path.exists():
        return 0

    today = date.today().isoformat()
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    count = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 2:
            continue
        if str(row[1]) == today:
            count += 1
    wb.close()
    return count


def is_daily_limit_reached(journal_path: str | Path, max_daily: int) -> bool:
    return count_today_responses(journal_path) >= max_daily
