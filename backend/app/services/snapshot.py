"""Snapshot builder — freezes a policy year into an immutable JSON blob.

Activation captures everything downstream consumers (insurers, audit reviewers,
historical comparisons) might need to reconstruct the placement state at that
moment without re-querying the live tables.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import (
    Category,
    Dependant,
    Employee,
    EmployeeAttributeSchema,
    EmployeePlanOverride,
    EnrollmentWindow,
    LeaveElection,
    Plan,
    PolicyYear,
    Product,
)
from app.models.enrollment_window import WindowStatus
from app.models.leave_election import LeaveElectionStatus
from app.services.product_terms import envelope_from_terms, resolve_terms

SNAPSHOT_VERSION = "v1"

# Field allowlists per entity — explicit so we never serialise an internal
# audit column by accident and so changes to the model don't silently widen
# the snapshot payload.
_CATEGORY_FIELDS = (
    "id", "product_id", "priority", "display_name", "raw_description",
    "matching_rule", "rule_human_readable", "participation_model",
    "plan_assignments", "source", "confidence", "status",
)
_EMPLOYEE_FIELDS = (
    "id", "staff_id", "employee_name", "attribute_values",
    "derived_attribute_values", "matched_category_id", "match_method",
    "match_confidence", "matched_categories",
    # Flexible-Benefits wallet — part of the activated entitlement, so it must be
    # frozen alongside the insured matches (see services/flex_assignment).
    "flex_family_status", "flex_tier_name", "flex_wallet_amount",
    "flex_currency", "flex_source",
)
_DEPENDANT_FIELDS = ("id", "employee_id", "attribute_values", "link_method")
_SCHEMA_FIELDS = (
    "attribute_id", "display_name", "data_type", "enum_values",
    "is_required", "is_pii", "derived_from", "derivation_rule",
)
_PRODUCT_FIELDS = (
    "id", "code", "display_name", "participation_model",
    "has_dependants", "is_outpatient",
)
_PLAN_FIELDS = (
    "id", "product_id", "code", "display_name",
    "benefit_schedule", "cover_description", "annual_policy_limit",
    "source", "confidence", "status",
)
_OVERRIDE_FIELDS = (
    "id", "employee_id", "product_id", "product_code", "plan_code",
    "tier_category_id", "declined", "covered_dependant_ids", "flex_price_tag",
    "source", "source_ref",
)


def _project(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: getattr(obj, f) for f in fields}


def build_snapshot(
    db: Session,
    policy_year_id: str,
    *,
    generated_by: str | None = None,
) -> dict[str, Any]:
    py = db.get(PolicyYear, policy_year_id)
    if py is None:
        raise ValueError(f"Policy year {policy_year_id} not found")

    categories = list(
        db.execute(select(Category).where(Category.policy_year_id == policy_year_id))
        .scalars()
        .all()
    )
    employees = list(
        db.execute(select(Employee).where(Employee.policy_year_id == policy_year_id))
        .scalars()
        .all()
    )
    dependants = list(
        db.execute(select(Dependant).where(Dependant.policy_year_id == policy_year_id))
        .scalars()
        .all()
    )
    # Schemas + products filtered to (NULL client_id) plus the owning tenant
    # — without this the snapshot would embed every client's catalog.
    schemas = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, py.client_id)
            )
        )
        .scalars()
        .all()
    )
    products = list(
        db.execute(
            select(Product).where(tenant_or_global(Product.client_id, py.client_id))
        )
        .scalars()
        .all()
    )
    plans = list(
        db.execute(select(Plan).where(Plan.policy_year_id == policy_year_id))
        .scalars()
        .all()
    )
    # Resolved per-product coverage periods (override or the year's span), plus
    # the rolled-up envelope — freeze them so the activated state records each
    # product's actual coverage window, not just the policy year's nominal one.
    product_terms = resolve_terms(db, py)
    env_start, env_end = envelope_from_terms(py, product_terms)

    # Enrollment state — the elected deviations from the cohort defaults, the
    # confirmed leave trades, and the window lifecycle — so the activated state
    # reflects what members actually enrolled in, not just the defaults.
    overrides = list(
        db.execute(
            select(EmployeePlanOverride).where(
                EmployeePlanOverride.policy_year_id == policy_year_id
            )
        ).scalars().all()
    )
    windows = list(
        db.execute(
            select(EnrollmentWindow).where(
                EnrollmentWindow.policy_year_id == policy_year_id
            )
        ).scalars().all()
    )
    leave_elections = list(
        db.execute(
            select(LeaveElection).where(
                LeaveElection.policy_year_id == policy_year_id,
                LeaveElection.status == LeaveElectionStatus.confirmed,
            )
        ).scalars().all()
    )

    return {
        "version": SNAPSHOT_VERSION,
        "policy_year_id": py.id,
        "client_id": py.client_id,
        "year": py.year,
        "start_date": py.start_date.isoformat(),
        "end_date": py.end_date.isoformat(),
        "coverage_start": env_start.isoformat(),
        "coverage_end": env_end.isoformat(),
        "product_terms": [
            {
                "product_id": r.product_id,
                "code": r.code,
                "coverage_start": r.coverage_start.isoformat(),
                "coverage_end": r.coverage_end.isoformat(),
                "is_default": r.is_default,
            }
            for r in product_terms
        ],
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "generated_by": generated_by,
        "source_commit": os.environ.get("INSPRO_GIT_SHA", "unknown"),
        "categories": [_project(c, _CATEGORY_FIELDS) for c in categories],
        "employees": [_project(e, _EMPLOYEE_FIELDS) for e in employees],
        "dependants": [_project(d, _DEPENDANT_FIELDS) for d in dependants],
        "schemas": [_project(s, _SCHEMA_FIELDS) for s in schemas],
        "products": [_project(p, _PRODUCT_FIELDS) for p in products],
        "plans": [_project(pl, _PLAN_FIELDS) for pl in plans],
        "plan_overrides": [_project(o, _OVERRIDE_FIELDS) for o in overrides],
        "enrollment_windows": [
            {
                "id": w.id,
                "name": w.name,
                "window_type": w.window_type,
                "status": w.status,
                "opens_at": w.opens_at.isoformat() if w.opens_at else None,
                "closes_at": w.closes_at.isoformat() if w.closes_at else None,
            }
            for w in windows
        ],
        "leave_elections": [
            {"employee_id": le.employee_id, "action": le.action, "days": le.days}
            for le in leave_elections
        ],
        "counts": {
            "categories": len(categories),
            "employees": len(employees),
            "employees_matched": sum(1 for e in employees if e.matched_category_id),
            "dependants": len(dependants),
            "dependants_linked": sum(1 for d in dependants if d.employee_id),
            "plans": len(plans),
            "plan_overrides": len(overrides),
            "plan_overrides_declined": sum(1 for o in overrides if o.declined),
            "leave_elections": len(leave_elections),
            "enrollment_windows_open": sum(
                1 for w in windows if w.status == WindowStatus.open
            ),
        },
    }
