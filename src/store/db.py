from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    platform TEXT NOT NULL,
    source_key TEXT NOT NULL,
    project_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    published_at TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    fit INTEGER,
    score REAL,
    title TEXT,
    url TEXT,
    PRIMARY KEY (platform, source_key, project_id)
);

CREATE TABLE IF NOT EXISTS scan_state (
    source_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    last_scan_at TEXT,
    last_known_project_id TEXT,
    last_new_project_at TEXT
);
"""


def init_db(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
