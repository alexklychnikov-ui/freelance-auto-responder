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

from src.config import get_settings
from src.journal.kwork_status_sync import sync_journal_from_kwork_offers
from src.journal.writer import JournalWriter, format_response_payload
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
    safe_name = Path(filename).name
    if safe_name != filename:
        raise ValueError(f"unsafe filename: {filename}")
    remote_file = f"{REMOTE_PREPARED}/{safe_name}"
    with tempfile.TemporaryDirectory(prefix="mark_exported_") as tmp_dir:
        local_path = Path(tmp_dir) / safe_name
        _run(["scp", f"{VPS}:{remote_file}", str(local_path)])
        data = json.loads(local_path.read_text(encoding="utf-8"))
        data["journal_exported"] = True
        local_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _run(["scp", str(local_path), f"{VPS}:{remote_file}"])
        print("marked", safe_name)


def _sync_kwork_offer_statuses(journal_path: Path) -> int:
    if os.environ.get("JOURNAL_SKIP_OFFERS_SYNC", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        print("Сверка kwork.ru/offers пропущена (JOURNAL_SKIP_OFFERS_SYNC).")
        return 0

    print("Сверка статусов с https://kwork.ru/offers ...")
    sync_result = sync_journal_from_kwork_offers(
        journal_path,
        settings=get_settings(),
    )
    if sync_result.error:
        print(f"WARN: сверка /offers не выполнена: {sync_result.error}")
        return 0
    print(
        f"Сверка /offers: обновлено {sync_result.updated}, добавлено {sync_result.appended}, "
        f"совпало {sync_result.matched}, пропущено {sync_result.skipped}"
    )
    return 0


def main() -> int:
    journal_path = _journal_path()
    if not journal_path.exists():
        print(f"FAIL: файл не найден: {journal_path}")
        return 1

    writer = JournalWriter(journal_path)
    writer.normalize_layout()
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

            if not item.journal_confirmed:
                continue

            in_journal = item.project_id in existing_ids
            response_payload = format_response_payload(
                item.response_text,
                price=item.price,
                delivery_days=item.delivery_days,
            )

            if item.journal_exported and in_journal:
                if writer.update_response_by_project_id(item.project_id, response_payload):
                    print(f"OK response: {item.title}")
                continue
            if (not item.journal_exported) and in_journal:
                if writer.update_response_by_project_id(item.project_id, response_payload):
                    print(f"OK response: {item.title}")
                _mark_exported_on_vps(path.name)
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
            return _sync_kwork_offer_statuses(journal_path)

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
        return _sync_kwork_offer_statuses(journal_path)


if __name__ == "__main__":
    sys.exit(main())
