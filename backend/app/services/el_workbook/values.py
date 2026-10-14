"""Cell normalisation for Employee Listing values."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from app.services.dual_coverage import normalize_name
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


# "WEF 29 Aug 2026", "w.e.f. 14/10/2025" — a four-digit year only, so a typo
# such as "11 May 206" yields nothing rather than year 206.
_WEF = re.compile(
    r"\bw\.?\s*e\.?\s*f\.?\s*:?\s*(\d{1,2})(?:st|nd|rd|th)?[\s/.-]+([a-z]{3,9}|\d{1,2})"
    r"\.?[\s/.-]+(\d{4})\b",
    re.IGNORECASE,
)


def wef_date(value: Cell) -> date | None:
    """The date a remark says a change takes effect from ("WEF 29 Aug 2026")."""
    match = _WEF.search(text(value))
    if match is None:
        return None
    day, month, year = match.groups()
    if month.isdigit():
        return as_date(f"{day}/{month}/{year}")
    return as_date(f"{day} {month[:3].title()} {year}")


# Words that never tell two people apart: salutations and the "bin" joiners of
# Malay names. The "s/o", "d/o", "a/l", "a/p" joiners of Indian names are
# removed before splitting, so a lone "S" or "D" stays an initial.
_NAME_NOISE = frozenset({
    "mr", "mrs", "ms", "mdm", "madam", "miss", "dr", "mx", "sir",
    "bin", "binte", "binti", "bte",
})
_PARENT_JOINER = re.compile(r"\b(?:[sd]\s*/\s*o|a\s*/\s*[lp])\b", re.IGNORECASE)
# One spelling for names commonly abbreviated on rosters.
_NAME_VARIANTS = {
    "mohd": "muhammad", "muhd": "muhammad", "mohamed": "muhammad", "mohammad": "muhammad",
    "mohammed": "muhammad", "muhamad": "muhammad", "mohamad": "muhammad",
}


def _name_tokens(value: Cell) -> frozenset[str]:
    """A name's words on the platform's one name normalisation
    (``dual_coverage.normalize_name``), minus words that never tell people
    apart."""
    words = normalize_name(_PARENT_JOINER.sub(" ", text(value))).split()
    return frozenset(_NAME_VARIANTS.get(w, w) for w in words if w not in _NAME_NOISE)


def same_name(a: Cell, b: Cell) -> bool:
    """The same words, whatever their order, case, punctuation, salutations and
    abbreviations ("Loong, Li Juan" = "Li Juan Loong", "Mr Tan Ah Kow" =
    "TAN AH KOW")."""
    ta = _name_tokens(a)
    return bool(ta) and ta == _name_tokens(b)


def same_person(a: Cell, b: Cell) -> bool:
    """Whether two spellings could be the same person: ``same_name``, or one
    name carrying an extra given name ("Tan Ah Kow" = "Tan Ah Kow Eric"), or a
    one-letter slip between names of the same length ("Lim Mei Ling" = "Lim
    Mei Lin").

    Initials tell people apart ("S. Kumar" is not "D. Kumar"), and a two-word
    name is a part of too many longer ones ("Tan Wei" is not "Tan Wei Ming").
    Tolerant by design: use ``same_name`` to choose between people.
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if {t for t in ta if len(t) == 1} != {t for t in tb if len(t) == 1}:
        return False
    smaller, larger = sorted((ta, tb), key=len)
    if len(smaller) >= 3 and smaller < larger:
        return True
    return len(ta) == len(tb) and SequenceMatcher(
        None, " ".join(sorted(ta)), " ".join(sorted(tb))
    ).ratio() >= 0.9


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
