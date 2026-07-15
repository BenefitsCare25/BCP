"""Cell/text normalization helpers and shared regexes for slip parsing."""
from __future__ import annotations

import re

from app.services.excel_reader import Cell

_SKIP_PHRASES: tuple[str, ...] = (
    "premium include",
    "(premium",
    "rate :",
    "subj to gst",
    "annual premium",
    "figures above",
)

_RATE_CODE = re.compile(r"^[A-Z0-9 ,/-]{1,10}$", re.IGNORECASE)
_PLAN_INLINE = re.compile(
    r"^\s*plan\s+([A-Za-z0-9/ ]+?)\s*[:\-—]\s*(.+)$", re.IGNORECASE
)
_FOOTNOTE_SPLIT = re.compile(r"\s*\*")
_PREMIUM_TRAILER = re.compile(r"\s*\(premium includes[^)]*\)", re.IGNORECASE)


def _norm(value: Cell) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _lower(value: Cell) -> str:
    return _norm(value).lower()


def _non_empty(value: Cell) -> bool:
    return value is not None and len(str(value).strip()) > 0


def _cell_text(row: list[Cell], col: int) -> str:
    """Normalized text at ``row[col]``, or "" if out of range / empty."""
    if 0 <= col < len(row) and _non_empty(row[col]):
        return _norm(row[col])
    return ""


def _int_code(value: Cell | str) -> str:
    """Normalize a numeric code/number by stripping the xlrd float artifact.

    xlrd yields integral cells as floats, so plan codes and benefit numbers
    arrive as "1.0"; collapse those to "1". A genuine fractional value ("1.1"
    sub-number, "1.5" code) and non-numeric input ("A") are returned trimmed and
    unchanged — never truncated to the integer part.
    """
    s = _norm(value)
    try:
        f = float(s)
    except (ValueError, TypeError):
        return s
    return str(int(f)) if f == int(f) else s


# Plan headers / rate keys bundle several codes with these separators
# ("1A/1B", "B1 & B", "1, 2") and may annotate each with a member type
# ("1 - Employees"). Both the rate matcher and the reconciler must agree on how a
# composite code splits, or a code resolved by one stage is dropped by the other.
_PLAN_CODE_SEP = re.compile(r"\s*[/&,]\s*")


def split_plan_codes(code: str) -> list[str]:
    """Expand a (possibly composite) plan code into its individual codes.

    "1A/1B" -> ["1A", "1B"]; "B1 & B" -> ["B1", "B"]; "1 - Employees" -> ["1"].
    Member-type suffixes (after a dash or whitespace) are dropped; xlrd float
    artifacts collapsed. Case + first-seen order preserved.
    """
    out: list[str] = []
    seen: set[str] = set()
    for part in _PLAN_CODE_SEP.split(code or ""):
        part = part.strip()
        if not part:
            continue
        lead = _int_code(re.split(r"\s*-\s*|\s+", part, maxsplit=1)[0].strip())
        if lead and lead not in seen:
            seen.add(lead)
            out.append(lead)
    return out


def _row_text(row: list[Cell]) -> str:
    return " ".join(str(c) for c in row if _non_empty(c))


def _safe_float(row: list[Cell], col: int) -> float | None:
    if col < 0 or col >= len(row):
        return None
    v = row[col]
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


# A *complete* currency amount at the start of an annotated cell: either
# currency-prefixed ("$3,169.80 (Subject to Minimum …)") or a bare figure
# immediately followed by an annotation paren or end-of-cell ("3,169.80 (est)").
# This deliberately does NOT match a leading number that is part of other prose
# ("33 travellers @ $96"), which would yield a bogus premium.
_CURRENCY_AMOUNT = re.compile(
    r"^\s*(?:S?\$\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:\(|$))"
)


def _currency_amount(row: list[Cell], col: int) -> float | None:
    """The currency figure in a (possibly annotated) cell.

    Falls back to the bare-number path of ``_safe_float`` first; only when that
    fails (the cell is annotated text like "$3,169.80 (Subject to …)") does it
    pull the leading amount — and only when that amount is currency-prefixed or
    is the whole leading token, never a number embedded in prose. Returns None
    when no complete figure is present.
    """
    direct = _safe_float(row, col)
    if direct is not None:
        return direct
    if col < 0 or col >= len(row) or row[col] is None:
        return None
    m = _CURRENCY_AMOUNT.match(str(row[col]).strip())
    if not m:
        return None
    try:
        return float((m.group(1) or m.group(2)).replace(",", ""))
    except (ValueError, AttributeError):
        return None
