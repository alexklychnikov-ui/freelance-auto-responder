from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.store.db import get_connection, init_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ScanState:
    source_key: str
    platform: str
    last_scan_at: str | None = None
    last_known_project_id: str | None = None
    last_new_project_at: str | None = None


class ProjectRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def is_known(self, platform: str, source_key: str, project_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM projects
                WHERE platform = ? AND source_key = ? AND project_id = ?
                """,
                (platform, source_key, project_id),
            ).fetchone()
        return row is not None

    def insert_new(
        self,
        *,
        platform: str,
        source_key: str,
        project_id: str,
        title: str | None = None,
        url: str | None = None,
        published_at: str | None = None,
        status: str = "new",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO projects
                (platform, source_key, project_id, first_seen_at, published_at, status, title, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    source_key,
                    project_id,
                    _now_iso(),
                    published_at,
                    status,
                    title,
                    url,
                ),
            )

    def bootstrap_skip(
        self,
        *,
        platform: str,
        source_key: str,
        project_id: str,
        title: str | None = None,
        url: str | None = None,
        published_at: str | None = None,
    ) -> None:
        self.insert_new(
            platform=platform,
            source_key=source_key,
            project_id=project_id,
            title=title,
            url=url,
            published_at=published_at,
            status="skipped",
        )

    def update_status(
        self,
        platform: str,
        source_key: str,
        project_id: str,
        status: str,
        *,
        fit: bool | None = None,
        score: float | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE projects
                SET status = ?,
                    fit = COALESCE(?, fit),
                    score = COALESCE(?, score)
                WHERE platform = ? AND source_key = ? AND project_id = ?
                """,
                (
                    status,
                    int(fit) if fit is not None else None,
                    score,
                    platform,
                    source_key,
                    project_id,
                ),
            )

    def get_scan_state(self, source_key: str) -> ScanState | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scan_state WHERE source_key = ?",
                (source_key,),
            ).fetchone()
        if row is None:
            return None
        return ScanState(
            source_key=row["source_key"],
            platform=row["platform"],
            last_scan_at=row["last_scan_at"],
            last_known_project_id=row["last_known_project_id"],
            last_new_project_at=row["last_new_project_at"],
        )

    def set_scan_state(
        self,
        *,
        source_key: str,
        platform: str,
        last_scan_at: str | None = None,
        last_known_project_id: str | None = None,
        last_new_project_at: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO scan_state
                (source_key, platform, last_scan_at, last_known_project_id, last_new_project_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    platform = excluded.platform,
                    last_scan_at = COALESCE(excluded.last_scan_at, scan_state.last_scan_at),
                    last_known_project_id = COALESCE(
                        excluded.last_known_project_id, scan_state.last_known_project_id
                    ),
                    last_new_project_at = COALESCE(
                        excluded.last_new_project_at, scan_state.last_new_project_at
                    )
                """,
                (
                    source_key,
                    platform,
                    last_scan_at or _now_iso(),
                    last_known_project_id,
                    last_new_project_at,
                ),
            )

    def count_consecutive_known_from_top(
        self,
        platform: str,
        source_key: str,
        project_ids: list[str],
    ) -> int:
        count = 0
        for pid in project_ids:
            if self.is_known(platform, source_key, pid):
                count += 1
            else:
                break
        return count
