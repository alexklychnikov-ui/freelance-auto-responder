from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.store.db import get_connection, init_db

SCAN_REPORTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    checked INTEGER NOT NULL DEFAULT 0,
    rejected_stack INTEGER NOT NULL DEFAULT 0,
    rejected_budget INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class ScanCycleStats:
    seen: int = 0
    checked: int = 0
    rejected_stack: int = 0
    rejected_budget: int = 0
    notified: int = 0

    def merge(self, other: ScanCycleStats) -> None:
        self.seen += other.seen
        self.checked += other.checked
        self.rejected_stack += other.rejected_stack
        self.rejected_budget += other.rejected_budget
        self.notified += other.notified


@dataclass(frozen=True)
class ScanReport:
    scanned_at: str
    seen: int
    checked: int
    rejected_stack: int
    rejected_budget: int
    notified: int


class ScanReportStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        init_db(self.db_path)
        self._ensure_table()

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCAN_REPORTS_SCHEMA)

    def save(self, stats: ScanCycleStats, *, scanned_at: datetime | None = None) -> None:
        when = scanned_at or datetime.now(timezone.utc)
        scanned_at_iso = when.replace(microsecond=0).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO scan_reports (
                    scanned_at, seen, checked, rejected_stack, rejected_budget, notified
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    scanned_at_iso,
                    stats.seen,
                    stats.checked,
                    stats.rejected_stack,
                    stats.rejected_budget,
                    stats.notified,
                ),
            )
            conn.execute(
                """
                DELETE FROM scan_reports
                WHERE id NOT IN (
                    SELECT id FROM scan_reports ORDER BY id DESC LIMIT 50
                )
                """
            )

    def list_recent(self, limit: int = 3) -> list[ScanReport]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT scanned_at, seen, checked, rejected_stack, rejected_budget, notified
                FROM scan_reports
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ScanReport(
                scanned_at=row["scanned_at"],
                seen=int(row["seen"]),
                checked=int(row["checked"]),
                rejected_stack=int(row["rejected_stack"]),
                rejected_budget=int(row["rejected_budget"]),
                notified=int(row["notified"]),
            )
            for row in rows
        ]
