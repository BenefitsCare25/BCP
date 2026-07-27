"""Shared worksheet styling + row helpers for the slip export.

The reference slips are gridded tables, not floating text, so every table row
gets a full thin border and the header/label blocks share one vocabulary of
fonts. Keeping it here means the Basis, Rate and Schedule sections can't drift
apart visually.
"""
from __future__ import annotations

from typing import Any

from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

TITLE = Font(bold=True, size=13)
SECTION = Font(bold=True, size=11)
HEADER = Font(bold=True)
NOTE = Font(italic=True, size=9)
PLAIN = Font(bold=False)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center")

MONEY = "#,##0.00"
COUNT = "#,##0"

_THIN = Side(style="thin")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def set_widths(ws: Worksheet, widths: dict[int, int]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def style_row(
    ws: Worksheet, font: Font | None = None, wrap_cols: tuple[int, ...] = ()
) -> int:
    row = ws.max_row
    if font is not None:
        for cell in ws[row]:
            cell.font = font
    for col in wrap_cols:
        ws.cell(row=row, column=col).alignment = WRAP
    return row


def border_row(ws: Worksheet, first: int, last: int, row: int | None = None) -> None:
    """Grid the table cells so the workbook reads like the reference slips."""
    r = row or ws.max_row
    for col in range(first, last + 1):
        ws.cell(row=r, column=col).border = BORDER


def label_value_rows(ws: Worksheet, rows: list[tuple[str, str] | None]) -> None:
    """Header block: label in col A, value in col C (reference layout).

    ``None`` emits a spacer row.
    """
    for entry in rows:
        if entry is None:
            ws.append([])
            continue
        label, value = entry
        ws.append([label, "", value])
        row = ws.max_row
        ws.cell(row=row, column=1).font = HEADER
        ws.cell(row=row, column=3).alignment = WRAP


def numeric_or_text(v: Any) -> Any:
    """A pure amount becomes a number (so Excel formats it); anything else — a
    salary-multiple expression, a qualifier — stays verbatim text."""
    if isinstance(v, (int, float)):
        return v
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return v
