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
