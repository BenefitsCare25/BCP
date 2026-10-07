"""Clause resolvers: explicit grade codes, pass types, and list clauses."""

from __future__ import annotations

import re
from typing import Any

from app.services.eligibility_mapping.base import (
    _MAX_SET_VALUES,
    AttributeValueCatalog,
    Rule,
)
from app.services.eligibility_mapping.vocabulary import (
    _attribute_rank,
    _matched_values,
    _sequence_spans,
    _tokens,
)
from app.services.explicit_grade_clauses import (
    JOB_CATEGORY_RE,
    explicit_grade_clauses,
    grade_family_values,
    split_code_pair,
)


def _alpha_rank(value: str) -> int:
    rank = 0
    for char in value.upper():
        if not "A" <= char <= "Z":
            return -1
        rank = rank * 26 + ord(char) - ord("A") + 1
    return rank


def _alpha_code(rank: int) -> str:
    """Inverse of :func:`_alpha_rank` for spreadsheet-style letter codes."""

    chars: list[str] = []
    while rank > 0:
        rank, remainder = divmod(rank - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def _ordered_code(value: Any) -> tuple[str, int] | None:
    """Natural ordering for real-world grade codes (08, A7, AA, AF)."""

    text = str(value).strip().upper()
    if match := re.fullmatch(r"([A-Z]*)(\d+)", text):
        return f"num:{match.group(1)}", int(match.group(2))
    if re.fullmatch(r"[A-Z]+", text):
        return "alpha", _alpha_rank(text)
    return None


def _expand_finite_grade_piece(piece: str) -> list[str] | None:
    """Expand one literal code or closed range exactly as written on the slip."""

    text = piece.strip().upper()
    if not text:
        return []
    if single := re.fullmatch(r"[A-Z]*\d+|[A-Z]+", text):
        return [single.group(0)]
    match = re.fullmatch(
        r"([A-Z]*\d+|[A-Z]+)\s*(?:TO|-)\s*([A-Z]*\d+|[A-Z]+)",
        text,
    )
    if match is None:
        return []
    start_text, end_text = match.groups()
    numeric_start = re.fullmatch(r"([A-Z]*)(\d+)", start_text)
    numeric_end = re.fullmatch(r"([A-Z]*)(\d+)", end_text)
    if numeric_start and numeric_end and numeric_start.group(1) == numeric_end.group(1):
        prefix = numeric_start.group(1)
        lo, hi = int(numeric_start.group(2)), int(numeric_end.group(2))
        if lo > hi:
            return []
        if hi - lo + 1 > _MAX_SET_VALUES:
            return None
        width = max(len(numeric_start.group(2)), len(numeric_end.group(2)))
        padded = numeric_start.group(2).startswith("0") or numeric_end.group(2).startswith("0")
        return [
            f"{prefix}{number:0{width}d}" if padded else f"{prefix}{number}"
            for number in range(lo, hi + 1)
        ]
    if re.fullmatch(r"[A-Z]+", start_text) and re.fullmatch(r"[A-Z]+", end_text):
        lo, hi = _alpha_rank(start_text), _alpha_rank(end_text)
        if lo <= 0 or lo > hi:
            return []
        if hi - lo + 1 > _MAX_SET_VALUES:
            return None
        return [_alpha_code(rank) for rank in range(lo, hi + 1)]
    return []


def _values_from_grade_clause(clause: str, values: list[Any]) -> list[Any]:
    """Expand explicit closed ranges, then add roster-backed open ranges.

    Closed ranges are authoritative insurer data, so every stated value is kept
    even when no active employee currently occupies that grade. Open-ended
    ranges still expand only over known roster values because the slip supplies
    no finite upper/lower bound.
    """

    selected: list[Any] = []
    selected_keys: set[str] = set()

    def add(value: Any) -> None:
        key = str(value).strip().casefold()
        if key and key not in selected_keys:
            selected_keys.add(key)
            selected.append(value)

    pieces = [
        part
        for piece in re.split(r"\s*[,;/]\s*", clause)
        for part in split_code_pair(piece)
    ]
    for piece in pieces:
        if family := grade_family_values(piece, values):
            for value in family:
                add(value)
            continue
        expanded = _expand_finite_grade_piece(piece)
        if expanded is None:
            return []
        for value in expanded:
            add(value)
    for value in _matched_values(clause, values):
        add(value)

    for match in re.finditer(
        r"\b([a-z]*\d+|[a-z]+)\s+(?:and\s+)?above\b",
        clause,
        re.IGNORECASE,
    ):
        start = _ordered_code(match.group(1))
        if start is None:
            continue
        for value in sorted(values, key=lambda item: _ordered_code(item) or ("", -1)):
            ordered = _ordered_code(value)
            if ordered is not None and ordered[0] == start[0] and ordered[1] >= start[1]:
                add(value)

    return selected


def _oversized_explicit_grade_clauses(text: str) -> list[str]:
    """Identify source ranges that exceed the safe finite expansion limit."""

    clauses = explicit_grade_clauses(text)
    return [
        clause.strip()
        for clause in clauses
        if any(
            _expand_finite_grade_piece(piece) is None
            for piece in re.split(r"\s*[,;/]\s*", clause)
        )
    ]


def _explicit_grade_mapping(
    text: str, catalog: AttributeValueCatalog
) -> tuple[str | None, list[Any], list[str]]:
    """Read explicit job-category/grade clauses before vague title wording."""

    job_category_clauses = list(JOB_CATEGORY_RE.finditer(text))
    clauses = explicit_grade_clauses(text)
    if not clauses:
        return None, [], []

    # Placement slips and employee templates do not always use the same label:
    # CDL calls the codes "Job category" while its roster stores them in
    # ``job_grade``. Select the company field whose actual values resolve the
    # most clauses instead of hard-coding a single canonical column.
    candidate_ids = (
        (
            "job_category",
            "job_grade",
            "grade",
            "hay_job_grade",
            "class",
            "employee_category",
            "category",
        )
        if job_category_clauses
        else (
            "job_grade",
            "grade",
            "hay_job_grade",
            "class",
            "job_category",
            "employee_category",
            "category",
        )
    )
    candidates: list[tuple[int, int, str, list[Any], list[str]]] = []
    for order, attribute_id in enumerate(candidate_ids):
        values = catalog.values.get(attribute_id, [])
        if not values:
            continue
        selected: list[Any] = []
        unresolved: list[str] = []
        for clause in clauses:
            resolved = _values_from_grade_clause(clause, values)
            if not resolved:
                unresolved.append(clause.strip())
            for value in resolved:
                if value not in selected:
                    selected.append(value)
        if selected:
            observed_keys = {str(value).strip().casefold() for value in values}
            observed_matches = sum(
                str(value).strip().casefold() in observed_keys for value in selected
            )
            # Closed ranges may name future hires absent from today's roster.
            # Trust a dedicated grade/code field in that case, but never infer
            # unseen codes from a broad text field such as ``category``.
            if observed_matches == 0 and attribute_id not in {
                "job_category",
                "job_grade",
                "grade",
                "hay_job_grade",
            }:
                continue
            candidates.append((-observed_matches, order, attribute_id, selected, unresolved))
    if not candidates:
        return None, [], [clause.strip() for clause in clauses]
    _, _, attribute_id, selected, unresolved = min(candidates)
    return attribute_id, selected, unresolved


def _bargainable_mapping(
    catalog: AttributeValueCatalog,
) -> tuple[str | None, list[Any]]:
    """Resolve STM's named bargainable cohort from company-owned values."""

    candidates: list[tuple[tuple[int, str], str, list[Any]]] = []
    for attribute_id, values in catalog.values.items():
        if catalog.data_types.get(attribute_id) not in {None, "string", "enum"}:
            continue
        matched = [value for value in values if "bargainable" in str(value).casefold()]
        if matched:
            candidates.append((_attribute_rank(attribute_id), attribute_id, matched))
    if not candidates:
        return None, []
    _, attribute_id, values = min(candidates)
    return attribute_id, values


def _unresolved_list_clauses(text: str, included: list[Any]) -> list[str]:
    """Keep slash/semicolon cohorts that did not map to a company value.

    A partially mapped list is useful, but silently discarding one title would
    make it look complete. Free-form prose is not split here because a value
    may legitimately be embedded in a sentence; this guard targets the clear
    list grammar used by placement slips such as MCIL's plan bands.
    """

    if not any(separator in text for separator in ("/", ";", ",")):
        return []
    unresolved: list[str] = []
    # Strip an explicit occupational classification prefix before checking the
    # named jobs. A comma-separated list must not validate only its first hit.
    text = re.sub(r"^(?:semi[- ]manual|non[- ]manual|manual\s*\d*)\s*-\s*", "", text, flags=re.I)
    separator = r"\s*[/;,]\s*|\s+and\s+" if "," in text else r"\s*[/;]\s*"
    for raw_clause in re.split(separator, text):
        clause = raw_clause.strip(" (),")
        clause_tokens = _tokens(clause)
        if not clause_tokens:
            continue
        if any(_sequence_spans(clause_tokens, _tokens(value)) for value in included):
            continue
        unresolved.append(clause)
    return unresolved


def _rule_for_values(attribute_id: str, values: list[Any], *, negate: bool = False) -> Rule:
    if len(values) == 1 and not negate:
        return {"=": [attribute_id, values[0]]}
    return {"not_in" if negate else "in": [attribute_id, values]}


def _pass_codes(text: str) -> list[str]:
    codes: list[str] = []
    # "SP Grade" is a job band, not an S Pass restriction.
    text = re.sub(r"\b(?:SP|WP|EP)\s+grade\b", "", text, flags=re.I)
    normalized = " ".join(_tokens(text))
    if re.search(r"\bs\s*pass\b|\bspass\b|\bsp\b", normalized):
        codes.append("SP")
    if re.search(r"\bwork\s+permit\b|\bwp\b", normalized):
        codes.append("WP")
    if re.search(r"\bemployment\s+pass\b|\bep\b", normalized):
        codes.append("EP")
    return codes


def _actual_pass_values(codes: list[str], values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for code in codes:
        code_tokens = _tokens(code)
        hit = next(
            (
                value
                for value in values
                if _tokens(value) == code_tokens
                or (code == "SP" and _tokens(value) in (["s", "pass"], ["spass"]))
                or (code == "WP" and _tokens(value) == ["work", "permit"])
                or (code == "EP" and _tokens(value) == ["employment", "pass"])
            ),
            None,
        )
        if hit is not None:
            out.append(hit)
    return out
