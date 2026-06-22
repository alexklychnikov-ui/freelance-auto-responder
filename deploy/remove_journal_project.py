"""Удалить строку из journal.xlsx по Kwork project_id."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from deploy.sync_journal_from_vps import _journal_path
from src.journal.writer import JournalWriter


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python deploy/remove_journal_project.py <project_id>")
        return 1
    project_id = sys.argv[1].strip()
    path = _journal_path()
    writer = JournalWriter(path)
    if writer.remove_row_by_project_id(project_id):
        print(f"OK: удалена строка project_id={project_id} из {path}")
        return 0
    print(f"FAIL: project_id={project_id} не найден в {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
