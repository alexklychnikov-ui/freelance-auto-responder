from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS early_ping_seen (
    platform TEXT NOT NULL,
    project_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (platform, project_id)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class EarlyPingSeenStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def mark_if_new(self, platform: str, project_id: str) -> bool:
        """Return True if this is the first early ping for the project."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO early_ping_seen
                (platform, project_id, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (platform, str(project_id), _now_iso()),
            )
        return cur.rowcount == 1
