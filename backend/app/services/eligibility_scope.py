"""Location-scoped cohorts and short slip labels, for rule mapping and matching.

Two slip shapes the generic mapper got wrong on CDL's GPA sheet:

* ``All Employees`` with participation ``Compulsory - Thai Office``. The label
  alone reads as everyone, so it compiled to the empty catch-all and swallowed
  239 Singapore staff whose own rows were unmapped. The location lives in
  ``plan_assignments.location_scope``, which nothing read.
* ``Executive to AM`` where the roster says ``Executive to AM & Secretary``:
  the slip label is a strict prefix of exactly one roster value.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

CATCH_ALL: dict[str, list[Any]] = {"and": []}

_WORD_RE = re.compile(r"[a-z0-9]+")
_SCOPE_NOISE = {"office", "offices", "branch", "staff", "employee", "employees", "based", "in"}
_BASED_IN_RE = re.compile(r"\bbased\s+in\s+([a-z ]+?)(?=\s*(?:\(|except|excluding|$))", re.I)
_LOCATION_ATTRS = (
    "country_of_work",
    "work_country",
    "work_location",
    "location",
    "office_location",
    "country",
)
_COHORT_ATTRS = ("category", "employee_category")


def _country(text: Any) -> str | None:
    # Imported late: flex_membership reaches matching_engine, which imports us.
    from app.services.flex_membership import nationality_country_exact

    return nationality_country_exact(text)


def _words(text: Any) -> list[str]:
    return _WORD_RE.findall(str(text or "").lower())


def location_scope(category: Any) -> str | None:
    pa = getattr(category, "plan_assignments", None)
    scope = pa.get("location_scope") if isinstance(pa, dict) else None
    text = str(scope or "").strip()
    return text or None


def is_catch_all(rule: Any) -> bool:
    return bool(rule == CATCH_ALL)


def multi_location_scoped(categories: Iterable[Any]) -> set[str]:
    """Categories limited to ONE of several separately priced locations.

    Only a product that prices two or more locations separately counts: a
    single-location slip states its office for information, and its catch-all
    genuinely is everyone.
    """
    scopes: dict[Any, set[str]] = defaultdict(set)
    rows = list(categories)
    for category in rows:
        if scope := location_scope(category):
            scopes[category.product_id].add(scope.casefold())
    return {
        category.id
        for category in rows
        if location_scope(category) and len(scopes[category.product_id]) >= 2
    }


def unscoped_catch_alls(categories: Iterable[Any]) -> set[str]:
    """Location-limited categories whose COMPILED rule still matches everyone.

    A broker who deliberately saved the catch-all (``human_modified``) made a
    decision; only the compiler's unscoped default is held back.
    """
    rows = list(categories)
    scoped = multi_location_scoped(rows)
    return {
        c.id
        for c in rows
        if c.id in scoped
        and is_catch_all(c.matching_rule)
        and not getattr(c, "human_modified", False)
    }


def scope_country(scope: str) -> str | None:
    """``Thai Office`` → ``thailand``; ``SG Office`` → ``singapore``."""
    words = [w for w in _words(scope) if w not in _SCOPE_NOISE]
    if not words:
        return None
    return _country(" ".join(words)) or next(
        (country for w in words if (country := _country(w))), None
    )


def _unique(matches: list[tuple[str, Any]]) -> tuple[str | None, Any | None]:
    return matches[0] if len(matches) == 1 else (None, None)


def location_cohort_value(
    scope: str, values: Mapping[str, list[Any]]
) -> tuple[str | None, Any | None]:
    """The one roster value that identifies staff at ``scope``.

    A work-location field wins; otherwise a cohort label written the way CDL's
    roster does it ("All Employees based in Thailand (except for Director)").
    """
    country = scope_country(scope)
    if country is None:
        return None, None
    for attr in _LOCATION_ATTRS:
        hit = _unique(
            [(attr, v) for v in values.get(attr, []) if _country(v) == country]
        )
        if hit[0]:
            return hit
    labelled: list[tuple[str, Any]] = []
    for attr in _COHORT_ATTRS:
        for value in values.get(attr, []):
            match = _BASED_IN_RE.search(str(value))
            if match and _country(match.group(1).strip()) == country:
                labelled.append((attr, value))
    return _unique(labelled)


def prefix_cohort_value(
    text: str, values: Mapping[str, list[Any]]
) -> tuple[str | None, Any | None]:
    """The one roster cohort whose name starts with the whole slip label.

    Two words minimum, so a bare "Manager" never claims "Manager, Executive…".
    """
    needle = _words(text)
    if len(needle) < 2:
        return None, None
    hits = [
        (attr, value)
        for attr in _COHORT_ATTRS
        for value in values.get(attr, [])
        if len(words := _words(value)) > len(needle) and words[: len(needle)] == needle
    ]
    return _unique(hits)
