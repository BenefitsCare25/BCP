"""Roster profiling — sample distinct raw attribute values per column.

Feeds the AI derivation-rule proposer: instead of sending thousands of rows,
we send each column's distinct values (capped) plus frequency, which is enough
for the model to infer an extraction/case rule. Pure sampling, no AI here.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    EMAIL_KEYS,
    EMPLOYEE_ID_KEYS,
)

# Cap distinct values surfaced per column. Enough variety for the model to
# generalise a rule without blowing the prompt (and the AI spend) up.
MAX_DISTINCT_PER_COLUMN = 40
# Columns with more distinct values than this are almost certainly free-text /
# identifiers (names, emails, IDs), not derivable enums — skip them.
HIGH_CARDINALITY_SKIP = 200
# Keys that are never derivation sources (identity / contact / raw PII). Covers
# every NRIC/FIN alias so a roster that lands its ID under a non-``id_no`` column
# can't surface raw NRICs into the AI-proposer prompt.
_NON_SOURCE_KEYS: frozenset[str] = frozenset(
    {"staff_id", "employee_name", "mobile"}
    | set(EMPLOYEE_ID_KEYS)
    | set(DEPENDANT_ID_KEYS)
    | set(EMAIL_KEYS)
)


@dataclass(frozen=True)
class ColumnProfile:
    key: str
    total: int  # non-empty value count
    distinct_count: int
    samples: tuple[str, ...]  # up to MAX_DISTINCT_PER_COLUMN, most-frequent first
    high_cardinality: bool  # likely free-text / identifier, not an enum source


@dataclass(frozen=True)
class RosterProfile:
    employee_count: int
    columns: tuple[ColumnProfile, ...]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def profile_roster(
    rows: Iterable[Mapping[str, Any]],
    *,
    max_distinct: int = MAX_DISTINCT_PER_COLUMN,
) -> RosterProfile:
    """Build a per-column distinct-value profile from raw employee attributes.

    `rows` is each employee's raw ``attribute_values`` mapping. Values are
    stringified and trimmed; empties are ignored. Columns are returned sorted
    by descending fill count so the most-populated (most-derivable) appear
    first.
    """
    counters: dict[str, Counter[str]] = {}
    employee_count = 0
    for row in rows:
        employee_count += 1
        if not row:
            continue
        for key, raw in row.items():
            if key in _NON_SOURCE_KEYS:
                continue
            cleaned = _clean(raw)
            if cleaned is None:
                continue
            counters.setdefault(key, Counter())[cleaned] += 1

    columns: list[ColumnProfile] = []
    for key, counter in counters.items():
        total = sum(counter.values())
        distinct_count = len(counter)
        high_card = distinct_count > HIGH_CARDINALITY_SKIP
        # High-cardinality columns are free-text / identifiers, not enum sources.
        # Don't surface their raw values — the model skips them anyway, and they
        # may hold PII the alias filter above didn't anticipate.
        samples = (
            ()
            if high_card
            else tuple(v for v, _ in counter.most_common(max_distinct))
        )
        columns.append(
            ColumnProfile(
                key=key,
                total=total,
                distinct_count=distinct_count,
                samples=samples,
                high_cardinality=high_card,
            )
        )

    columns.sort(key=lambda c: c.total, reverse=True)
    return RosterProfile(employee_count=employee_count, columns=tuple(columns))


__all__ = [
    "MAX_DISTINCT_PER_COLUMN",
    "ColumnProfile",
    "RosterProfile",
    "profile_roster",
]
