from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from src.store.scan_reports import ScanReport


def _resolve_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo("UTC")


def format_scan_time(scanned_at: str, timezone_name: str) -> str:
    raw = scanned_at.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    local = dt.astimezone(_resolve_timezone(timezone_name))
    return local.strftime("%d.%m.%Y %H:%M")


def format_scan_reports_message(
    reports: list[ScanReport],
    *,
    timezone_name: str,
    limit: int = 3,
) -> str:
    if not reports:
        return (
            "📊 <b>Отчёт по сканам</b>\n"
            "Пока нет данных — дождись 1–2 циклов daemon."
        )

    lines = [
        "📊 <b>Отчёт: последние сканы</b>",
        f"Часовой пояс: <code>{html.escape(timezone_name)}</code>",
        "",
    ]
    for idx, report in enumerate(reports[:limit], start=1):
        when = format_scan_time(report.scanned_at, timezone_name)
        lines.append(f"<b>{idx}.</b> {when}")
        lines.append(
            f"Проверено: {report.checked} "
            f"(в ленте {report.seen}) · "
            f"не стек: {report.rejected_stack} · "
            f"не бюджет: {report.rejected_budget}"
        )
        if report.notified:
            lines.append(f"В TG: {report.notified}")
        lines.append("")
    return "\n".join(lines).strip()
