"""Убрать заказ из очереди откликов (prepared + pending + DB rejected)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.store.repository import ProjectRepository
from src.telegram_bot.pending_store import PendingStore
from src.responses.prepared_store import PreparedResponseStore


def dismiss(project_id: str, *, base: Path | None = None) -> None:
    settings = get_settings()
    if base:
        prepared_dir = base / "data" / "prepared_responses"
        pending_dir = base / "data" / "pending_offers"
        db_path = base / "data" / "seen_projects.db"
    else:
        prepared_dir = Path(settings.prepared_responses_dir)
        pending_dir = Path("data/pending_offers")
        db_path = Path(settings.database_path)

    prepared = PreparedResponseStore(prepared_dir)
    pending = PendingStore(pending_dir)
    repo = ProjectRepository(db_path)

    removed_prepared = False
    for item in prepared.list_all():
        if item.project_id == project_id:
            path = prepared._path(item.platform, item.source_key, item.project_id)
            if path.exists():
                path.unlink()
                print("removed prepared", path.name)
                removed_prepared = True

    for offer in pending.list_all():
        if offer.project_id == project_id:
            offer.status = "rejected"
            pending.save(offer)
            print("pending -> rejected", offer.platform, offer.source_key, project_id)
            repo.update_status(
                offer.platform, offer.source_key, project_id, "rejected"
            )

    if not removed_prepared:
        matches = list(prepared_dir.glob(f"*_{project_id}.json"))
        for path in matches:
            path.unlink()
            print("removed prepared", path.name)
            removed_prepared = True

    row = repo._conn().execute(
        "SELECT platform, source_key FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row:
        repo.update_status(row["platform"], row["source_key"], project_id, "rejected")
        print("db -> rejected", project_id)

    if not removed_prepared:
        print("prepared not found", project_id)


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else ""
    if not pid:
        print("Usage: dismiss_prepared_offer.py <project_id>")
        raise SystemExit(1)
    dismiss(pid)
