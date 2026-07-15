"""Tolerant parser for free-text "Period of Insurance" values from slips.

Mirrors the frontend `parsePeriodOfInsurance` (lib/policy-year.ts): day-first
(Singapore convention) numeric dates, textual months, basic calendar
validation. Returns None when two valid dates with end >= start can't be
confidently extracted — callers must treat None as "unknown", never as a match.
"""
from __future__ import annotations

import calendar
import re
from datetime import date

_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DATE_TOKEN = re.compile(
    r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
)
_NUMERIC = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$")
_DMY = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})$")
_MDY = re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")


def _valid(year: int, month: int, day: int) -> date | None:
    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= calendar.monthrange(year, month)[1]):
        return None
    return date(year, month, day)


def _parse_one(token: str) -> date | None:
    t = token.strip()
    num = _NUMERIC.match(t)
    if num:
        a, b = int(num.group(1)), int(num.group(2))
        year = int(num.group(3))
        if year < 100:
            year += 2000
        # A component > 12 can only be a day; ambiguous (both <= 12) → day-first.
        if b > 12 and a <= 12:
            month, day = a, b
        else:
            day, month = a, b
        return _valid(year, month, day)
    dmy = _DMY.match(t)
    if dmy:
        day, mon, year = int(dmy.group(1)), dmy.group(2), int(dmy.group(3))
    else:
        mdy = _MDY.match(t)
        if not mdy:
            return None
        mon, day, year = mdy.group(1), int(mdy.group(2)), int(mdy.group(3))
    month = _MONTHS.get(mon.lower())
    if not month:
        return None
    return _valid(year, month, day)


def parse_period_of_insurance(text: str | None) -> tuple[date, date] | None:
    """Parse e.g. "01/01/2026 - 31/12/2026" into (start, end) dates."""
    if not text:
        return None
    dates: list[date] = []
    for match in _DATE_TOKEN.finditer(text):
        parsed = _parse_one(match.group(0))
        if parsed:
            dates.append(parsed)
        if len(dates) == 2:
            break
    if len(dates) < 2:
        return None
    start, end = dates[0], dates[1]
    if end < start:
        return None
    return start, end
