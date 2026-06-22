from __future__ import annotations

import logging
import re
import shutil
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell

from src.models import GptScoreResult, ProjectFull

logger = logging.getLogger(__name__)

JOURNAL_COLUMNS = [
    "№",
    "Дата отклика",
    "Площадка",
    "Ссылка на проект",
    "Тип проекта",
    "Статус",
    "Результат общения",
    "Заметки",
]

PLATFORM_DISPLAY = {
    "kwork": "Kwork",
    "flru": "FL.ru",
    "telegram": "Telegram",
}


def format_offer_notes(
    title: str,
    *,
    price: str | None = None,
    delivery_days: int | None = None,
) -> str:
    price_text = f"{price} ₽" if price else "—"
    days_text = f"{delivery_days} дн." if delivery_days else "—"
    return f"{title}\nЦена: {price_text} · Срок: {days_text}"


class JournalWriter:
    def __init__(self, journal_path: str | Path) -> None:
        self.journal_path = Path(journal_path)

    def _ensure_workbook(self) -> None:
        if not self.journal_path.exists():
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.title = "Отклики"
            for col, header in enumerate(JOURNAL_COLUMNS, start=1):
                ws.cell(row=1, column=col, value=header)
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            wb.save(self.journal_path)

    def _header_row(self, ws) -> int:
        for row in range(1, min(ws.max_row + 1, 20)):
            if ws.cell(row=row, column=2).value == "Дата отклика":
                return row
        return 1

    def _writable(self, ws, row: int, col: int) -> bool:
        return not isinstance(ws.cell(row=row, column=col), MergedCell)

    def _row_has_data(self, ws, row: int) -> bool:
        for col in (2, 4):
            cell = ws.cell(row=row, column=col)
            if isinstance(cell, MergedCell):
                continue
            if cell.value not in (None, ""):
                return True
        return False

    def _next_row(self, ws) -> int:
        header = self._header_row(ws)
        for row in range(header + 1, max(ws.max_row + 2, header + 250)):
            if not self._writable(ws, row, 2):
                continue
            if not self._row_has_data(ws, row):
                return row
        return ws.max_row + 1

    def _next_number(self, ws) -> int:
        header = self._header_row(ws)
        max_n = 0
        for row in range(header + 1, ws.max_row + 1):
            if not self._row_has_data(ws, row):
                continue
            cell = ws.cell(row=row, column=1)
            if isinstance(cell, MergedCell):
                continue
            try:
                if cell.value is not None:
                    max_n = max(max_n, int(cell.value))
            except (TypeError, ValueError):
                continue
        return max_n + 1

    def project_ids_in_journal(self) -> set[str]:
        if not self.journal_path.exists():
            return set()
        wb = load_workbook(self.journal_path, read_only=True, data_only=True)
        ws = wb.active
        ids: set[str] = set()
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=4, max_col=4):
            cell = row[0]
            if cell.value is None:
                continue
            text = str(cell.value)
            match = re.search(r"/projects/(\d+)", text.replace("\\", "/"))
            if match:
                ids.add(match.group(1))
                continue
            for part in text.replace("\\", "/").split("/"):
                if part.isdigit() and len(part) >= 3:
                    ids.add(part)
        wb.close()
        return ids

    def _find_row_by_project_id(self, ws, project_id: str) -> int | None:
        header = self._header_row(ws)
        for row in range(header + 1, ws.max_row + 1):
            cell = ws.cell(row=row, column=4)
            if isinstance(cell, MergedCell):
                continue
            if project_id in str(cell.value or ""):
                return row
        return None

    def update_notes_by_project_id(
        self,
        project_id: str,
        notes: str,
    ) -> bool:
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        row = self._find_row_by_project_id(ws, project_id)
        if row is None or not self._writable(ws, row, 8):
            wb.close()
            return False
        ws.cell(row=row, column=8, value=notes)
        wb.save(self.journal_path)
        wb.close()
        logger.info("journal_notes_updated project_id=%s row=%d", project_id, row)
        return True

    def remove_row_by_project_id(self, project_id: str) -> bool:
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        row = self._find_row_by_project_id(ws, project_id)
        if row is None:
            wb.close()
            return False
        for col in range(1, len(JOURNAL_COLUMNS) + 1):
            if self._writable(ws, row, col):
                ws.cell(row=row, column=col, value=None)
        wb.save(self.journal_path)
        wb.close()
        logger.info("journal_row_removed project_id=%s row=%d", project_id, row)
        return True

    def append_submission(
        self,
        project: ProjectFull,
        score: GptScoreResult,
        response_text: str,
    ) -> int:
        self._ensure_workbook()
        wb = load_workbook(self.journal_path)
        ws = wb.active
        row = self._next_row(ws)

        platform_label = PLATFORM_DISPLAY.get(project.platform, project.platform)
        price = project.desired_budget or project.max_budget
        notes = format_offer_notes(project.title, price=price)

        ws.cell(row=row, column=1, value=self._next_number(ws))
        ws.cell(row=row, column=2, value=date.today())
        ws.cell(row=row, column=3, value=platform_label)
        ws.cell(row=row, column=4, value=project.url)
        ws.cell(row=row, column=5, value=score.suggested_project_type)
        ws.cell(row=row, column=6, value="Отправлен")
        ws.cell(row=row, column=7, value="Жду ответа")
        ws.cell(row=row, column=8, value=notes)

        wb.save(self.journal_path)
        logger.info(
            "journal_appended project_id=%s row=%d",
            project.project_id,
            row,
        )
        return row

    def append_prepared(
        self,
        project: ProjectFull,
        score: GptScoreResult,
        response_text: str,
        *,
        price: str | None = None,
        delivery_days: int | None = None,
    ) -> int:
        self._ensure_workbook()
        wb = load_workbook(self.journal_path)
        ws = wb.active
        row = self._next_row(ws)

        platform_label = PLATFORM_DISPLAY.get(project.platform, project.platform)
        notes = format_offer_notes(
            project.title,
            price=price,
            delivery_days=delivery_days,
        )

        ws.cell(row=row, column=1, value=self._next_number(ws))
        ws.cell(row=row, column=2, value=date.today())
        ws.cell(row=row, column=3, value=platform_label)
        ws.cell(row=row, column=4, value=project.url)
        ws.cell(row=row, column=5, value=score.suggested_project_type)
        ws.cell(row=row, column=6, value="Подготовлен")
        ws.cell(row=row, column=7, value="Жду ответа")
        ws.cell(row=row, column=8, value=notes)

        wb.save(self.journal_path)
        logger.info(
            "journal_prepared project_id=%s row=%d",
            project.project_id,
            row,
        )
        return row

    @staticmethod
    def create_template_copy(dest: Path, source: Path | None = None) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source and source.exists():
            shutil.copy(source, dest)
        else:
            JournalWriter(dest)._ensure_workbook()
        return dest
