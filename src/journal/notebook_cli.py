"""Local Journal.xlsx → Google Notebook sync (Bridge A).

Usage:
  python -m src.journal.notebook_cli sync --days 10 --dry-run
  python -m src.journal.notebook_cli sync --days 10 --apply
  python -m src.journal.notebook_cli watch
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.journal.notebook_sync import (
    NotebookSyncRequest,
    NotebookSyncResult,
    resolve_journal_path,
    sync_journal_to_notebook,
    window_bounds,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VPS_HOST = "LightRAG_Naive"
REMOTE_JOB = "/opt/freelance-responder/data/notebook_sync_request.json"
REMOTE_JOB_DONE = "/opt/freelance-responder/data/notebook_sync_request.done.json"
REMOTE_JOURNAL = "/opt/freelance-responder/data/response_journal.xlsx"
LOCAL_JOB_DIR = REPO_ROOT / "data" / "notebook_jobs"


def _load_env() -> None:
    load_dotenv(REPO_ROOT / ".env", override=False)


def _vps_host() -> str:
    return (os.environ.get("FREELANCE_VPS_HOST") or DEFAULT_VPS_HOST).strip()


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info("+ %s", " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def parse_job(raw: dict) -> NotebookSyncRequest:
    n_days = int(raw.get("n_days") or 10)
    n_days = max(1, min(n_days, 90))
    return NotebookSyncRequest(
        n_days=n_days,
        requested_at=raw.get("requested_at"),
        chat_id=str(raw.get("chat_id") or "") or None,
        request_id=str(raw.get("request_id") or "") or None,
    )


def _telegram_send_direct(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text[:3900]}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = json.loads(body)
    if not data.get("ok"):
        raise RuntimeError(f"telegram sendMessage failed: {body[:300]}")


def _telegram_send_via_vps(token: str, chat_id: str, text: str) -> None:
    """Fallback when local PC cannot reach api.telegram.org (scp payload + remote python)."""
    host = _vps_host()
    LOCAL_JOB_DIR.mkdir(parents=True, exist_ok=True)
    payload_local = LOCAL_JOB_DIR / "tg_report_payload.json"
    script_local = LOCAL_JOB_DIR / "tg_send_remote.py"
    payload_local.write_text(
        json.dumps(
            {"token": token, "chat_id": chat_id, "text": text[:3900]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script_local.write_text(
        "import json\n"
        "import urllib.parse\n"
        "import urllib.request\n"
        "p = json.load(open('/tmp/notebook_tg_payload.json', encoding='utf-8'))\n"
        "data = urllib.parse.urlencode(\n"
        "    {'chat_id': p['chat_id'], 'text': p['text'][:3900]}\n"
        ").encode()\n"
        "req = urllib.request.Request(\n"
        "    f\"https://api.telegram.org/bot{p['token']}/sendMessage\",\n"
        "    data=data,\n"
        "    method='POST',\n"
        "    headers={'Content-Type': 'application/x-www-form-urlencoded'},\n"
        ")\n"
        "body = urllib.request.urlopen(req, timeout=30).read().decode()\n"
        "print(body)\n"
        "raise SystemExit(0 if json.loads(body).get('ok') else 1)\n",
        encoding="utf-8",
    )
    scp1 = _run(
        ["scp", str(payload_local), f"{host}:/tmp/notebook_tg_payload.json"],
        check=False,
    )
    if scp1.returncode != 0:
        raise RuntimeError(f"vps scp payload failed: {(scp1.stderr or '')[:200]}")
    scp2 = _run(
        ["scp", str(script_local), f"{host}:/tmp/notebook_tg_send.py"],
        check=False,
    )
    if scp2.returncode != 0:
        raise RuntimeError(f"vps scp script failed: {(scp2.stderr or '')[:200]}")
    proc = _run(
        ["ssh", host, "python3 /tmp/notebook_tg_send.py"],
        check=False,
    )
    try:
        payload_local.unlink(missing_ok=True)
    except OSError:
        pass
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:300]
        raise RuntimeError(f"vps telegram fallback failed: {err}")


def send_telegram_message(text: str, *, chat_id: str | None = None) -> None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    target = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not target:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing")

    # Local PC often cannot reach api.telegram.org (WinError 10060) — fail fast, then VPS.
    prefer_vps = (os.environ.get("NOTEBOOK_TG_VIA_VPS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    last_exc: Exception | None = None
    if not prefer_vps:
        try:
            _telegram_send_direct(token, target, text)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning("telegram_direct_failed err=%s — try VPS", exc)
    try:
        _telegram_send_via_vps(token, target, text)
        logger.info("telegram_report_sent_via_vps")
        return
    except Exception as exc:
        raise RuntimeError(
            f"telegram direct+vps failed: direct={last_exc}; vps={exc}"
        ) from exc


def run_sync(
    *,
    days: int,
    apply: bool,
    journal: str | None = None,
    spreadsheet_id: str | None = None,
) -> NotebookSyncResult:
    _load_env()
    try:
        from src.config import get_settings

        settings = get_settings()
        journal_path = resolve_journal_path(
            journal, settings_path=settings.response_journal
        )
        sid = spreadsheet_id or settings.notebook_spreadsheet_id
        tz_name = settings.notebook_tz
        min_date = settings.notebook_min_date_value()
    except Exception:
        journal_path = resolve_journal_path(journal)
        sid = spreadsheet_id
        tz_name = None
        min_date = None

    start, end = window_bounds(days, tz_name=tz_name, min_date=min_date)
    print(f"journal={journal_path}")
    print(f"window={start.isoformat()}..{end.isoformat()} dry_run={not apply}")
    result = sync_journal_to_notebook(
        journal_path,
        n_days=days,
        spreadsheet_id=sid,
        tz_name=tz_name,
        min_date=min_date,
        dry_run=not apply,
    )
    print(result.summary())
    for err in result.errors:
        print(f"ERR: {err}", file=sys.stderr)
    return result


def _pull_job(local_path: Path) -> bool:
    host = _vps_host()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if local_path.exists():
        local_path.unlink()
    proc = _run(
        ["scp", f"{host}:{REMOTE_JOB}", str(local_path)],
        check=False,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").lower()
        if "no such file" in stderr or "not found" in stderr:
            return False
        logger.warning("scp_job_failed: %s", proc.stderr.strip())
        return False
    return local_path.exists() and local_path.stat().st_size > 0


def _ack_job() -> None:
    host = _vps_host()
    script = (
        f"if [ -f {REMOTE_JOB} ]; then "
        f"mv -f {REMOTE_JOB} {REMOTE_JOB_DONE}; "
        f"fi"
    )
    proc = _run(["ssh", host, script], check=False)
    if proc.returncode != 0:
        logger.warning("ack_job_failed: %s", (proc.stderr or proc.stdout).strip())


def pull_vps_journal(dest: Path | None = None) -> Path:
    """scp fresh journal.xlsx from VPS over local RESPONSE_JOURNAL.

    /notebook reads the LOCAL file; without this pull, GS stays on stale Aug data.
    """
    _load_env()
    if dest is None:
        dest = resolve_journal_path(os.environ.get("RESPONSE_JOURNAL"))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    host = _vps_host()
    remote = (os.environ.get("NOTEBOOK_REMOTE_JOURNAL") or REMOTE_JOURNAL).strip()
    proc = _run(["scp", f"{host}:{remote}", str(tmp)], check=False)
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= 0:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"scp journal failed: {(proc.stderr or proc.stdout or '').strip()[:300]}"
        )
    tmp.replace(dest)
    logger.info("pulled_vps_journal dest=%s bytes=%s", dest, dest.stat().st_size)
    print(f"pulled VPS journal → {dest} ({dest.stat().st_size} bytes)")
    return dest


def process_job_file(path: Path, *, apply: bool = True) -> NotebookSyncResult:
    raw = json.loads(path.read_text(encoding="utf-8"))
    job = parse_job(raw)
    try:
        pull_vps_journal()
    except Exception as exc:
        logger.exception("pull_vps_journal_failed")
        # Still try sync — but report the pull error in TG.
        result = run_sync(days=job.n_days, apply=apply)
        result.errors.append(f"journal_pull: {exc}")
        report = result.summary()
        if job.request_id:
            report = f"{report}\nrequest_id={job.request_id}"
        report = f"{report}\n⚠️ journal_pull failed: {exc}"
        try:
            send_telegram_message(report, chat_id=job.chat_id)
        except Exception as tg_exc:
            result.errors.append(f"telegram: {tg_exc}")
        return result
    result = run_sync(days=job.n_days, apply=apply)
    report = result.summary()
    if job.request_id:
        report = f"{report}\nrequest_id={job.request_id}"
    if job.requested_at:
        report = f"{report}\nrequested_at={job.requested_at}"
    try:
        send_telegram_message(report, chat_id=job.chat_id)
    except Exception as exc:
        logger.exception("telegram_report_failed")
        result.errors.append(f"telegram: {exc}")
    return result


def run_watch(*, poll_seconds: float = 15.0, apply: bool = True) -> None:
    _load_env()
    print(
        "Watch-Notebook: polling VPS job "
        f"{_vps_host()}:{REMOTE_JOB} every {poll_seconds:.0f}s "
        f"(apply={apply}). Ctrl+C to stop."
    )
    LOCAL_JOB_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        local = LOCAL_JOB_DIR / "notebook_sync_request.json"
        try:
            if _pull_job(local):
                print(
                    f"[{datetime.now(timezone.utc).isoformat()}] job received → sync"
                )
                result = process_job_file(local, apply=apply)
                fatal = any(
                    e.startswith("google_auth") or e.startswith("journal_read")
                    for e in result.errors
                )
                if fatal:
                    print("kept remote job (fatal error)")
                else:
                    _ack_job()
                    print("ack: remote job → .done.json")
        except KeyboardInterrupt:
            print("watch stopped")
            return
        except Exception:
            logger.exception("watch_iteration_failed")
        time.sleep(poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.journal.notebook_cli",
        description=(
            "Sync local journal.xlsx → Google Notebook sheet. "
            "Google SA credentials stay on LOCAL PC only."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync_p = sub.add_parser("sync", help="One-shot sync for last N days")
    sync_p.add_argument("--days", type=int, default=10, help="Window size N (1..90)")
    sync_p.add_argument("--journal", default=None, help="Override local journal.xlsx")
    sync_p.add_argument(
        "--spreadsheet-id",
        default=None,
        help="Override NOTEBOOK_SPREADSHEET_ID",
    )
    sync_p.add_argument(
        "--pull-vps",
        action="store_true",
        help="scp fresh journal.xlsx from VPS before sync",
    )
    mode = sync_p.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write to Google Sheets",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute upserts without writing (default)",
    )

    watch_p = sub.add_parser(
        "watch",
        help="Loop: scp job from VPS → sync --apply → TG report → ack",
    )
    watch_p.add_argument(
        "--poll",
        type=float,
        default=15.0,
        help="Seconds between scp polls (default 15)",
    )
    watch_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Watch without writing to Google Sheets",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "sync":
        if getattr(args, "pull_vps", False):
            try:
                pull_vps_journal(
                    Path(args.journal) if args.journal else None
                )
            except Exception as exc:
                print(f"ERR: journal_pull: {exc}", file=sys.stderr)
                return 1
        result = run_sync(
            days=max(1, min(int(args.days), 90)),
            apply=bool(args.apply),
            journal=args.journal,
            spreadsheet_id=args.spreadsheet_id,
        )
        return 1 if result.errors else 0

    if args.command == "watch":
        run_watch(poll_seconds=float(args.poll), apply=not args.dry_run)
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
