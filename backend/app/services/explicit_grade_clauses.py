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
# A grade list keeps one code shape (5, 08 / M1, E10 / J, AF) so trailing
# prose such as "Grade A1, S Pass" or "Grade M1, 3x salary" is not read as
# codes; a letter followed by "Pass" is a pass type ("A, S Pass"), not a grade.
_CODE_JOIN = r"(?:\s*[-\u2013\u2014]\s*|\s+to\s+|\s*[,/]\s*(?:(?:and|&)\s+)?|\s+(?:and|&)\s+)"
_CODE_END = r"(?![a-z0-9])(?!\s+pass\b)"
_CODE_LIST = "|".join(
    rf"{code}{_CODE_END}(?:{_CODE_JOIN}{code}{_CODE_END})*"
    for code in (r"\d+", r"[a-z]{1,3}\d+[a-z]?", r"(?-i:[A-Z]{1,2})")
)
# Any other code shape ("1A and 1B", "MGR to EXE") keeps one joined pair.
_OTHER_CODES = r"[a-z0-9]+(?:\s+(?:to|and|&)\s+(?!(?:above|below)\b)[a-z0-9]+)?"
OPEN_BOUND = r"\s*\+|\s+(?:(?:and|&)\s+)?(?:above|below)\b"
GRADE_RE = re.compile(
    rf"\b(?:grade|grades(?=\s+(?:{_CODE_LIST})))\s+"
    rf"((?:{_CODE_LIST}|{_OTHER_CODES})(?:{OPEN_BOUND})?)",
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


# "J and P" / "C & G" / "5 and 6 and 7": bare codes joined by conjunctions.
# Bounded to short codes so "E7 and above" is never read as a code called ABOVE.
_CODE_CONJUNCTION_RE = re.compile(r"[A-Z0-9]{1,3}(?:\s+(?:AND|&)\s+[A-Z0-9]{1,3})+")


def split_code_pair(piece: str) -> list[str]:
    """``"J and P"`` → ``["J", "P"]``; any other piece is returned unchanged."""
    text = piece.strip().upper()
    if _CODE_CONJUNCTION_RE.fullmatch(text):
        return re.split(r"\s+(?:AND|&)\s+", text)
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
