"""Quick Yandex session + DB status on VPS."""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_enabled_sources, get_settings


def main() -> None:
    settings = get_settings()
    print("bootstrap_skip_pipeline:", settings.scan_bootstrap_skip_pipeline)
    src = next(s for s in get_enabled_sources(settings.sources_config_path) if s.id == "yandex_uslugi_it")
    print("source bootstrap:", src.bootstrap)

    db = settings.database_path or "data/responder.db"
    if os.path.exists(db):
        con = sqlite3.connect(db)
        rows = con.execute(
            "select status, count(*) from seen_projects where platform=? group by status",
            ("yandex_uslugi",),
        ).fetchall()
        total = con.execute(
            "select count(*) from seen_projects where platform=?",
            ("yandex_uslugi",),
        ).fetchone()[0]
        print("yandex_db_total:", total)
        print("yandex_db_by_status:", dict(rows) if rows else {})
        scan = con.execute(
            "select value from scan_state where platform=? and source_key=?",
            ("yandex_uslugi", "yandex_uslugi_it"),
        ).fetchone()
        print("scan_state:", scan[0] if scan else None)
        con.close()
    else:
        print("db missing:", db)


if __name__ == "__main__":
    main()
