from __future__ import annotations

import logging
import re
import shutil
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment
from openpyxl.styles import Font
from openpyxl.styles import PatternFill

from src.adapters.flru_urls import extract_flru_project_id
from src.adapters.kwork_urls import extract_kwork_project_id
from src.adapters.yandex_urls import extract_yandex_order_id
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
    "Предложения",
    "Отклик",
    "Бюджет заказчика",
    "Моя цена",
    "Релевантность",
    "Похожий кейс",
    "Тип отклика",
    "Причина отказа",
]

# Expanded funnel statuses for docs/comments (update_status stays free-text).
FUNNEL_STATUSES = (
    "Подготовлен",
    "Отправлен",
    "Просмотрен",
    "Ответ получен",
    "Диалог",
    "Созвон",
    "Тестовое",
    "Заказ",
    "Отказ",
    "Без ответа",
)

OFFER_COLUMN = 8
RESPONSE_COLUMN = 9
KPI_FIRST_COLUMN = 10
KPI_LAST_COLUMN = 15
CLIENT_BUDGET_COLUMN = 10
MY_PRICE_COLUMN = 11
RELEVANCE_COLUMN = 12
SIMILAR_CASE_COLUMN = 13
RESPONSE_KIND_COLUMN = 14
REJECT_REASON_COLUMN = 15
TEXT_COLUMN_WIDTH = 73

_STOCK_RESPONSE_PHRASES = (
    "здравствуйте",
    "добрый день",
    "готов обсудить",
    "давайте обсудим",
    "могу выполнить",
    "готов приступить",
    "с уважением",
    "буду рад",
    "напишите",
    "свяжитесь",
)

PLATFORM_DISPLAY = {
    "kwork": "Kwork",
    "flru": "FL.ru",
    "telegram": "Telegram",
    "yandex_uslugi": "Яндекс Услуги",
}


def kwork_project_url(project_id: str) -> str:
    return f"https://kwork.ru/projects/{project_id}/view"


def infer_project_type(title: str) -> str:
    low = (title or "").lower()
    if any(x in low for x in ("telegram", "телеграм", "бот")):
        return "Telegram-бот"
    if "парс" in low or "parser" in low or "scrap" in low:
        return "Парсинг"
    if any(x in low for x in ("rag", "llm", "gpt", "ии ", " ai", "нейро")):
        return "AI/RAG"
    if any(x in low for x in ("сайт", "лендинг", "wordpress", "веб", "frontend", "backend")):
        return "Веб-MVP"
    if any(x in low for x in ("интеграц", "api", "crm", "1с")):
        return "Интеграция"
    if any(x in low for x in ("автомат", "скрипт", "excel", "google sheet")):
        return "Автоматизация"
    return "Другое"


_TZ_PROJECT_ID_RE = re.compile(r"\btz_\d+\b")


def extract_journal_project_id(text: str) -> str | None:
    """Extract canonical project/order id from a journal URL cell."""
    raw = (text or "").strip()
    if not raw:
        return None
    normalized = raw.replace("\\", "/")
    tz_match = _TZ_PROJECT_ID_RE.search(normalized)
    if tz_match:
        return tz_match.group(0)
    for extractor in (
        extract_kwork_project_id,
        extract_flru_project_id,
        extract_yandex_order_id,
    ):
        project_id = extractor(normalized)
        if project_id:
            return project_id
    for part in normalized.split("/"):
        if part.isdigit() and len(part) >= 3:
            return part
    return None


def format_response_payload(
    response_text: str,
    *,
    price: str | None = None,
    delivery_days: int | None = None,
) -> str:
    price_text = f"{price} ₽" if price else "—"
    days_text = f"{delivery_days} дн." if delivery_days else "—"
    body = (response_text or "").strip()
    return f"Отклик\n{body}\n\nОбщий бюджет: {price_text}\nСрок: {days_text}".strip()


def client_budget_value(project: ProjectFull) -> str:
    return (project.desired_budget or project.max_budget or "").strip()


def relevance_tier(
    score: GptScoreResult,
    *,
    acceptance_tier: str | None = None,
) -> str:
    """A/B/C relevance from score (+ optional acceptance quick/experience)."""
    if score.score >= 7 and score.fit:
        return "A"
    if score.score >= 5 or acceptance_tier in ("quick_win", "experience_win"):
        return "B"
    return "C"


def similar_case_label(score: GptScoreResult) -> str:
    return "Да" if score.matched_skills else "Нет"


def _is_mostly_stock_phrases(text: str) -> bool:
    low = (text or "").lower()
    if not low.strip():
        return True
    hits = sum(1 for phrase in _STOCK_RESPONSE_PHRASES if phrase in low)
    # Short texts with several stock openers/closers count as template-only.
    return hits >= 2 and len(low.strip()) <= 220


def response_kind_label(response_text: str) -> str:
    """Шаблон / Персональный / Персональный + demo."""
    body = (response_text or "").strip()
    low = body.lower()
    has_demo = "demo" in low or "демо" in low
    personal = len(body) > 200 and not _is_mostly_stock_phrases(body)
    if has_demo:
        return "Персональный + demo"
    if personal:
        return "Персональный"
    return "Шаблон"


class JournalWriter:
    def __init__(self, journal_path: str | Path) -> None:
        self.journal_path = Path(journal_path)

    @staticmethod
    def _url_from_cell(cell) -> str:
        link = getattr(cell, "hyperlink", None)
        if link is not None and getattr(link, "target", None):
            return str(link.target)
        return str(cell.value or "")

    @staticmethod
    def _set_url_cell(ws, row: int, url: str) -> None:
        url_text = (url or "").strip()
        if not url_text:
            cell = ws.cell(row=row, column=4, value=None)
            cell.hyperlink = None
            cell.font = Font()
            return
        cell = ws.cell(row=row, column=4, value=url_text)
        cell.hyperlink = url_text
        cell.font = Font(color="0563C1", underline="single")

    def _ensure_workbook(self) -> None:
        if not self.journal_path.exists():
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.title = "Отклики"
            for col, header in enumerate(JOURNAL_COLUMNS, start=1):
                ws.cell(row=1, column=col, value=header)
            self._apply_journal_layout(ws)
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            wb.save(self.journal_path)

    def _ensure_kpi_headers(self, ws) -> None:
        header = self._header_row(ws)
        header_fill = PatternFill(
            fill_type="solid",
            start_color="1F4E78",
            end_color="1F4E78",
        )
        header_font = Font(color="FFFFFF", bold=True)
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for col in range(KPI_FIRST_COLUMN, KPI_LAST_COLUMN + 1):
            expected = JOURNAL_COLUMNS[col - 1]
            cell = ws.cell(row=header, column=col)
            if cell.value != expected:
                cell.value = expected
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align

    def _write_kpi_cells(
        self,
        ws,
        row: int,
        *,
        project: ProjectFull | None = None,
        score: GptScoreResult | None = None,
        response_text: str = "",
        price: str | None = None,
        acceptance_tier: str | None = None,
        project_type_title: str = "",
    ) -> None:
        """Fill KPI columns 10–14; leave 15 (Причина отказа) empty for manual entry."""
        if project is not None:
            client_budget = client_budget_value(project)
        else:
            client_budget = ""

        my_price = (price or "").strip()

        if score is not None:
            relevance = relevance_tier(score, acceptance_tier=acceptance_tier)
            similar = similar_case_label(score)
        else:
            relevance = "C" if project_type_title else ""
            similar = "Нет"

        kind = response_kind_label(response_text) if response_text else ""

        if self._writable(ws, row, CLIENT_BUDGET_COLUMN):
            ws.cell(row=row, column=CLIENT_BUDGET_COLUMN, value=client_budget or None)
        if self._writable(ws, row, MY_PRICE_COLUMN):
            ws.cell(row=row, column=MY_PRICE_COLUMN, value=my_price or None)
        if self._writable(ws, row, RELEVANCE_COLUMN):
            ws.cell(row=row, column=RELEVANCE_COLUMN, value=relevance or None)
        if self._writable(ws, row, SIMILAR_CASE_COLUMN):
            ws.cell(row=row, column=SIMILAR_CASE_COLUMN, value=similar or None)
        if self._writable(ws, row, RESPONSE_KIND_COLUMN):
            ws.cell(row=row, column=RESPONSE_KIND_COLUMN, value=kind or None)
        # col 15 left empty intentionally

    def _apply_journal_layout(self, ws) -> None:
        header = self._header_row(ws)
        ws.column_dimensions["H"].width = TEXT_COLUMN_WIDTH
        ws.column_dimensions["I"].width = TEXT_COLUMN_WIDTH

        header_fill = PatternFill(
            fill_type="solid",
            start_color="1F4E78",
            end_color="1F4E78",
        )
        header_font = Font(color="FFFFFF", bold=True)
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

        ws.cell(row=header, column=OFFER_COLUMN, value="Предложения")
        ws.cell(row=header, column=RESPONSE_COLUMN, value="Отклик")
        for col in (OFFER_COLUMN, RESPONSE_COLUMN):
            cell = ws.cell(row=header, column=col)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align

        self._ensure_kpi_headers(ws)

        text_align = Alignment(vertical="top", wrap_text=True)
        for row in range(header + 1, ws.max_row + 1):
            for col in (OFFER_COLUMN, RESPONSE_COLUMN):
                cell = ws.cell(row=row, column=col)
                if isinstance(cell, MergedCell):
                    continue
                cell.alignment = text_align

    def normalize_layout(self) -> bool:
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        wb.save(self.journal_path)
        wb.close()
        return True

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
            text = self._url_from_cell(cell)
            project_id = extract_journal_project_id(text)
            if project_id:
                ids.add(project_id)
        wb.close()
        return ids

    def _find_row_by_project_id(self, ws, project_id: str) -> int | None:
        header = self._header_row(ws)
        for row in range(header + 1, ws.max_row + 1):
            cell = ws.cell(row=row, column=4)
            if isinstance(cell, MergedCell):
                continue
            if project_id in self._url_from_cell(cell):
                return row
        return None

    def update_status_by_project_id(
        self,
        project_id: str,
        *,
        status: str,
        result: str | None = None,
    ) -> bool:
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._find_row_by_project_id(ws, project_id)
        if row is None:
            wb.close()
            return False
        changed = False
        if self._writable(ws, row, 6):
            current = str(ws.cell(row=row, column=6).value or "").strip()
            if current != status:
                ws.cell(row=row, column=6, value=status)
                changed = True
        if result is not None and self._writable(ws, row, 7):
            current_result = str(ws.cell(row=row, column=7).value or "").strip()
            if current_result != result:
                ws.cell(row=row, column=7, value=result)
                changed = True
        if changed:
            wb.save(self.journal_path)
            logger.info(
                "journal_status_updated project_id=%s row=%d status=%r",
                project_id,
                row,
                status,
            )
        wb.close()
        return changed

    def update_response_by_project_id(
        self,
        project_id: str,
        response_payload: str,
    ) -> bool:
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._find_row_by_project_id(ws, project_id)
        if row is None or not self._writable(ws, row, 9):
            wb.close()
            return False
        ws.cell(row=row, column=9, value=response_payload)
        ws.cell(row=row, column=9).alignment = Alignment(vertical="top", wrap_text=True)
        wb.save(self.journal_path)
        wb.close()
        logger.info("journal_response_updated project_id=%s row=%d", project_id, row)
        return True

    @staticmethod
    def _response_cell_empty(value: object) -> bool:
        return not str(value or "").strip()

    def response_is_empty_by_project_id(self, project_id: str) -> bool:
        if not self.journal_path.exists():
            return True
        wb = load_workbook(self.journal_path)
        ws = wb.active
        row = self._find_row_by_project_id(ws, project_id)
        if row is None:
            wb.close()
            return True
        empty = self._response_cell_empty(ws.cell(row=row, column=RESPONSE_COLUMN).value)
        wb.close()
        return empty

    def update_response_by_project_id_if_empty(
        self,
        project_id: str,
        response_payload: str,
    ) -> bool:
        """Fill column I only when the cell is missing/blank. Never overwrite."""
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._find_row_by_project_id(ws, project_id)
        if row is None or not self._writable(ws, row, RESPONSE_COLUMN):
            wb.close()
            return False
        if not self._response_cell_empty(ws.cell(row=row, column=RESPONSE_COLUMN).value):
            wb.close()
            return False
        ws.cell(row=row, column=RESPONSE_COLUMN, value=response_payload)
        ws.cell(row=row, column=RESPONSE_COLUMN).alignment = Alignment(
            vertical="top", wrap_text=True
        )
        wb.save(self.journal_path)
        wb.close()
        logger.info(
            "journal_response_filled_empty project_id=%s row=%d", project_id, row
        )
        return True

    def update_notes_by_project_id(self, project_id: str, notes: str) -> bool:
        return self.update_response_by_project_id(project_id, notes)

    def remove_row_by_project_id(self, project_id: str) -> bool:
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._find_row_by_project_id(ws, project_id)
        if row is None:
            wb.close()
            return False
        ws.delete_rows(row, 1)
        wb.save(self.journal_path)
        wb.close()
        logger.info("journal_row_removed project_id=%s row=%d", project_id, row)
        return True

    def _repair_row_cells(
        self,
        ws,
        row: int,
        project_id: str,
        *,
        title: str = "",
        project_type: str | None = None,
    ) -> bool:
        changed = False
        url_text = self._url_from_cell(ws.cell(row=row, column=4)).strip()
        if url_text.startswith("http"):
            url_cell = ws.cell(row=row, column=4)
            link = getattr(url_cell, "hyperlink", None)
            if link is None or not getattr(link, "target", None):
                self._set_url_cell(ws, row, url_text)
                changed = True

        type_val = str(ws.cell(row=row, column=5).value or "").strip()
        if type_val in ("", "—", "-"):
            offer_text = str(ws.cell(row=row, column=8).value or "")
            infer_title = title or offer_text.split("\n")[0]
            new_type = (project_type or "").strip() or infer_project_type(infer_title)
            ws.cell(row=row, column=5, value=new_type)
            changed = True
        return changed

    def repair_row_by_project_id(
        self,
        project_id: str,
        *,
        title: str = "",
        project_type: str | None = None,
    ) -> bool:
        if not self.journal_path.exists():
            return False
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._find_row_by_project_id(ws, project_id)
        if row is None:
            wb.close()
            return False
        changed = self._repair_row_cells(
            ws,
            row,
            project_id,
            title=title,
            project_type=project_type,
        )
        if changed:
            wb.save(self.journal_path)
        wb.close()
        return changed

    def repair_all_rows(
        self,
        *,
        titles: dict[str, str] | None = None,
        project_types: dict[str, str] | None = None,
    ) -> int:
        if not self.journal_path.exists():
            return 0
        titles = titles or {}
        project_types = project_types or {}
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        changed_rows = 0
        for row in range(2, ws.max_row + 1):
            if not self._writable(ws, row, 4):
                continue
            url_text = self._url_from_cell(ws.cell(row=row, column=4))
            match = re.search(r"/projects/(\d+)", url_text.replace("\\", "/"))
            if not match:
                continue
            project_id = match.group(1)
            if self._repair_row_cells(
                ws,
                row,
                project_id,
                title=titles.get(project_id, ""),
                project_type=project_types.get(project_id),
            ):
                changed_rows += 1
        if changed_rows:
            wb.save(self.journal_path)
        wb.close()
        return changed_rows

    def append_submission(
        self,
        project: ProjectFull,
        score: GptScoreResult,
        response_text: str,
    ) -> int:
        self._ensure_workbook()
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._next_row(ws)

        platform_label = PLATFORM_DISPLAY.get(project.platform, project.platform)
        response_payload = format_response_payload(
            response_text,
            price=project.desired_budget or project.max_budget,
        )

        ws.cell(row=row, column=1, value=self._next_number(ws))
        ws.cell(row=row, column=2, value=date.today())
        ws.cell(row=row, column=3, value=platform_label)
        self._set_url_cell(ws, row, project.url)
        ws.cell(row=row, column=5, value=score.suggested_project_type)
        ws.cell(row=row, column=6, value="Отправлен")
        ws.cell(row=row, column=7, value="Жду ответа")
        ws.cell(row=row, column=8, value=project.full_description)
        ws.cell(row=row, column=9, value=response_payload)
        ws.cell(row=row, column=8).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row=row, column=9).alignment = Alignment(vertical="top", wrap_text=True)
        offered_price = project.desired_budget or project.max_budget
        self._write_kpi_cells(
            ws,
            row,
            project=project,
            score=score,
            response_text=response_text,
            price=offered_price,
        )

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
        status: str = "Подготовлен",
        result: str = "Жду ответа",
    ) -> int:
        self._ensure_workbook()
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._next_row(ws)

        platform_label = PLATFORM_DISPLAY.get(project.platform, project.platform)
        response_payload = format_response_payload(
            response_text,
            price=price,
            delivery_days=delivery_days,
        )

        ws.cell(row=row, column=1, value=self._next_number(ws))
        ws.cell(row=row, column=2, value=date.today())
        ws.cell(row=row, column=3, value=platform_label)
        self._set_url_cell(ws, row, project.url)
        ws.cell(row=row, column=5, value=score.suggested_project_type)
        ws.cell(row=row, column=6, value=status)
        ws.cell(row=row, column=7, value=result)
        ws.cell(row=row, column=8, value=project.full_description)
        ws.cell(row=row, column=9, value=response_payload)
        ws.cell(row=row, column=8).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row=row, column=9).alignment = Alignment(vertical="top", wrap_text=True)
        self._write_kpi_cells(
            ws,
            row,
            project=project,
            score=score,
            response_text=response_text,
            price=price,
        )

        wb.save(self.journal_path)
        logger.info(
            "journal_prepared project_id=%s row=%d status=%r",
            project.project_id,
            row,
            status,
        )
        return row

    def append_kwork_offer_status(
        self,
        *,
        project_id: str,
        title: str,
        status: str,
        result: str,
        project_type: str | None = None,
    ) -> int:
        self._ensure_workbook()
        wb = load_workbook(self.journal_path)
        ws = wb.active
        self._apply_journal_layout(ws)
        row = self._next_row(ws)
        url = kwork_project_url(project_id)
        type_label = (project_type or "").strip() or infer_project_type(title)

        ws.cell(row=row, column=1, value=self._next_number(ws))
        ws.cell(row=row, column=2, value=date.today())
        ws.cell(row=row, column=3, value="Kwork")
        self._set_url_cell(ws, row, url)
        ws.cell(row=row, column=5, value=type_label)
        ws.cell(row=row, column=6, value=status)
        ws.cell(row=row, column=7, value=result)
        ws.cell(row=row, column=8, value=title.strip())
        ws.cell(row=row, column=9, value="")
        ws.cell(row=row, column=8).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row=row, column=9).alignment = Alignment(vertical="top", wrap_text=True)
        self._write_kpi_cells(
            ws,
            row,
            response_text="",
            project_type_title=title,
        )

        wb.save(self.journal_path)
        wb.close()
        logger.info(
            "journal_offer_synced project_id=%s row=%d status=%r",
            project_id,
            row,
            status,
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
