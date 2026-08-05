"""Resolve a ``DependantFilters`` against the year's dependants.

Filtering runs in PYTHON over one load, for the same reason
``services/member_query`` does: a dependant's name, relationship and date of
birth all live in the ``attribute_values`` JSON blob (there are no columns for
them), and JSON querying differs between SQLite and Postgres. One load of a few
thousand rows is cheap and behaves identically on both.

The employee half of a dependant query is delegated to ``member_query`` rather
than reimplemented, so "dependants of everyone in Sales on Plan 3" resolves the
employee side through exactly the predicate chain the Employees tab and the bulk
tool use.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dependant, Employee, PolicyYear
from app.models.dependant import DEPENDANT_STATUS_ACTIVE
from app.schemas.dependant_query import DependantFacetsOut, DependantFilters
from app.schemas.member_query import FacetValue
from app.services import member_query as mq
from app.services.flex_membership import classify_relationship
from app.services.roster_attributes import (
    REL_KEYS,
    anb_from_attrs,
    first_value,
    normalize_nric,
)
from app.services.roster_dedup import DEP_NAME_KEYS, dependant_nric

# Values per facet. Beyond this the picker's own search is the tool.
_MAX_FACET_VALUES = 200


def _norm(value: object) -> str:
    return str(value or "").strip().casefold()


def dependant_name(attrs: dict) -> str:
    """The dependant's OWN name.

    ``roster_attributes.NAME_KEYS`` must not be used here: it includes
    ``employee_name``, and a dependant row genuinely carries that column, so a
    row with no ``dependant_name`` would report the PARENT's name as the
    dependant's.
    """
    return str(first_value(attrs, DEP_NAME_KEYS) or "").strip()


def role_of(attrs: dict) -> str:
    """spouse / child / other — ``classify_relationship``'s None, named."""
    return classify_relationship(first_value(attrs, REL_KEYS)) or "other"


@dataclass
class DependantIndex:
    """One load of the year's dependants plus the employees they hang off."""

    dependants: list[Dependant]
    employees_by_id: dict[str, Employee]

    def staff_id(self, dep: Dependant) -> str:
        emp = self.employees_by_id.get(dep.employee_id or "")
        if emp is not None:
            return emp.staff_id or ""
        # Unlinked (or a sponsor outside the load) — the roster hint the parser
        # wrote is all there is.
        return str((dep.attribute_values or {}).get("employee_staff_id") or "")


def load_dependants(db: Session, policy_year_id: str) -> DependantIndex:
    deps = list(
        db.execute(
            select(Dependant).where(Dependant.policy_year_id == policy_year_id)
        ).scalars()
    )
    # Terminated sponsors included: their dependants still exist and must sort
    # and display with a staff id rather than falling to a blank.
    emps = list(
        db.execute(
            select(Employee).where(Employee.policy_year_id == policy_year_id)
        ).scalars()
    )
    return DependantIndex(dependants=deps, employees_by_id={e.id: e for e in emps})


def resolve_listing(
    db: Session,
    py: PolicyYear,
    filters: DependantFilters,
    *,
    index: DependantIndex | None = None,
) -> tuple[list[Dependant], DependantIndex]:
    """Every dependant matching ``filters``, grouped by sponsoring employee."""
    idx = index or load_dependants(db, py.id)

    employee_ids: set[str] | None = None
    if filters.employee is not None and filters.employee.has_filters():
        rows, _ = mq.resolve_listing(db, py, filters.employee)
        employee_ids = {e.id for e in rows}

    out = [
        dep
        for dep in idx.dependants
        if _matches(dep, filters, employee_ids, py.start_date)
    ]
    # A family reads as a family: sponsor first, then the dependant's own name.
    # An unlinked row has no sponsor, so it sorts LAST rather than leading every
    # page with the exceptions — "Unlinked" is a filter, and that is how they
    # are meant to be found.
    def _key(d: Dependant) -> tuple[int, str, str, str]:
        staff = idx.staff_id(d)
        return (0 if staff else 1, staff, dependant_name(d.attribute_values or {}), d.id)

    return sorted(out, key=_key), idx


def _matches(
    dep: Dependant,
    f: DependantFilters,
    employee_ids: set[str] | None,
    year_start: date,
) -> bool:
    attrs = dep.attribute_values or {}

    # Status: empty means the default view (active only). Pending self-adds are
    # deliberately out of the default — they are not coverage yet.
    if f.statuses:
        if dep.status not in f.statuses:
            return False
    elif dep.status != DEPENDANT_STATUS_ACTIVE:
        return False

    if f.link_state == "linked" and dep.employee_id is None:
        return False
    if f.link_state == "unlinked" and dep.employee_id is not None:
        return False
    if f.link_methods and _norm(dep.link_method) not in {
        _norm(m) for m in f.link_methods
    }:
        return False

    if f.relationships:
        wanted = {_norm(r) for r in f.relationships if r.strip()}
        if _norm(first_value(attrs, REL_KEYS)) not in wanted:
            return False
    if f.roles and role_of(attrs) not in f.roles:
        return False

    if f.age is not None:
        anb = anb_from_attrs(attrs, year_start)
        if anb is None:
            return False  # no date of birth on file — an age filter can't include them
        if f.age.min is not None and anb < f.age.min:
            return False
        if f.age.max is not None and anb > f.age.max:
            return False

    if employee_ids is not None and (dep.employee_id or "") not in employee_ids:
        # An employee filter necessarily excludes unlinked dependants: there is
        # no employee to test them against.
        return False

    if f.q:
        if not _matches_needle(dep, attrs, _norm(f.q), normalize_nric(f.q)):
            return False
    return True


def _matches_needle(
    dep: Dependant, attrs: dict, needle: str, nric_needle: str
) -> bool:
    """Same fields the existing dependants search covers: the dependant's name
    and NRIC, plus the sponsoring employee's staff id and name."""
    hay = (
        dependant_name(attrs),
        str(attrs.get("employee_staff_id") or ""),
        str(attrs.get("employee_name") or ""),
    )
    if any(needle in _norm(v) for v in hay):
        return True
    if not nric_needle:
        return False
    stored = dep.national_id_normalized or dependant_nric(attrs) or ""
    return nric_needle in stored


# ── Facets ──────────────────────────────────────────────────────────────────


def _tally(pairs: list[str]) -> list[FacetValue]:
    bucket: dict[str, int] = {}
    for value in pairs:
        if value:
            bucket[value] = bucket.get(value, 0) + 1
    ordered = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))
    return [FacetValue(value=v, count=c) for v, c in ordered[:_MAX_FACET_VALUES]]


def build_facets(db: Session, py: PolicyYear) -> DependantFacetsOut:
    idx = load_dependants(db, py.id)
    active = [d for d in idx.dependants if d.status == DEPENDANT_STATUS_ACTIVE]
    return DependantFacetsOut(
        active_total=len(active),
        all_statuses_total=len(idx.dependants),
        linked=sum(1 for d in active if d.employee_id is not None),
        unlinked=sum(1 for d in active if d.employee_id is None),
        # Spans every status — this is the control that WIDENS the population,
        # so counting it over the active-only default would hide the pending
        # self-adds a broker opens this filter to find.
        statuses=_tally([d.status for d in idx.dependants]),
        relationships=_tally(
            [str(first_value(d.attribute_values or {}, REL_KEYS) or "") for d in active]
        ),
        roles=_tally([role_of(d.attribute_values or {}) for d in active]),
        link_methods=_tally([d.link_method or "" for d in active]),
    )


__all__ = [
    "DependantIndex",
    "build_facets",
    "dependant_name",
    "load_dependants",
    "resolve_listing",
    "role_of",
]
