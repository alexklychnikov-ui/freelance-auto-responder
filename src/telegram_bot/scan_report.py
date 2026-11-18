from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from src.store.scan_reports import ScanCycleStats, ScanReport

# Keep aligned with bot.PLATFORM_LABELS
PLATFORM_LABELS = {
    "kwork": "Kwork",
    "flru": "FL.ru",
    "telegram": "Telegram",
    "yandex_uslugi": "Яндекс Услуги",
}

_PLATFORM_ORDER = ("kwork", "yandex_uslugi", "flru")


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


def _platform_sort_key(platform: str) -> tuple[int, str]:
    try:
        return (_PLATFORM_ORDER.index(platform), platform)
    except ValueError:
        return (len(_PLATFORM_ORDER), platform)


def _format_stats_compact(stats: ScanCycleStats) -> str:
    if stats.errors:
        err = (stats.error or "ошибка скана").strip()
        if len(err) > 120:
            err = err[:117] + "…"
        return f"❌ DOWN · {html.escape(err)}"
    return (
        f"лента {stats.seen} · "
        f"в TG {stats.notified} · "
        f"не стек {stats.rejected_stack} · "
        f"не бюджет {stats.rejected_budget}"
    )


def _format_legacy_lines(report: ScanReport) -> list[str]:
    lines = [
        f"Лента: {report.seen} · в TG: {report.notified} · "
        f"не стек: {report.rejected_stack} · не бюджет: {report.rejected_budget}"
    ]
    return lines


def _format_by_platform_lines(report: ScanReport) -> list[str]:
    lines: list[str] = []
    down: list[str] = []
    for plat in sorted(report.by_platform.keys(), key=_platform_sort_key):
        stats = report.by_platform[plat]
        label = PLATFORM_LABELS.get(plat, plat)
        line = f"• {html.escape(label)} — {_format_stats_compact(stats)}"
        lines.append(line)
        if stats.errors:
            down.append(label)
    if down:
        lines.insert(
            0,
            "🚨 Отвал: " + ", ".join(html.escape(x) for x in down),
        )
    ok_totals = ScanCycleStats(
        seen=report.seen,
        checked=report.checked,
        rejected_stack=report.rejected_stack,
        rejected_budget=report.rejected_budget,
        notified=report.notified,
        errors=sum(s.errors for s in report.by_platform.values()),
    )
    if ok_totals.errors == 0:
        lines.append(
            f"Итого: лента {ok_totals.seen} · в TG {ok_totals.notified}"
        )
    return lines


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
    latest = reports[0]
    if latest.by_platform and any(s.errors for s in latest.by_platform.values()):
        lines.append("⚠️ В последнем цикле есть площадки в DOWN — см. ниже.")
        lines.append("")
    for idx, report in enumerate(reports[:limit], start=1):
        when = format_scan_time(report.scanned_at, timezone_name)
        lines.append(f"<b>{idx}.</b> {when}")
        if report.by_platform:
            lines.extend(_format_by_platform_lines(report))
        else:
            lines.extend(_format_legacy_lines(report))
        lines.append("")
    return "\n".join(lines).strip()
