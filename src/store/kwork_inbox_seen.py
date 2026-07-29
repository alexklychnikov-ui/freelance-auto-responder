from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_seen (
    interlocutor_id TEXT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    last_mid INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

BOOTSTRAP_KEY = "bootstrap_done"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class InboxSeenRow:
    interlocutor_id: str
    username: str
    last_mid: int
    updated_at: str


class KworkInboxSeenStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def is_bootstrapped(self) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?",
                (BOOTSTRAP_KEY,),
            ).fetchone()
        return bool(row and str(row["value"]).strip() in ("1", "true", "yes"))

    def mark_bootstrap_done(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (BOOTSTRAP_KEY, "1"),
            )

    def get_last_mid(self, interlocutor_id: str) -> int | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_mid FROM inbox_seen WHERE interlocutor_id = ?",
                (str(interlocutor_id),),
            ).fetchone()
        if row is None:
            return None
        return int(row["last_mid"])

    def set_last_mid(
        self,
        interlocutor_id: str,
        username: str,
        last_mid: int,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO inbox_seen (interlocutor_id, username, last_mid, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(interlocutor_id) DO UPDATE SET
                    username = excluded.username,
                    last_mid = excluded.last_mid,
                    updated_at = excluded.updated_at
                """,
                (
                    str(interlocutor_id),
                    username or "",
                    int(last_mid),
                    _now_iso(),
                ),
            )

    def list_all(self) -> list[InboxSeenRow]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT interlocutor_id, username, last_mid, updated_at
                FROM inbox_seen
                ORDER BY interlocutor_id
                """
            ).fetchall()
        return [
            InboxSeenRow(
                interlocutor_id=str(r["interlocutor_id"]),
                username=str(r["username"] or ""),
                last_mid=int(r["last_mid"]),
                updated_at=str(r["updated_at"]),
            )
            for r in rows
        ]
