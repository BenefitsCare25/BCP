"""Live "members matched" preview for the Basis-of-Cover setup form.

The setup form builds category rows *before* a product is confirmed, so no
persisted ``Category`` rows (and no stored match results) exist yet. This
service evaluates the draft category descriptions against the current roster
on the fly — using the exact same matching semantics as
``matching_engine.match_one`` (exact-name → fuzzy → rule, most-specific wins
within the product) — and returns matched employee + dependant counts per
draft category.

It is strictly read-only: employees' derived attributes are computed into
throwaway views, never written back, so calling it can't mutate the roster.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Dependant, Employee, EmployeeAttributeSchema
from app.models.category import Category, CategoryStatus
from app.services.derivation_engine import derive
from app.services.matching_engine import (
    _build_exact_lookup,
    _normalize,
    _status_rank,
    canonicalize_category_name,
    entity_alias_map,
    insured_names,
    match_one,
    tokenize,
)
from app.services.rule_generator import description_to_rule


@dataclass(frozen=True)
class DraftCategory:
    """One Basis-of-Cover row: a client-side key + its category description.

    ``insured`` (the row's legal-entity list, when the slip states one) feeds
    the same insured-entity gate real matching applies, so multi-subsidiary
    previews count each entity's employees separately."""

    key: str
    description: str
    # List of entity tokens; a legacy comma-joined string is still accepted.
    insured: str | list[str] | None = None


@dataclass(frozen=True)
class CategoryCount:
    key: str
    employees: int
    dependants: int


@dataclass(frozen=True)
class MemberCounts:
    counts: list[CategoryCount]
    employees_total: int
    employees_matched: int
    has_dependants: bool


@dataclass
class _EmpView:
    """Minimal stand-in for ``Employee`` — only the two fields ``match_one``
    reads — so we never touch (or flush) the ORM rows during a preview."""

    attribute_values: dict
    derived_attribute_values: dict


def _transient_categories(
    drafts: list[DraftCategory],
    persisted: dict[str, Category],
) -> list[Category]:
    """Build unpersisted ``Category`` objects from draft descriptions.

    ``id`` is set to the client-side row key so a match outcome maps straight
    back to the originating row. They're never added to the session.

    When a persisted category with the same (normalised) display name already
    exists for this product, its **stored** ``matching_rule``/status/confidence
    are reused — so the preview mirrors what real matching will do. Re-deriving
    from the text alone can't reproduce rules an edit or AI reconcile produced
    (e.g. a category titled "All job Grades…" whose rule was set to the
    catch-all "all employees"). Only genuinely new rows fall back to
    ``description_to_rule``.
    """
    cats: list[Category] = []
    for index, draft in enumerate(drafts):
        desc = (draft.description or "").strip()
        match = persisted.get(_normalize(desc)) if desc else None
        if match is not None:
            rule = match.matching_rule
            confidence = match.confidence
            status = match.status
        else:
            envelope = description_to_rule(draft.description or "")
            rule = envelope.rule
            confidence = envelope.confidence
            status = CategoryStatus.confirmed.value
        # The draft row's insured entities (falling back to the persisted
        # category's) ride plan_assignments so match_one applies the same
        # insured-entity gate here as in a real matching run.
        # Tokens, not a joined string — see `insured_names`. Falls back to the
        # persisted category's entities when the draft row hasn't set any.
        insured = insured_names(draft.insured)
        if not insured and match is not None and isinstance(match.plan_assignments, dict):
            insured = insured_names(match.plan_assignments.get("insured"))
        cats.append(
            Category(
                id=draft.key,
                display_name=desc,
                raw_description=draft.description or "",
                matching_rule=rule,
                priority=index,
                status=status,
                confidence=confidence,
                plan_assignments={"insured": insured} if insured else None,
            )
        )
    return cats


def _persisted_by_name(
    db: Session, policy_year_id: str, product_id: str | None
) -> dict[str, Category]:
    """Map normalised display name → the persisted Category for this product.

    On a name collision, prefer the better (lower) status rank, then lower
    priority — matching ``_build_exact_lookup``'s tie-break.
    """
    if not product_id:
        return {}
    out: dict[str, Category] = {}
    for c in db.execute(
        select(Category).where(
            Category.policy_year_id == policy_year_id,
            Category.product_id == product_id,
        )
    ).scalars():
        if not c.display_name:
            continue
        key = _normalize(c.display_name)
        existing = out.get(key)
        if existing is None or (_status_rank(c.status), c.priority) < (
            _status_rank(existing.status),
            existing.priority,
        ):
            out[key] = c
    return out


def compute_member_counts(
    db: Session,
    policy_year_id: str,
    client_id: str | None,
    has_dependants: bool,
    drafts: list[DraftCategory],
    product_id: str | None = None,
) -> MemberCounts:
    """Count employees (and, when the product covers dependants, their
    dependants) that match each draft category in this policy year.

    ``product_id`` (when known) lets the preview reuse the product's persisted
    category rules instead of re-deriving them from text — see
    ``_transient_categories``."""
    valid = [d for d in drafts if (d.description or "").strip()]
    if not valid:
        total = (
            db.execute(
                select(func.count(Employee.id)).where(
                    Employee.policy_year_id == policy_year_id
                )
            ).scalar()
            or 0
        )
        return MemberCounts(
            counts=[CategoryCount(d.key, 0, 0) for d in drafts],
            employees_total=total,
            employees_matched=0,
            has_dependants=has_dependants,
        )

    # Load everything up front; derive into throwaway views afterwards so no
    # autoflush ever writes derived attributes back to the DB.
    schemas = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    employees = list(
        db.execute(
            select(Employee).where(Employee.policy_year_id == policy_year_id)
        ).scalars()
    )
    deps_per_employee: dict[str, int] = defaultdict(int)
    if has_dependants:
        for emp_id, count in db.execute(
            select(Dependant.employee_id, func.count(Dependant.id))
            .where(Dependant.policy_year_id == policy_year_id)
            .where(Dependant.employee_id.is_not(None))
            .group_by(Dependant.employee_id)
        ).all():
            deps_per_employee[emp_id] = count

    persisted = _persisted_by_name(db, policy_year_id, product_id)
    cats = _transient_categories(valid, persisted)
    cats_by_priority = sorted(cats, key=lambda c: (_status_rank(c.status), c.priority))
    exact_lookup = _build_exact_lookup(cats_by_priority)
    category_tokens = {
        c.id: tokenize(canonicalize_category_name(c.display_name))
        for c in cats_by_priority
    }

    # The preview must apply the SAME entity gate as a real matching run, or a
    # broker sees a headcount the run won't reproduce — so aliases too, loaded
    # once outside the loop.
    aliases = entity_alias_map(db, client_id)

    emp_counts: dict[str, int] = defaultdict(int)
    dep_counts: dict[str, int] = defaultdict(int)
    matched = 0
    for emp in employees:
        view = _EmpView(
            attribute_values=emp.attribute_values or {},
            derived_attribute_values=derive(emp.attribute_values or {}, schemas),
        )
        outcome = match_one(
            view,
            cats_by_priority,
            exact_lookup,
            category_tokens,
            entity_aliases=aliases,
        )
        if outcome.category_id is None:
            continue
        matched += 1
        emp_counts[outcome.category_id] += 1
        if has_dependants:
            dep_counts[outcome.category_id] += deps_per_employee.get(emp.id, 0)

    # Echo back every requested row (including blank ones) so the UI can key by
    # the order it sent.
    counts = [
        CategoryCount(
            key=d.key,
            employees=emp_counts.get(d.key, 0),
            dependants=dep_counts.get(d.key, 0),
        )
        for d in drafts
    ]
    return MemberCounts(
        counts=counts,
        employees_total=len(employees),
        employees_matched=matched,
        has_dependants=has_dependants,
    )
