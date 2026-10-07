"""Cell normalisation for Employee Listing values."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from app.services.excel_reader import Cell

# Excel's 1900 date system: serial 1 = 1900-01-01, with the phantom 1900-02-29.
_EXCEL_EPOCH = date(1899, 12, 30)
_TEXT_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d-%b-%y", "%d/%b/%y", "%d-%b-%Y", "%d/%b/%Y",
                 "%d %b %Y", "%d %B %Y", "%d.%m.%Y")


def text(value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def as_date(value: Cell) -> date | None:
    """A calendar date from an ISO string, an Excel serial or day-first text.

    Two-digit and day-first text ("31-05-1957", "17/Jan/59") is accepted because
    Singapore listings write dates day-first; month-first text is never guessed.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        serial = float(value)
        return _EXCEL_EPOCH + timedelta(days=int(serial)) if 1 <= serial < 80000 else None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    for fmt in _TEXT_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def as_number(value: Cell) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,$\s]|S\$|SGD", "", str(value), flags=re.I)
    try:
        return float(cleaned)
    except ValueError:
        return None



def relationship(value: Cell) -> str | None:
    """"spouse" / "child" from the listing's codes ("S", "Spouse - S", "Child - C")."""
    word = text(value).lower()
    if not word:
        return None
    if re.match(r"^(s\b|spouse|wife|husband)", word):
        return "spouse"
    if re.match(r"^(c\b|child|son|daughter)", word):
        return "child"
    return None


def family_group(value: Cell) -> str | None:
    word = text(value).upper().replace(" ", "")
    return word if word in {"EO", "ES", "EC", "EF"} else None


def admin_type(value: Cell) -> str | None:
    word = text(value).lower()
    if word.startswith("name"):
        return "named"
    if word.startswith("head"):
        return "headcount"
    return None


def normalize(role: str, value: Cell) -> Any:
    """The typed value for a column role; text for roles without a type."""
    if role in {"date_of_birth", "date_of_hire", "mu_letter_member", "mu_letter_insurer",
                "acceptance_date", "last_day_of_service", "retrenchment_start",
                "retrenchment_end", "reemployment_start", "reemployment_end"}:
        return as_date(value)
    if role in {"salary", "present_si", "eligible_si", "pending_si", "last_accepted_si",
                "premium", "premium_gst", "age"}:
        return as_number(value)
    if role == "relationship":
        return relationship(value)
    if role == "family_group":
        return family_group(value)
    if role == "admin_type":
        return admin_type(value)
    out = text(value)
    return out or None
