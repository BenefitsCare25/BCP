"""Spreadsheet formula-injection guard shared by every workbook export.

openpyxl stores any string starting with = + - @ (or a leading control char) as
a live formula, so a roster value like ``=HYPERLINK(...)`` or ``=cmd|...`` would
execute in the recipient's Excel when they open our file. Prefixing such
strings with an apostrophe makes Excel treat them as literal text.

The leader tuple lives in `roster_parser` because that module owns the READ
half (`unescape_formula_guard`): exported workbooks are uploaded back, so an
escape with no matching unescape turns "+60186448967" into a phantom change on
every upload.
"""
from __future__ import annotations

from collections.abc import Sequence

from openpyxl.worksheet.worksheet import Worksheet

from app.services.roster_parser import _FORMULA_LEADERS


def safe_cell(value: object) -> object:
    """``value`` with a formula-leading string neutralized; anything else as-is."""
    if isinstance(value, str) and value and value[0] in _FORMULA_LEADERS:
        return "'" + value
    return value


def append_safe(ws: Worksheet, row: Sequence[object]) -> None:
    """Append a row with every string cell neutralized against formula
    injection (see ``safe_cell``)."""
    ws.append([safe_cell(v) for v in row])
