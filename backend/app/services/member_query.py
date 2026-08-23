"""Resolve a ``MemberQuery`` against the roster — selection as a RULE.

A bulk coverage change acts on a POPULATION, and a broker describes a population
with rules ("everyone in Sales currently on Plan 1"), not with a list of staff
ids. This module resolves such a rule once and is shared by every caller that
needs the answer — the live headcount, the bulk preview and the bulk apply — so
the three can never disagree about who is in scope.

Resolution order is load-bearing: the filters are ANDed, then the explicit
``employee_ids`` / ``staff_ids`` are ADDED to whatever they matched, and
``exclude_employee_ids`` is SUBTRACTED last.

- Explicit ids ADD to the filter result, so "everyone in Sales, plus this
  contractor" is one query.
- ``exclude_employee_ids`` subtracts LAST and is what keeps apply small: the
  broker previews 412 members, unticks 3, and applies the same rule with 3
  exclusions. Applying the *ticked* ids instead (what the old UI did) means the
  request that was applied is not the one that was previewed, and there is
  nothing left to re-validate against.

Filtering runs in PYTHON over one roster load rather than in SQL. The attribute
filters read ``attribute_values`` / ``derived_attribute_values``, JSON columns
whose querying differs between SQLite (dev/tests) and Postgres (prod); one load
of a few thousand rows is cheap and behaves identically on both.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import (
    Category,
    Employee,
    EmployeeAttributeSchema,
    EmployeePlanOverride,
    PolicyYear,
    Product,
)
from app.models.employee import EMPLOYEE_STATUS_TERMINATED
from app.schemas.member_query import (
    AttributeFacet,
    AttributeFilter,
    CategoryFacet,
    FacetValue,
    MemberFacetsOut,
    MemberFilters,
    MemberQuery,
    PlanFacet,
    ProductFacet,
    ResolvedMemberOut,
    UnresolvedRefOut,
)
from app.services.coverage_resolver import (
    batch_category_defaults,
    load_overrides,
    resolve_plan,
)
from app.services.roster_attributes import (
    DOB_KEYS,
    EMAIL_KEYS,
    EMPLOYEE_ID_KEYS,
    NAME_KEYS,
    anb_from_attrs,
    normalize_nric,
)

# A runaway guard, not a workflow limit — a whole-roster renewal move is a real
# operation and must not be blocked. Callers surface the resolved count so a
# broker over the cap knows how far over they are.
MAX_SELECTION = 5000

# Attributes never offered as a filter facet: identifiers and dates that are
# unique per person (a "facet" with one value each is not a facet), plus the
# obvious PII. `EmployeeAttributeSchema.is_pii` covers configured attributes;
# this covers the parser-written keys that may have no schema row.
_FACET_KEY_DENYLIST = frozenset(
    {*EMPLOYEE_ID_KEYS, *NAME_KEYS, *DOB_KEYS, *EMAIL_KEYS, "staff_id", "salary"}
)
# Values per attribute in the facet response. Beyond this the picker's own search
# is the tool, not a longer list.
_MAX_FACET_VALUES = 200
# An attribute with a distinct value for nearly every member is an IDENTIFIER
# (a reference number the parser wrote with no schema row), not a facet. The
# roster-size floor matters: on a six-person company "three departments" is a
# 0.5 ratio and a perfectly good filter, so a ratio test alone would drop real
# facets for small clients — which is exactly where a filter earns its keep.
_FACET_UNIQUENESS_LIMIT = 0.9
_FACET_UNIQUENESS_MIN_ROSTER = 20


# Dates the roster parser writes: ISO ("2020-01-15", often with a time part
# because Excel stores them as datetimes) and the two slash forms brokers type.
_DATE_VALUE = re.compile(
    r"^(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})([ T].*)?$"
)
# Share of an attribute's distinct values that must parse as dates before the
# whole column is treated as one. Not 1.0: a roster column routinely carries a
# stray "N/A" or "-" beside real dates.
_DATE_COLUMN_SHARE = 0.8

# A search term worth testing against an NRIC: an optional series letter and
# at least five digits, optionally with the checksum letter.
_NRIC_SHAPE = re.compile(r"^[A-Z]?\d{5,7}[A-Z]?$")


def _is_date_column(bucket: dict[str, int]) -> bool:
    """Is this attribute a date rather than a vocabulary?

    A date is a RANGE, not a set of chips: CDL's roster carries date of hire and
    confirmation date, and as pickers they offer hundreds of exact dates, none
    of which is what anyone wants to filter on. Detected by VALUE, not by key
    name, because every roster names these columns differently.
    """
    if not bucket:
        return False
    dated = sum(1 for value in bucket if _DATE_VALUE.match(value))
    return dated / len(bucket) >= _DATE_COLUMN_SHARE


def _norm(value: object) -> str:
    return str(value or "").strip().casefold()


def _merged_attrs(emp: Employee) -> dict[str, Any]:
    """Roster attributes with derived values winning — the same precedence the
    leave-rate and flex-tier vocabularies use."""
    return {**(emp.attribute_values or {}), **(emp.derived_attribute_values or {})}


@dataclass(frozen=True)
class UnresolvedRef:
    kind: Literal["employee_id", "staff_id"]
    value: str
    reason: str

    def out(self) -> UnresolvedRefOut:
        return UnresolvedRefOut(kind=self.kind, value=self.value, reason=self.reason)


@dataclass
class RosterIndex:
    """One load of everything a filter or a bulk evaluation needs.

    Built once per request and passed down, so a preview never issues the same
    roster/override/category queries twice.
    """

    employees: list[Employee]
    _db: Session
    _policy_year_id: str
    _defaults: dict[str, dict[str, tuple[str, str | None]]] | None = None
    _overrides: dict[tuple[str, str], EmployeePlanOverride] | None = None

    @property
    def defaults(self) -> dict[str, dict[str, tuple[str, str | None]]]:
        if self._defaults is None:
            self._defaults = batch_category_defaults(self._db, self.employees)
        return self._defaults

    @property
    def overrides(self) -> dict[tuple[str, str], EmployeePlanOverride]:
        if self._overrides is None:
            self._overrides = load_overrides(
                self._db, self._policy_year_id, [e.id for e in self.employees]
            )
        return self._overrides

    def default_plan(self, emp_id: str, product_id: str) -> str | None:
        return self.defaults.get(emp_id, {}).get(product_id, (None, None))[1]

    def covers(self, emp_id: str, product_id: str) -> bool:
        return product_id in self.defaults.get(emp_id, {})

    def product_codes(self, emp_id: str) -> set[str]:
        return {code for code, _plan in self.defaults.get(emp_id, {}).values()}


@dataclass
class Selection:
    employees: list[Employee]
    unresolved: list[UnresolvedRef] = field(default_factory=list)
    index: RosterIndex | None = None
    # The same population BEFORE ``exclude_employee_ids`` was subtracted.
    #
    # This is what the staleness digest fingerprints, and the distinction is
    # what makes unticking rows usable: an exclusion is the broker narrowing
    # what they already approved, so it must not invalidate their own preview,
    # while a member joining/leaving the rule or someone else moving a member's
    # plan still must. Applying to a subset of an approved population is safe;
    # applying to one that changed underneath is not.
    matched: list[Employee] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.employees]


def load_roster(
    db: Session, policy_year_id: str, *, include_terminated: bool = False
) -> RosterIndex:
    """Every candidate employee in the year, in one query."""
    stmt = select(Employee).where(Employee.policy_year_id == policy_year_id)
    if not include_terminated:
        stmt = stmt.where(Employee.status != EMPLOYEE_STATUS_TERMINATED)
    rows = list(db.execute(stmt.order_by(Employee.staff_id)).scalars())
    return RosterIndex(employees=rows, _db=db, _policy_year_id=policy_year_id)


def resolve_selection(
    db: Session,
    py: PolicyYear,
    query: MemberQuery,
    *,
    product_id: str | None = None,
    index: RosterIndex | None = None,
) -> Selection:
    """Resolve ``query`` to an ordered, de-duplicated list of employees.

    ``product_id`` scopes the coverage filters (``current_plan_codes``,
    ``coverage_state``); without it those filters are inert, because "the
    effective plan" has no meaning until a product is named.
    """
    idx = index or load_roster(
        db, py.id, include_terminated=query.include_terminated
    )
    by_id = {e.id: e for e in idx.employees}

    picked: dict[str, Employee] = {}
    if query.has_filters():
        for emp in _filtered(idx, query, product_id, py.start_date):
            picked[emp.id] = emp

    unresolved: list[UnresolvedRef] = []
    for emp_id in dict.fromkeys(query.employee_ids):
        found = by_id.get(emp_id)
        if found is None:
            unresolved.append(
                UnresolvedRef("employee_id", emp_id, _missing_reason(db, py, emp_id))
            )
        else:
            picked[found.id] = found

    if query.staff_ids:
        by_staff = {_norm(e.staff_id): e for e in idx.employees}
        for staff in dict.fromkeys(query.staff_ids):
            hit = by_staff.get(_norm(staff))
            if hit is None:
                unresolved.append(
                    UnresolvedRef(
                        "staff_id", staff, _missing_staff_reason(db, py, staff)
                    )
                )
            else:
                picked[hit.id] = hit

    def _ordered(rows: list[Employee]) -> list[Employee]:
        return sorted(rows, key=lambda e: (e.staff_id or "", e.id))

    matched = _ordered(list(picked.values()))
    for emp_id in query.exclude_employee_ids:
        picked.pop(emp_id, None)

    return Selection(
        employees=_ordered(list(picked.values())),
        unresolved=unresolved,
        index=idx,
        matched=matched,
    )


def resolve_listing(
    db: Session,
    py: PolicyYear,
    filters: MemberFilters,
    *,
    product_id: str | None = None,
    index: RosterIndex | None = None,
) -> tuple[list[Employee], RosterIndex]:
    """Every member matching ``filters``, in roster order — the LISTING path.

    Shares ``_filtered`` with :func:`resolve_selection`, so the Member Listing
    page and the bulk coverage tool can never disagree about who is in a cohort.
    It differs in exactly two ways, both deliberate:

    - **An empty query is legal and means everyone.** For a bulk *apply* that is
      a footgun — it would silently target the whole roster — which is why
      ``MemberQuery`` refuses one. For a *list* it is the default view.
    - **The explicit id add/subtract fields are ignored.** A listing has no
      preview to narrow, and honouring ``exclude_employee_ids`` here would let a
      URL hide members from a roster view without saying so.
    """
    idx = index or load_roster(
        db, py.id, include_terminated=filters.include_terminated
    )
    rows = (
        _filtered(idx, filters, product_id, py.start_date)
        if filters.has_filters()
        else list(idx.employees)
    )
    return sorted(rows, key=lambda e: (e.staff_id or "", e.id)), idx


def _missing_reason(db: Session, py: PolicyYear, employee_id: str) -> str:
    """Distinguish "not in this benefit year" from "excluded as a leaver" — the
    second is a one-checkbox fix and the first is not."""
    emp = db.get(Employee, employee_id)
    if emp is not None and emp.policy_year_id == py.id and emp.client_id == py.client_id:
        return "Terminated — switch on 'Include leavers' to select them."
    return "Employee not found in this benefit year."


def _missing_staff_reason(db: Session, py: PolicyYear, staff_id: str) -> str:
    exists = db.execute(
        select(Employee.id).where(
            Employee.policy_year_id == py.id,
            Employee.staff_id == staff_id,
            Employee.status == EMPLOYEE_STATUS_TERMINATED,
        )
    ).first()
    if exists:
        return "Terminated — switch on 'Include leavers' to select them."
    return "No employee with this staff ID in the benefit year."


def _filtered(
    idx: RosterIndex,
    query: MemberFilters,
    product_id: str | None,
    year_start: date,
) -> list[Employee]:
    """The one predicate chain. Shared by the bulk selection and the listing —
    never copy it, or the two surfaces start disagreeing silently."""
    needle = _norm(query.q) if query.q else None
    # A broker searching "S1234567A" types the punctuation the roster may not
    # store, so the NRIC leg compares normalized forms. The Dependants tab has
    # always searched NRIC; the Employees tab did not, which brokers hit at once.
    nric_needle = (normalize_nric(query.q) or "") if query.q else ""
    categories = set(query.category_ids)
    products = {c.strip().casefold() for c in query.product_codes if c.strip()}
    plans = {p.strip().casefold() for p in query.current_plan_codes if p.strip()}
    out: list[Employee] = []

    for emp in idx.employees:
        if needle and not _matches_needle(emp, needle, nric_needle):
            continue
        if query.match_status == "matched" and emp.matched_category_id is None:
            continue
        if query.match_status == "unmatched" and emp.matched_category_id is not None:
            continue
        if categories and not _in_categories(emp, categories):
            continue
        if products and not products <= {c.casefold() for c in idx.product_codes(emp.id)}:
            continue
        if (plans or query.coverage_state != "any") and product_id:
            if not _coverage_matches(idx, emp, product_id, plans, query.coverage_state):
                continue
        if query.attributes and not _attributes_match(emp, query.attributes):
            continue
        if query.age is not None:
            anb = anb_from_attrs(_merged_attrs(emp), year_start)
            if anb is None:
                continue  # no date of birth on file — an age filter can't include them
            if query.age.min is not None and anb < query.age.min:
                continue
            if query.age.max is not None and anb > query.age.max:
                continue
        out.append(emp)
    return out


def looks_like_nric(value: str) -> bool:
    """Enough of an NRIC/FIN to be worth matching identifiers on.

    Deliberately loose — brokers paste partial numbers — but it must not fire on
    a short numeric fragment that is really a staff id.
    """
    return bool(_NRIC_SHAPE.match(value))


def _matches_needle(emp: Employee, needle: str, nric_needle: str) -> bool:
    if needle in _norm(emp.staff_id) or needle in _norm(emp.employee_name):
        return True
    # Only when the search text actually LOOKS like an NRIC. `normalize_nric`
    # merely strips punctuation, so it returns non-empty for anything — without
    # this shape test, a partial staff-id search like "123" would also pull in
    # every unrelated member whose NRIC happens to contain "123".
    return looks_like_nric(nric_needle) and nric_needle in (
        emp.national_id_normalized or ""
    )


def _in_categories(emp: Employee, category_ids: set[str]) -> bool:
    return any(
        m.get("category_id") in category_ids for m in (emp.matched_categories or [])
    )


def _coverage_matches(
    idx: RosterIndex,
    emp: Employee,
    product_id: str,
    plans: set[str],
    coverage_state: str,
) -> bool:
    """Effective-coverage predicates, resolved through the canonical resolver so
    the filter matches exactly what the benefit statement and exports show."""
    if not idx.covers(emp.id, product_id):
        return False
    ov = idx.overrides.get((emp.id, product_id))
    resolved = resolve_plan(ov, idx.default_plan(emp.id, product_id))
    if coverage_state == "default" and resolved.overridden:
        return False
    if coverage_state == "overridden" and not resolved.overridden:
        return False
    if coverage_state == "declined" and not resolved.declined:
        return False
    if plans:
        if resolved.declined or not resolved.plan_code:
            return False
        if resolved.plan_code.casefold() not in plans:
            return False
    return True


def _attributes_match(emp: Employee, filters: list[AttributeFilter]) -> bool:
    attrs = _merged_attrs(emp)
    for f in filters:
        wanted = {_norm(v) for v in f.values if v.strip()}
        actual = _norm(attrs.get(f.key))
        if f.op == "not_in":
            # A member with no value for the attribute is, correctly, not in the
            # excluded set.
            if actual and actual in wanted:
                return False
        elif actual not in wanted:
            return False
    return True


# ── Digest (the preview → apply staleness guard) ────────────────────────────


def selection_digest(
    products: list[tuple[str, str]],
    index: RosterIndex,
    employees: list[Employee],
) -> str:
    """Fingerprint of WHO is selected and WHAT their coverage is right now.

    Apply recomputes it and refuses on a mismatch. The state has to be in the
    hash, not just the id set: the population can be identical while somebody
    else has already moved two of them, and the broker approved neither that
    change nor its consequence.

    ``products`` is every ``(code, id)`` the batch touches — a change set has to
    prove the state of ALL of them, or a second product's coverage could move
    between preview and apply unnoticed. Sorted, so reordering the changes (which
    cannot change the outcome) doesn't force a re-preview.
    """
    h = hashlib.sha256()
    for product_code, product_id in sorted(products):
        h.update(b"\x1e")
        h.update(product_code.encode("utf-8"))
        for emp in sorted(employees, key=lambda e: e.id):
            ov = index.overrides.get((emp.id, product_id))
            resolved = resolve_plan(ov, index.default_plan(emp.id, product_id))
            state = "declined" if resolved.declined else (resolved.plan_code or "")
            h.update(b"\x1f")
            h.update(f"{emp.id}={state}".encode())
    return h.hexdigest()[:32]


# ── Facets (the picker's vocabulary) ────────────────────────────────────────


def build_facets(db: Session, py: PolicyYear) -> MemberFacetsOut:
    """Distinct roster values, cohorts and products — with live headcounts.

    Headcounts are what make a filter usable: "Sales (48)" tells the broker the
    selection is right before they run anything.
    """
    idx = load_roster(db, py.id)
    active = idx.employees
    terminated_total = (
        db.scalar(
            select(func.count(Employee.id)).where(
                Employee.policy_year_id == py.id,
                Employee.status == EMPLOYEE_STATUS_TERMINATED,
            )
        )
        or 0
    )

    return MemberFacetsOut(
        employees_total=len(active),
        terminated_total=terminated_total,
        attributes=_attribute_facets(db, py, active),
        categories=_category_facets(db, py, active),
        products=_product_facets(db, py, idx),
    )


def _attribute_facets(
    db: Session, py: PolicyYear, employees: list[Employee]
) -> list[AttributeFacet]:
    schemas = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, py.client_id)
            )
        ).scalars()
    )
    # A company row shadows the global default of the same attribute_id.
    labels: dict[str, str] = {}
    pii: set[str] = set()
    for row in sorted(schemas, key=lambda r: r.client_id is not None):
        labels[row.attribute_id] = row.display_name
        if row.is_pii:
            pii.add(row.attribute_id)
        else:
            pii.discard(row.attribute_id)

    counts: dict[str, dict[str, int]] = {}
    for emp in employees:
        for key, raw in _merged_attrs(emp).items():
            if key in _FACET_KEY_DENYLIST or key in pii:
                continue
            value = str(raw).strip() if raw is not None else ""
            if not value or isinstance(raw, (dict, list)):
                continue
            bucket = counts.setdefault(key, {})
            bucket[value] = bucket.get(value, 0) + 1

    total = max(len(employees), 1)
    facets: list[AttributeFacet] = []
    for key, bucket in counts.items():
        if _is_date_column(bucket):
            # A date is a RANGE, not a vocabulary. CDL's roster carries date of
            # hire and confirmation date; as chip lists they offer hundreds of
            # exact dates, none of which is what anyone wants to filter on.
            continue
        if (
            total >= _FACET_UNIQUENESS_MIN_ROSTER
            and len(bucket) / total > _FACET_UNIQUENESS_LIMIT
        ):
            continue  # ~one value per person: an identifier, not a facet
        ordered = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ordered[:_MAX_FACET_VALUES]
        facets.append(
            AttributeFacet(
                key=key,
                label=labels.get(key) or key.replace("_", " ").title(),
                values=[FacetValue(value=v, count=c) for v, c in shown],
                truncated=len(ordered) > len(shown),
            )
        )
    return sorted(facets, key=lambda f: f.label.casefold())


def _category_facets(
    db: Session, py: PolicyYear, employees: list[Employee]
) -> list[CategoryFacet]:
    counts: dict[str, int] = {}
    for emp in employees:
        for m in emp.matched_categories or []:
            cid = m.get("category_id")
            if cid:
                counts[cid] = counts.get(cid, 0) + 1
    rows = db.execute(
        select(Category.id, Category.display_name, Product.code)
        .outerjoin(Product, Category.product_id == Product.id)
        .where(Category.policy_year_id == py.id)
    ).all()
    facets = [
        CategoryFacet(
            id=cid, label=name, product_code=code, count=counts.get(cid, 0)
        )
        for cid, name, code in rows
    ]
    # Cohorts nobody is in still appear — a zero there is a matching gap the
    # broker needs to see, not a row to hide.
    return sorted(facets, key=lambda c: (-c.count, c.label.casefold()))


def _product_facets(db: Session, py: PolicyYear, idx: RosterIndex) -> list[ProductFacet]:
    product_ids = {
        pid for emp in idx.employees for pid in idx.defaults.get(emp.id, {})
    }
    if not product_ids:
        return []
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(Product.id.in_(product_ids))
        ).scalars()
    }
    covered: dict[str, int] = {}
    declined: dict[str, int] = {}
    plan_counts: dict[str, dict[str, int]] = {}
    for emp in idx.employees:
        for pid in idx.defaults.get(emp.id, {}):
            covered[pid] = covered.get(pid, 0) + 1
            resolved = resolve_plan(
                idx.overrides.get((emp.id, pid)), idx.default_plan(emp.id, pid)
            )
            if resolved.declined:
                declined[pid] = declined.get(pid, 0) + 1
            elif resolved.plan_code:
                bucket = plan_counts.setdefault(pid, {})
                bucket[resolved.plan_code] = bucket.get(resolved.plan_code, 0) + 1

    out: list[ProductFacet] = []
    for pid, product in products.items():
        plans = sorted(
            plan_counts.get(pid, {}).items(), key=lambda kv: (-kv[1], kv[0])
        )
        out.append(
            ProductFacet(
                id=pid,
                code=product.code,
                name=product.display_name,
                covered=covered.get(pid, 0),
                declined=declined.get(pid, 0),
                plans=[PlanFacet(code=c, count=n) for c, n in plans],
            )
        )
    return sorted(out, key=lambda p: p.code)


# ── Pasted-list resolution ──────────────────────────────────────────────────

_LIST_SEPARATORS = str.maketrans({",": "\n", ";": "\n", "\t": "\n", "\r": "\n"})


def split_member_tokens(text: str) -> list[str]:
    """Split pasted text into candidate identifiers.

    Handles the three shapes a broker actually pastes: a column copied out of
    Excel (newlines), a comma/semicolon list from an email, and whitespace-
    separated ids.
    """
    tokens: list[str] = []
    for line in text.translate(_LIST_SEPARATORS).split("\n"):
        for token in line.split():
            cleaned = token.strip().strip("\"'")
            if cleaned:
                tokens.append(cleaned)
    return tokens


def resolve_member_list(
    db: Session, py: PolicyYear, text: str, *, include_terminated: bool = False
) -> tuple[list[ResolvedMemberOut], list[str], int]:
    """Match pasted tokens against staff IDs, then NRIC/FIN.

    Returns ``(matched, unmatched, duplicates)``. NRIC is matched as well as
    staff id because half the lists a broker is handed are keyed on it, and the
    normalization (``normalize_nric``) is the same identity key roster dedup
    uses — never a raw string compare.
    """
    idx = load_roster(db, py.id, include_terminated=include_terminated)
    by_staff = {_norm(e.staff_id): e for e in idx.employees}
    by_nric: dict[str, Employee] = {}
    for row in idx.employees:
        nric = normalize_nric(
            next(
                (
                    (row.attribute_values or {}).get(k)
                    for k in EMPLOYEE_ID_KEYS
                    if (row.attribute_values or {}).get(k)
                ),
                None,
            )
        )
        if nric:
            by_nric.setdefault(nric, row)

    matched: list[ResolvedMemberOut] = []
    seen: set[str] = set()
    unmatched: list[str] = []
    duplicates = 0
    for token in split_member_tokens(text):
        emp: Employee | None = by_staff.get(_norm(token))
        how: Literal["staff_id", "nric"] = "staff_id"
        if emp is None:
            nric = normalize_nric(token)
            emp = by_nric.get(nric) if nric else None
            how = "nric"
        if emp is None:
            if _norm(token) in {_norm(u) for u in unmatched}:
                duplicates += 1
            else:
                unmatched.append(token)
            continue
        if emp.id in seen:
            duplicates += 1
            continue
        seen.add(emp.id)
        matched.append(
            ResolvedMemberOut(
                id=emp.id,
                staff_id=emp.staff_id,
                employee_name=emp.employee_name,
                matched_on=how,
            )
        )
    return matched, unmatched, duplicates


__all__ = [
    "MAX_SELECTION",
    "RosterIndex",
    "Selection",
    "UnresolvedRef",
    "build_facets",
    "load_roster",
    "resolve_member_list",
    "resolve_selection",
    "selection_digest",
    "split_member_tokens",
]
