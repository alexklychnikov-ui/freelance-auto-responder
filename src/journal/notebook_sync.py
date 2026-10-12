from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell

from src.journal.writer import JOURNAL_COLUMNS, extract_journal_project_id

logger = logging.getLogger(__name__)

DEFAULT_SPREADSHEET_ID = "1EU0vEbEqxsMtMkMxNS6Drj00smbikG2gbrv3EU8zo94"
DEFAULT_TZ = "Asia/Irkutsk"
DEFAULT_MIN_DATE = date(2026, 9, 1)
DEFAULT_STATUS = "Отклик ушел"
SHEETS_SCOPE = ("https://www.googleapis.com/auth/spreadsheets",)

GS_HEADERS = [
    "№",
    "Вакансия/заказ",
    "Ссылка",
    "Дата отклика",
    "Статус заявки",
    "Ваш комментарий",
    "Сопроводительное письмо (текст)",
    "Принято вип-менеджером",
]

_MONTHS_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

_DATE_RE = re.compile(
    r"(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>\d{2,4})"
)


@dataclass
class JournalRow:
    response_date: date
    link: str
    norm_link: str
    vacancy: str  # short order title → GS «Вакансия/заказ»
    response_text: str  # our cover letter → GS «Сопроводительное письмо»
    comment: str  # client request text → GS «Ваш комментарий»
    number: int | None = None


def short_order_title(project_type: str, offer_text: str, *, limit: int = 60) -> str:
    """Compact order name for GS column B."""
    typed = _cell_text(project_type)
    if typed:
        return typed[:limit]
    return _first_line(offer_text, limit=limit)


@dataclass
class NotebookSyncRequest:
    n_days: int = 10
    requested_at: str | None = None
    chat_id: str | None = None
    request_id: str | None = None


@dataclass
class NotebookSyncResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        mode = "dry-run" if self.dry_run else "apply"
        lines = [
            f"📒 Notebook sync ({mode})",
            f"added={self.added} updated={self.updated} skipped={self.skipped}",
        ]
        if self.errors:
            lines.append(f"errors={len(self.errors)}")
            lines.extend(f"• {err}" for err in self.errors[:8])
        return "\n".join(lines)


@dataclass
class UpsertDecision:
    action: Literal["insert", "update", "skip"]
    sheet_row: int | None = None
    values: list[Any] = field(default_factory=list)
    reason: str = ""


def normalize_journal_link(url: str) -> str:
    raw = (url or "").strip().replace("\\", "/")
    if not raw:
        return ""
    project_id = extract_journal_project_id(raw)
    low = raw.lower()
    if project_id:
        if "kwork" in low:
            return f"kwork:{project_id}"
        if "fl.ru" in low or "freelance.ru" in low:
            return f"flru:{project_id}"
        if "yandex" in low or "uslugi" in low:
            return f"yandex:{project_id}"
        if project_id.startswith("tz_"):
            return f"tz:{project_id}"
        return f"id:{project_id}"
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "").rstrip("/")
    return urlunparse(("https", host, path, "", "", "")).lower()


def parse_journal_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if "T" in text or text.count("-") == 2 and len(text) >= 10:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    match = _DATE_RE.search(text)
    if not match:
        return None
    day = int(match.group("d"))
    month = int(match.group("m"))
    year = int(match.group("y"))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def format_gs_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def month_sheet_name(d: date) -> str:
    return f"{_MONTHS_RU[d.month]} {d.year}"


def match_key(norm_link: str, d: date) -> tuple[str, str]:
    return (norm_link, format_gs_date(d))


def resolve_timezone(name: str | None = None) -> ZoneInfo:
    tz_name = (name or os.environ.get("NOTEBOOK_TZ") or DEFAULT_TZ).strip()
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def parse_min_date(value: str | date | None = None) -> date:
    if isinstance(value, date):
        return value
    raw = (value or os.environ.get("NOTEBOOK_MIN_DATE") or "").strip()
    if not raw:
        return DEFAULT_MIN_DATE
    parsed = parse_journal_date(raw)
    return parsed or DEFAULT_MIN_DATE


def window_bounds(
    n_days: int,
    *,
    tz_name: str | None = None,
    min_date: date | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    days = max(1, min(int(n_days), 90))
    end = today or datetime.now(resolve_timezone(tz_name)).date()
    start = end - timedelta(days=days)
    floor = min_date or parse_min_date()
    if start < floor:
        start = floor
    return start, end


def filter_rows(
    rows: list[JournalRow],
    start: date,
    end: date,
) -> list[JournalRow]:
    return [row for row in rows if start <= row.response_date <= end]


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return format_gs_date(value.date())
    if isinstance(value, date):
        return format_gs_date(value)
    return str(value).strip()


def _url_from_cell(cell: Any) -> str:
    link = getattr(cell, "hyperlink", None)
    if link is not None and getattr(link, "target", None):
        return str(link.target)
    return _cell_text(cell.value)


def _first_line(text: str, limit: int = 200) -> str:
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:limit]
    return ""


def iter_journal_rows(path: str | Path) -> list[JournalRow]:
    journal_path = Path(path)
    if not journal_path.exists():
        raise FileNotFoundError(f"journal not found: {journal_path}")

    wb = load_workbook(journal_path, data_only=True)
    try:
        if "Отклики" in wb.sheetnames:
            ws = wb["Отклики"]
        else:
            ws = wb.active

        header_row = 1
        for row in range(1, min(ws.max_row + 1, 30)):
            values = [_cell_text(ws.cell(row=row, column=c).value) for c in range(1, 10)]
            if "Дата отклика" in values and "Ссылка на проект" in values:
                header_row = row
                break

        col_index = {
            JOURNAL_COLUMNS[i]: i + 1 for i in range(len(JOURNAL_COLUMNS))
        }
        for col in range(1, ws.max_column + 1):
            name = _cell_text(ws.cell(row=header_row, column=col).value)
            if name:
                col_index[name] = col

        date_col = col_index.get("Дата отклика", 2)
        link_col = col_index.get("Ссылка на проект", 4)
        type_col = col_index.get("Тип проекта", 5)
        offer_col = col_index.get("Предложения", 8)
        response_col = col_index.get("Отклик", 9)
        num_col = col_index.get("№", 1)

        rows: list[JournalRow] = []
        for row_idx in range(header_row + 1, ws.max_row + 1):
            date_cell = ws.cell(row=row_idx, column=date_col)
            link_cell = ws.cell(row=row_idx, column=link_col)
            if isinstance(date_cell, MergedCell) or isinstance(link_cell, MergedCell):
                continue
            response_date = parse_journal_date(date_cell.value)
            link = _url_from_cell(link_cell)
            if response_date is None or not link.strip():
                continue
            offer_text = _cell_text(ws.cell(row=row_idx, column=offer_col).value)
            project_type = _cell_text(ws.cell(row=row_idx, column=type_col).value)
            vacancy = short_order_title(project_type, offer_text)
            number_raw = ws.cell(row=row_idx, column=num_col).value
            number: int | None
            try:
                number = int(number_raw) if number_raw not in (None, "") else None
            except (TypeError, ValueError):
                number = None
            rows.append(
                JournalRow(
                    response_date=response_date,
                    link=link.strip(),
                    norm_link=normalize_journal_link(link),
                    vacancy=vacancy,
                    response_text=_cell_text(
                        ws.cell(row=row_idx, column=response_col).value
                    ),
                    # Journal «Предложения» = client brief/request → GS comment col
                    comment=offer_text,
                    number=number,
                )
            )
        return rows
    finally:
        wb.close()


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    return not str(value).strip()


def decide_upsert(
    journal_row: JournalRow,
    *,
    existing: list[Any] | None,
    sheet_row: int | None = None,
    next_number: int = 1,
) -> UpsertDecision:
    """Pure upsert policy for Journal → Google Sheet."""
    date_str = format_gs_date(journal_row.response_date)
    if existing is None:
        values = [
            next_number,
            journal_row.vacancy,
            journal_row.link,
            date_str,
            DEFAULT_STATUS,
            journal_row.comment,
            journal_row.response_text,
            "",
        ]
        return UpsertDecision(action="insert", values=values, reason="new")

    padded = list(existing) + [""] * max(0, len(GS_HEADERS) - len(existing))
    current_vacancy = padded[1] if len(padded) > 1 else ""
    current_comment = padded[5] if len(padded) > 5 else ""
    current_letter = padded[6] if len(padded) > 6 else ""

    new_vacancy = current_vacancy if not _is_empty(current_vacancy) else journal_row.vacancy
    # ALWAYS refresh request text + cover letter from journal
    new_comment = journal_row.comment or current_comment
    new_letter = journal_row.response_text

    unchanged = (
        _cell_text(current_vacancy) == _cell_text(new_vacancy)
        and _cell_text(current_comment) == _cell_text(new_comment)
        and _cell_text(current_letter) == _cell_text(new_letter)
    )
    if unchanged:
        return UpsertDecision(
            action="skip",
            sheet_row=sheet_row,
            reason="no_changes",
        )

    values = [
        padded[0],
        new_vacancy,
        padded[2] if not _is_empty(padded[2]) else journal_row.link,
        padded[3] if not _is_empty(padded[3]) else date_str,
        padded[4] if not _is_empty(padded[4]) else DEFAULT_STATUS,
        new_comment,
        new_letter,
        padded[7],  # VIP preserved
    ]
    return UpsertDecision(
        action="update",
        sheet_row=sheet_row,
        values=values,
        reason="matched",
    )


def _load_service_account_credentials():
    from google.oauth2.service_account import Credentials

    file_path = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    raw_json = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if file_path:
        return Credentials.from_service_account_file(file_path, scopes=SHEETS_SCOPE)
    if raw_json:
        info = json.loads(raw_json)
        return Credentials.from_service_account_info(info, scopes=SHEETS_SCOPE)
    raise RuntimeError(
        "Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON"
    )


def open_spreadsheet(spreadsheet_id: str | None = None):
    import gspread

    sid = (
        spreadsheet_id
        or os.environ.get("NOTEBOOK_SPREADSHEET_ID")
        or DEFAULT_SPREADSHEET_ID
    ).strip()
    creds = _load_service_account_credentials()
    client = gspread.authorize(creds)
    return client.open_by_key(sid)


def _find_header_row(values: list[list[Any]]) -> int:
    for idx, row in enumerate(values[:30]):
        cells = [_cell_text(c) for c in row]
        if "Ссылка" in cells and "Дата отклика" in cells:
            return idx
    return 0


def _header_map(header_row: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        name = _cell_text(cell)
        if name:
            mapping[name] = idx
    # tolerate shorter header label variants
    if "Сопроводительное письмо (текст)" not in mapping:
        for name, idx in list(mapping.items()):
            if name.startswith("Сопроводительное письмо"):
                mapping["Сопроводительное письмо (текст)"] = idx
    return mapping


def find_worksheet_ci(spreadsheet, title: str):
    """Find worksheet by title, case-insensitive (GS months are lowercase)."""
    want = (title or "").casefold()
    for ws in spreadsheet.worksheets():
        if (ws.title or "").casefold() == want:
            return ws
    return None


def ensure_month_worksheet(spreadsheet, title: str, *, dry_run: bool = False):
    existing = find_worksheet_ci(spreadsheet, title)
    if existing is not None:
        return existing
    if dry_run:
        return None
    # Prefer lowercase month titles to match existing Notebook sheets.
    create_title = title[:1].lower() + title[1:] if title else title
    if find_worksheet_ci(spreadsheet, create_title) is None:
        title = create_title
    ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(GS_HEADERS))
    ws.update("A1", [list(GS_HEADERS)])
    return ws


def _build_index(
    values: list[list[Any]],
    header_idx: int,
    header_map: dict[str, int],
) -> dict[tuple[str, str], int]:
    link_i = header_map.get("Ссылка", 2)
    date_i = header_map.get("Дата отклика", 3)
    index: dict[tuple[str, str], int] = {}
    for offset, row in enumerate(values[header_idx + 1 :], start=header_idx + 2):
        if not row:
            continue
        link = _cell_text(row[link_i] if link_i < len(row) else "")
        d = parse_journal_date(row[date_i] if date_i < len(row) else None)
        if not link or d is None:
            continue
        index[match_key(normalize_journal_link(link), d)] = offset
    return index


def _row_has_data(row: list[Any], header_map: dict[str, int]) -> bool:
    """True if row looks like a real response (not empty template № slot)."""
    link_i = header_map.get("Ссылка", 2)
    date_i = header_map.get("Дата отклика", 3)
    vac_i = header_map.get("Вакансия/заказ", 1)
    link = _cell_text(row[link_i] if link_i < len(row) else "")
    d = _cell_text(row[date_i] if date_i < len(row) else "")
    vac = _cell_text(row[vac_i] if vac_i < len(row) else "")
    return bool(link or d or vac)


def _max_number(
    values: list[list[Any]],
    header_idx: int,
    num_i: int,
    header_map: dict[str, int] | None = None,
) -> int:
    max_n = 0
    for row in values[header_idx + 1 :]:
        if not row or num_i >= len(row):
            continue
        if header_map is not None and not _row_has_data(row, header_map):
            continue
        try:
            max_n = max(max_n, int(row[num_i]))
        except (TypeError, ValueError):
            continue
    return max_n


def _empty_slot_rows(
    values: list[list[Any]],
    header_idx: int,
    header_map: dict[str, int],
    *,
    count: int,
) -> list[int]:
    """1-based sheet row numbers of empty template slots after header."""
    slots: list[int] = []
    if count <= 0:
        return slots
    for offset, row in enumerate(values[header_idx + 1 :], start=header_idx + 2):
        if not _row_has_data(row, header_map):
            slots.append(offset)
            if len(slots) >= count:
                break
    # If template exhausted, continue after last sheet row.
    next_row = len(values) + 1
    while len(slots) < count:
        slots.append(next_row)
        next_row += 1
    return slots


def sync_journal_to_notebook(
    journal_path: str | Path,
    *,
    n_days: int = 10,
    spreadsheet_id: str | None = None,
    tz_name: str | None = None,
    min_date: date | None = None,
    dry_run: bool = False,
    spreadsheet=None,
) -> NotebookSyncResult:
    result = NotebookSyncResult(dry_run=dry_run)
    start, end = window_bounds(
        n_days, tz_name=tz_name, min_date=min_date
    )
    try:
        rows = filter_rows(iter_journal_rows(journal_path), start, end)
    except Exception as exc:
        result.errors.append(f"journal_read: {exc}")
        return result

    if not rows:
        return result

    by_sheet: dict[str, list[JournalRow]] = {}
    for row in rows:
        by_sheet.setdefault(month_sheet_name(row.response_date), []).append(row)

    try:
        ss = spreadsheet or open_spreadsheet(spreadsheet_id)
    except Exception as exc:
        result.errors.append(f"google_auth: {exc}")
        return result

    for sheet_title, sheet_rows in by_sheet.items():
        try:
            ws = ensure_month_worksheet(ss, sheet_title, dry_run=dry_run)
            if ws is None:
                values: list[list[Any]] = [list(GS_HEADERS)]
            else:
                values = ws.get_all_values()
                if not values:
                    if dry_run:
                        values = [list(GS_HEADERS)]
                    else:
                        ws.update("A1", [GS_HEADERS])
                        values = [list(GS_HEADERS)]
            header_idx = _find_header_row(values)
            header_map = _header_map(values[header_idx])
            for required in ("Ссылка", "Дата отклика"):
                if required not in header_map:
                    raise RuntimeError(
                        f"sheet {sheet_title!r}: missing header {required!r}"
                    )
            index = _build_index(values, header_idx, header_map)
            num_i = header_map.get("№", 0)
            next_number = _max_number(values, header_idx, num_i, header_map) + 1

            updates: list[tuple[int, list[Any]]] = []
            inserts: list[list[Any]] = []

            for journal_row in sheet_rows:
                key = match_key(journal_row.norm_link, journal_row.response_date)
                existing_row_num = index.get(key)
                existing_values = None
                if existing_row_num is not None:
                    existing_values = values[existing_row_num - 1]
                decision = decide_upsert(
                    journal_row,
                    existing=existing_values,
                    sheet_row=existing_row_num,
                    next_number=next_number,
                )
                if decision.action == "skip":
                    result.skipped += 1
                    continue
                if decision.action == "insert":
                    inserts.append(decision.values)
                    next_number += 1
                    result.added += 1
                elif decision.action == "update" and decision.sheet_row is not None:
                    updates.append((decision.sheet_row, decision.values))
                    values[decision.sheet_row - 1] = [
                        str(v) if v is not None else "" for v in decision.values
                    ]
                    result.updated += 1

            if dry_run or ws is None:
                continue

            for row_num, row_values in updates:
                end_col = chr(ord("A") + len(row_values) - 1)
                ws.update(
                    f"A{row_num}:{end_col}{row_num}",
                    [row_values],
                    value_input_option="USER_ENTERED",
                )
            if inserts:
                # Prefill empty template slots under the header (not append under №200).
                slots = _empty_slot_rows(
                    values, header_idx, header_map, count=len(inserts)
                )
                for slot_row, row_values in zip(slots, inserts, strict=True):
                    # Keep template № in col A when slot already has one.
                    if slot_row <= len(values):
                        existing_num = (
                            values[slot_row - 1][num_i]
                            if num_i < len(values[slot_row - 1])
                            else ""
                        )
                        if not _is_empty(existing_num):
                            row_values = list(row_values)
                            row_values[0] = existing_num
                    end_col = chr(ord("A") + len(row_values) - 1)
                    ws.update(
                        f"A{slot_row}:{end_col}{slot_row}",
                        [row_values],
                        value_input_option="USER_ENTERED",
                    )
                    # Extend local values so subsequent slot picks stay correct.
                    while len(values) < slot_row:
                        values.append([""] * len(GS_HEADERS))
                    values[slot_row - 1] = [
                        str(v) if v is not None else "" for v in row_values
                    ]
                    link = _cell_text(row_values[header_map.get("Ссылка", 2)])
                    d = parse_journal_date(
                        row_values[header_map.get("Дата отклика", 3)]
                    )
                    if link and d is not None:
                        index[match_key(normalize_journal_link(link), d)] = slot_row
        except Exception as exc:
            logger.exception("notebook_sheet_sync_failed sheet=%s", sheet_title)
            result.errors.append(f"{sheet_title}: {exc}")

    return result


def resolve_journal_path(
    explicit: str | Path | None = None,
    *,
    settings_path: str | None = None,
) -> Path:
    if explicit:
        return Path(explicit)
    env_override = (os.environ.get("NOTEBOOK_JOURNAL_PATH") or "").strip()
    if env_override:
        return Path(env_override)
    if settings_path:
        return Path(settings_path)
    env_journal = (os.environ.get("RESPONSE_JOURNAL") or "").strip()
    if env_journal:
        return Path(env_journal)
    return Path("data/response_journal.xlsx")
