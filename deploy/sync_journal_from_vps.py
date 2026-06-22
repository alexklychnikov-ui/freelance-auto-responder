"""Скачать pending prepared_responses с VPS -> локальный journal.xlsx."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.journal.writer import JournalWriter, format_offer_notes
from src.responses.prepared_store import PreparedResponse

VPS = os.environ.get("FREELANCE_VPS_HOST", "LightRAG_Naive")
REMOTE_PREPARED = "/opt/freelance-responder/data/prepared_responses"
DEFAULT_JOURNAL = Path(
    r"C:\Python\Projects\Zerocode2md\ResponseJournal\journal.xlsx"
)
# Тестовые project_id — не писать в Excel (через запятую в JOURNAL_SKIP_PROJECT_IDS)
DEFAULT_SKIP_PROJECT_IDS = {"3202099"}


def _journal_path() -> Path:
    env_path = os.environ.get("RESPONSE_JOURNAL", "").strip()
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    dotenv = REPO / ".env"
    candidates: list[Path] = []
    if dotenv.exists():
        raw = dotenv.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                text = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            for line in text.splitlines():
                if line.startswith("RESPONSE_JOURNAL="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        candidates.append(Path(value))

    for path in candidates:
        if path.exists():
            return path

    journal_dir = DEFAULT_JOURNAL.parent
    if journal_dir.is_dir():
        matches = sorted(journal_dir.glob("*.xlsx"))
        if len(matches) == 1:
            return matches[0]

    if DEFAULT_JOURNAL.exists():
        return DEFAULT_JOURNAL
    return candidates[0] if candidates else DEFAULT_JOURNAL


def _skip_project_ids() -> set[str]:
    raw = os.environ.get("JOURNAL_SKIP_PROJECT_IDS", "").strip()
    ids = set(DEFAULT_SKIP_PROJECT_IDS)
    if raw:
        ids.update(part.strip() for part in raw.split(",") if part.strip())
    return ids


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def _pull_prepared(tmp: Path) -> list[Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            "scp",
            f"{VPS}:{REMOTE_PREPARED}/*.json",
            str(tmp),
        ],
        check=False,
    )
    if result.returncode != 0 and "No such file" not in (result.stderr or ""):
        if result.stderr:
            print(result.stderr.strip())
        if result.stdout:
            print(result.stdout.strip())
    return sorted(tmp.glob("*.json"))


def _mark_exported_on_vps(filename: str) -> None:
    script = (
        "import json; from pathlib import Path; "
        f"p=Path('{REMOTE_PREPARED}')/'{filename}'; "
        "d=json.loads(p.read_text(encoding='utf-8')); "
        "d['journal_exported']=True; "
        "p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8'); "
        "print('marked', p.name)"
    )
    _run(["ssh", VPS, f"python3 -c \"{script}\""])


def main() -> int:
    journal_path = _journal_path()
    if not journal_path.exists():
        print(f"FAIL: файл не найден: {journal_path}")
        return 1

    writer = JournalWriter(journal_path)
    existing_ids = writer.project_ids_in_journal()
    skip_ids = _skip_project_ids()

    with tempfile.TemporaryDirectory(prefix="journal_sync_") as tmp_dir:
        files = _pull_prepared(Path(tmp_dir))
        pending: list[tuple[str, PreparedResponse]] = []
        for path in files:
            try:
                item = PreparedResponse.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, ValueError, KeyError) as exc:
                print(f"skip corrupt {path.name}: {exc}")
                continue

            if item.project_id in skip_ids:
                continue

            in_journal = item.project_id in existing_ids
            notes = format_offer_notes(
                item.title,
                price=item.price,
                delivery_days=item.delivery_days,
            )

            if item.journal_exported and in_journal:
                if writer.update_notes_by_project_id(item.project_id, notes):
                    print(f"OK notes: {item.title}")
                continue
            if item.journal_exported and not in_journal:
                print(
                    f"re-sync {path.name}: exported на VPS, но нет в Excel"
                )
            pending.append((path.name, item))

        if not pending:
            print("Нет новых откликов для Excel.")
            print(f"Файл: {journal_path}")
            if existing_ids:
                print(
                    "Если строки не видно — закрой Excel полностью и открой файл заново "
                    "(открытый Excel не подхватывает изменения с диска)."
                )
            return 0

        for filename, item in pending:
            row = writer.append_prepared(
                item.project,
                item.score,
                item.response_text,
                price=item.price,
                delivery_days=item.delivery_days,
            )
            existing_ids.add(item.project_id)
            _mark_exported_on_vps(filename)
            print(f"OK row {row}: {item.title}")

        print(f"Готово: {len(pending)} строк -> {journal_path}")
        print(
            "Закрой Excel, если был открыт, затем открой journal.xlsx из проводника."
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
