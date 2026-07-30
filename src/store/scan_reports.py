from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
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
    notified INTEGER NOT NULL DEFAULT 0,
    platforms_json TEXT
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


def stats_to_dict(stats: ScanCycleStats) -> dict[str, int]:
    return {
        "seen": stats.seen,
        "checked": stats.checked,
        "rejected_stack": stats.rejected_stack,
        "rejected_budget": stats.rejected_budget,
        "notified": stats.notified,
    }


def stats_from_dict(data: dict) -> ScanCycleStats:
    return ScanCycleStats(
        seen=int(data.get("seen", 0)),
        checked=int(data.get("checked", 0)),
        rejected_stack=int(data.get("rejected_stack", 0)),
        rejected_budget=int(data.get("rejected_budget", 0)),
        notified=int(data.get("notified", 0)),
    )


@dataclass(frozen=True)
class ScanReport:
    scanned_at: str
    seen: int
    checked: int
    rejected_stack: int
    rejected_budget: int
    notified: int
    by_platform: dict[str, ScanCycleStats] = field(default_factory=dict)


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
            cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(scan_reports)").fetchall()
            }
            if "platforms_json" not in cols:
                conn.execute(
                    "ALTER TABLE scan_reports ADD COLUMN platforms_json TEXT"
                )

    def save(
        self,
        stats: ScanCycleStats,
        *,
        by_platform: dict[str, ScanCycleStats] | None = None,
        scanned_at: datetime | None = None,
    ) -> None:
        when = scanned_at or datetime.now(timezone.utc)
        scanned_at_iso = when.replace(microsecond=0).isoformat()
        platforms_json = None
        if by_platform:
            platforms_json = json.dumps(
                {plat: stats_to_dict(s) for plat, s in by_platform.items()},
                ensure_ascii=False,
            )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO scan_reports (
                    scanned_at, seen, checked, rejected_stack, rejected_budget,
                    notified, platforms_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scanned_at_iso,
                    stats.seen,
                    stats.checked,
                    stats.rejected_stack,
                    stats.rejected_budget,
                    stats.notified,
                    platforms_json,
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
                SELECT scanned_at, seen, checked, rejected_stack, rejected_budget,
                       notified, platforms_json
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
                by_platform=_parse_platforms_json(row["platforms_json"]),
            )
            for row in rows
        ]


def _parse_platforms_json(raw: str | None) -> dict[str, ScanCycleStats]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, ScanCycleStats] = {}
    for plat, payload in data.items():
        if not isinstance(payload, dict):
            continue
        try:
            result[str(plat)] = stats_from_dict(payload)
        except (TypeError, ValueError):
            continue
    return result
