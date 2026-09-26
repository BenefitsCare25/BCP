"""Recognize explicit employee code clauses shared by mapping and matching."""

from __future__ import annotations

import re

JOB_CATEGORY_RE = re.compile(
    r"\bjob\s+categor(?:y|ies)\s*:\s*([^)]*)",
    re.IGNORECASE,
)
HAY_GRADE_RE = re.compile(
    r"\bhay\s+job\s+grade\s+(.+?)(?=(?:\(|/|\bhay\s+job\s+grade\b|$))",
    re.IGNORECASE,
)
GRADE_RE = re.compile(
    r"\bgrade\s+([a-z0-9]+(?:\s+(?:to|and|&)\s+[a-z0-9]+)?)",
    re.IGNORECASE,
)


def explicit_grade_clauses(text: str) -> list[str]:
    """Return the highest-priority code clauses expressed by the slip."""
    job_categories = [match.group(1) for match in JOB_CATEGORY_RE.finditer(text)]
    if job_categories:
        return job_categories
    hay_grades = [match.group(1) for match in HAY_GRADE_RE.finditer(text)]
    if hay_grades:
        return hay_grades
    return [match.group(1) for match in GRADE_RE.finditer(text)]


def has_explicit_grade_clause(text: str) -> bool:
    return bool(explicit_grade_clauses(text))


# "J and P" / "C & G": two bare codes joined by a conjunction. Bounded to short
# codes so "E7 and above" is never read as a code called ABOVE.
_CODE_PAIR_RE = re.compile(r"([A-Z0-9]{1,3})\s+(?:AND|&)\s+([A-Z0-9]{1,3})")


def split_code_pair(piece: str) -> list[str]:
    """``"J and P"`` → ``["J", "P"]``; any other piece is returned unchanged."""
    text = piece.strip().upper()
    if match := _CODE_PAIR_RE.fullmatch(text):
        return [match.group(1), match.group(2)]
    return [piece]


def grade_family_values(letter: str, values: list[object]) -> list[object]:
    """Roster codes in one letter family: ``J`` → J1, J2, JA, JB.

    CDL's GPA sheet writes "Grade J and P" where the life sheets spell the same
    cohort "J1 to J3, JA to JC". A lone letter only means a family when the
    roster has no literal code equal to it.
    """
    key = letter.strip().upper()
    if len(key) != 1 or not key.isalpha():
        return []
    codes = [str(v).strip().upper() for v in values]
    if key in codes:
        return []
    return [
        value
        for value, code in zip(values, codes, strict=True)
        if re.fullmatch(rf"{key}(?:\d{{1,2}}|[A-Z])", code)
    ]
