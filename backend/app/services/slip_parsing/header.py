"""Policy-header scan and column-header row location."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.excel_reader import Cell
from app.services.slip_parsing.models import PolicyHeader
from app.services.slip_parsing.text import _lower, _non_empty, _norm, _row_text


@dataclass
class _HeaderScan:
    header: PolicyHeader
    basis_row: int


# A number followed (within a short gap) by a birthday qualifier. ANB = "age
# next birthday", ALB = "age last birthday". The gap absorbs "years", "(age ",
# etc. between the number and the qualifier.
_AGE_BIRTHDAY_RE = re.compile(
    r"(\d{1,3})[^\d]{0,25}?(next\s+birthday|last\s+birthday|anb|alb)\b",
    re.IGNORECASE,
)


def _age_from_birthday(text: str | None) -> str | None:
    """Age from a 'N next/last birthday' (or ANB/ALB) phrase, normalised to the
    canonical age-NEXT-birthday convention (relative to the renewal date):
    next birthday/ANB keep N; last birthday/ALB give N+1 (age N last birthday
    = age N+1 next birthday). None when no such phrase (so a bare sum-insured
    figure in the same cell is never mistaken for an age)."""
    if not text:
        return None
    m = _AGE_BIRTHDAY_RE.search(str(text))
    if not m:
        return None
    n = int(m.group(1))
    qual = m.group(2).lower()
    return str(n + 1 if qual.startswith("last") or qual == "alb" else n)


def _normalize_age(text: str | None) -> str | None:
    """Like `_age_from_birthday` but falls back to a bare number (e.g. 'Last
    entry age: 80.0' or '74') when no birthday qualifier is present."""
    by_birthday = _age_from_birthday(text)
    if by_birthday is not None:
        return by_birthday
    m = re.search(r"\b(\d{1,3})(?:\.0+)?\b", str(text or ""))
    return str(int(m.group(1))) if m else None


def _up_to_age(text: str | None) -> str | None:
    """The 'renewable up to age N …' / 'up to age N' clause from an eligibility
    sentence, including any trailing birthday qualifier (so it normalises)."""
    if not text:
        return None
    m = re.search(r"up\s+to\s+(?:age\s+)?(\d{1,3}[^,;.]{0,30})", str(text), re.IGNORECASE)
    return m.group(1) if m else None


def _find_nel_text(rows: list[list[Cell]]) -> str | None:
    """The Non-Evidence / Free-Cover Limit row text (lives in the footer, after
    Basis of Cover, so it's scanned separately from the header block)."""
    for row in rows:
        text = _row_text(row or [])
        if re.search(r"non[\s-]*evidence\s+limit|free\s+cover\s+limit", text, re.IGNORECASE):
            return text
    return None


def _nel_amount(text: str | None) -> float | None:
    """The NEL dollar figure: first S$ amount in the row ("Sum insured
    exceeding S$500,000 or existing FCL …" → 500000.0)."""
    if not text:
        return None
    m = re.search(r"S?\$\s*([\d,]+(?:\.\d+)?)", str(text))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _header_value(row: list[Cell], exclude: str) -> str | None:
    """First non-empty cell on a label row that isn't the label itself.

    Labels sit in col 0 (e.g. "Insured :") and the value in a later cell; the
    `exclude` pattern matches the label word so it's skipped.
    """
    for v in row:
        if _non_empty(v) and not re.search(exclude, str(v), re.IGNORECASE):
            return _norm(v)
    return None


# (PolicyHeader field, label regex matched on the row head, exclude regex that
#  marks the label cell so the value cell is taken, optional char cap). The
#  `eligibility_date` row also contains "eligibility", so its precise label
#  (requires " date :") is checked and the bare-eligibility label requires ":"
#  immediately after the word, so the two never collide.
_HEADER_LABELS: tuple[tuple[str, str, str, int | None], ...] = (
    ("policyholder", r"^\s*policyholder", r"policyholder", None),
    ("insured", r"^\s*insured\s*:", r"insured", None),
    ("insurer", r"^\s*insurer\s*:", r"insurer", None),
    ("business", r"^\s*business\s*:", r"business", 300),
    ("address", r"address\s*:", r"address", 300),
    ("period", r"period\s+of\s+insurance", r"period", None),
    ("policy_no", r"^\s*policy\s*no", r"policy", None),
    ("admin_basis", r"type\s+of\s+administration", r"administration", None),
    ("eligibility_date", r"^\s*eligibility\s+date\s*:", r"eligibility", None),
    ("eligibility", r"^\s*eligibility\s*:", r"eligibility", 300),
    ("last_entry_age", r"last\s+entry\s+age", r"entry", None),
)


def _scan_policy_header(rows: list[list[Cell]]) -> _HeaderScan:
    vals: dict[str, str | None] = {key: None for key, *_ in _HEADER_LABELS}
    basis_row = -1
    for i in range(min(len(rows), 30)):
        row = rows[i] or []
        text = _row_text(row)
        head = text[:60]
        for key, label, exclude, limit in _HEADER_LABELS:
            if vals[key] is None and re.search(label, head, re.IGNORECASE):
                value = _header_value(row, exclude)
                vals[key] = value[:limit] if (value and limit) else value
        if re.search(r"basis\s+of\s+cover", text, re.IGNORECASE):
            basis_row = i
            break
    # Derived ages: Last entry age -> plain number; Employee age limit from the
    # "renewable up to age N" clause; No-underwriting age from the (footer)
    # Non-Evidence Limit row.
    vals["last_entry_age"] = _normalize_age(vals["last_entry_age"])
    vals["employee_age_limit"] = _normalize_age(_up_to_age(vals["eligibility"]))
    nel_text = _find_nel_text(rows)
    vals["age_limit_no_underwriting"] = _age_from_birthday(nel_text)
    vals["non_evidence_limit"] = _nel_amount(nel_text)
    return _HeaderScan(PolicyHeader(**vals), basis_row)


def _find_column_header_row(rows: list[list[Cell]], basis_idx: int) -> int:
    """Find the column-header row.

    When `basis_idx` >= 0, scan the next 5 rows (the brief's documented case).
    When `basis_idx` < 0 (no "Basis of Cover" anchor — happens on Dental
    sheets and some abridged templates), scan the first 30 rows.

    The header row must contain "category". "Insured" + ("plan" | "participation"
    | "number" | "trips") helps disambiguate from instructional text that
    happens to contain the word "category".

    The scan includes the basis row itself: some slips (VDL WICA) merge the
    column header into the "Basis of Cover :" row instead of printing it below.
    """
    start = basis_idx if basis_idx >= 0 else 0
    stop = (
        min(basis_idx + 6, len(rows))
        if basis_idx >= 0
        else min(30, len(rows))
    )
    for i in range(start, stop):
        text = _lower(_row_text(rows[i] or []))
        if "category" not in text:
            continue
        if "insured" in text or any(
            k in text for k in ("participation", "plan", "trips", "number of")
        ):
            return i
    return -1
