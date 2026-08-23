"""Enrollment lifecycle — open, project, close.

- ``open_window``     creates an Enrollment per eligible employee, snapshotting
  their current effective coverage as the reverse-enrollment baseline.
- ``project_enrollment`` materializes one enrollment's elections into sparse
  ``EmployeePlanOverride`` rows (the effective state) and confirms its leave.
- ``close_window``    finalizes every enrollment per the window's
  ``default_behavior`` and flips the window to closed.

All functions take an explicit ``db`` + ``user``, write audit rows, and leave the
commit to the caller — matching the rest of the codebase.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.models import (
    Employee,
    EmployeePlanOverride,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    LeaveElection,
    Plan,
)
from app.models.employee_plan_override import OverrideSource
from app.models.enrollment import EnrollmentStatus
from app.models.enrollment_window import DefaultBehavior, WindowStatus
from app.models.leave_election import LeaveAction, LeaveElectionStatus
from app.services.coverage_resolver import (
    employee_category_defaults,
    employee_compulsory_product_ids,
    is_sparse_default,
    load_overrides,
)
from app.services.override_writer import upsert_override


def _invalid_submissions(
    db: Session,
    window: EnrollmentWindow,
    enrollments: Sequence[Enrollment],
) -> list[dict[str, Any]]:
    """Validate every submitted row before any projection begins."""
    from app.services.enrollment_elections import revalidate_enrollment
    from app.services.enrollment_flex_guard import assert_within_wallet

    invalid: list[dict[str, Any]] = []
    for enrollment in enrollments:
        if enrollment.status != EnrollmentStatus.submitted:
            continue
        try:
            revalidate_enrollment(db, enrollment)
            assert_within_wallet(db, enrollment, window)
        except HTTPException as exc:
            invalid.append(
                {
                    "enrollment_id": enrollment.id,
                    "employee_id": enrollment.employee_id,
                    "detail": exc.detail,
                }
            )
    return invalid


def plan_rank(db: Session, policy_year_id: str, product_id: str) -> dict[str, int]:
    """Rank a product's plan tiers (richer = higher) for upgrade/downgrade labels.

    No explicit tier order is stored, so this is a stable heuristic: order by
    creation time then code. Used only for the informational election label.
    """
    rows = db.execute(
        select(Plan.code)
        .where(Plan.policy_year_id == policy_year_id, Plan.product_id == product_id)
        .order_by(Plan.created_at, Plan.code)
    ).scalars().all()
    return {code: i for i, code in enumerate(rows)}


def _defaults_and_baseline(
    db: Session, employee: Employee
) -> tuple[dict[str, tuple[str, str | None]], dict[str, str]]:
    """One query → ``({product_id: (code, default_plan)}, {product_id: category_id})``.

    Combines the cohort-default lookup and the baseline-tier lookup (which the
    open / confirm paths need together) so they don't issue two near-identical
    category queries per employee and can't disagree on the chosen category.
    """
    from app.models.category import Category  # local import avoids a cycle
    from app.models.product import Product

    cat_ids = [
        m["category_id"]
        for m in (employee.matched_categories or [])
        if m.get("category_id")
    ]
    if not cat_ids:
        return {}, {}
    defaults: dict[str, tuple[str, str | None]] = {}
    baseline_cat: dict[str, str] = {}
    rows = db.execute(
        select(Category.id, Category.product_id, Product.code, Category.plan_assignments)
        .join(Product, Category.product_id == Product.id)
        .where(Category.id.in_(cat_ids), Category.product_id.is_not(None))
    ).all()
    for cat_id, product_id, code, pa in rows:
        plan_code = (pa or {}).get("plan_code") if isinstance(pa, dict) else None
        defaults[product_id] = (code, plan_code)
        baseline_cat[product_id] = cat_id
    return defaults, baseline_cat


def baseline_for(
    db: Session, employee: Employee
) -> dict[str, Any]:
    """Snapshot the employee's current effective coverage as the baseline bag.

    Each product entry now includes a ``compulsory`` flag so the enrollment UI
    can prevent members from declining required coverage.
    """
    defaults, baseline_cat = _defaults_and_baseline(db, employee)
    overrides = load_overrides(db, employee.policy_year_id, [employee.id])
    compulsory_ids = employee_compulsory_product_ids(db, employee)
    products: dict[str, dict[str, Any]] = {}
    for product_id, (code, default_plan) in defaults.items():
        ov = overrides.get((employee.id, product_id))
        compulsory = product_id in compulsory_ids
        base_tier = baseline_cat.get(product_id)
        if ov is not None:
            products[code] = {
                "plan_code": None if ov.declined else (ov.plan_code or default_plan),
                "tier_category_id": None if ov.declined else (ov.tier_category_id or base_tier),
                "declined": ov.declined,
                "covered_dependant_ids": ov.covered_dependant_ids,
                "dependant_option_ids": ov.dependant_option_ids,
                "compulsory": compulsory,
            }
        else:
            products[code] = {
                "plan_code": default_plan,
                "tier_category_id": base_tier,
                "declined": False,
                "covered_dependant_ids": None,
                "dependant_option_ids": None,
                "compulsory": compulsory,
            }
    return {"products": products, "leave": {"action": "none", "days": 0}}


def open_window(
    db: Session, window: EnrollmentWindow, user: CurrentUser
) -> int:
    """Create enrollments for every active employee that lacks one. Returns count."""
    existing = set(
        db.execute(
            select(Enrollment.employee_id).where(Enrollment.window_id == window.id)
        ).scalars()
    )
    employees = db.execute(
        select(Employee).where(
            Employee.policy_year_id == window.policy_year_id,
            Employee.status == "active",
        )
    ).scalars().all()
    created = 0
    for emp in employees:
        if emp.id in existing:
            continue
        db.add(Enrollment(
            window_id=window.id,
            policy_year_id=window.policy_year_id,
            client_id=window.client_id,
            employee_id=emp.id,
            status=EnrollmentStatus.not_started,
            baseline_snapshot=baseline_for(db, emp),
        ))
        created += 1
    window.status = WindowStatus.open
    db.flush()
    write_audit(
        db, user, action="open_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id, after={"enrollments_created": created},
    )
    return created


def project_enrollment(
    db: Session, enrollment: Enrollment, user: CurrentUser
) -> None:
    """Materialize one enrollment's elections into EmployeePlanOverride rows."""
    emp = db.get(Employee, enrollment.employee_id)
    # Keyed by product_id (not code) so two products sharing a code can't collide.
    defaults, baseline_cat = _defaults_and_baseline(db, emp) if emp else ({}, {})

    elections = db.execute(
        select(EnrollmentElection).where(
            EnrollmentElection.enrollment_id == enrollment.id
        )
    ).scalars().all()
    for el in elections:
        _code, default_plan = defaults.get(el.product_id, (el.product_code, None))
        declined = el.action == "decline"
        base_tier = baseline_cat.get(el.product_id)
        # A "keep at default" election needs no override — stay sparse. A tier
        # change that keeps the same plan_code (e.g. GPA "Option N") is NOT a
        # default: the elected tier differs from the matched cohort tier.
        if is_sparse_default(
            declined=declined,
            plan_code=el.elected_plan_code,
            tier_category_id=el.tier_category_id,
            covered_dependant_ids=el.covered_dependant_ids,
            default_plan=default_plan,
            base_tier=base_tier,
            dependant_option_ids=el.dependant_option_ids,
        ):
            existing = db.execute(
                select(EmployeePlanOverride).where(
                    EmployeePlanOverride.employee_id == enrollment.employee_id,
                    EmployeePlanOverride.product_id == el.product_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                db.delete(existing)
            continue
        upsert_override(
            db,
            employee_id=enrollment.employee_id,
            policy_year_id=enrollment.policy_year_id,
            client_id=enrollment.client_id,
            product_id=el.product_id,
            product_code=el.product_code,
            declined=declined,
            plan_code=el.elected_plan_code,
            tier_category_id=el.tier_category_id,
            flex_price_tag=el.flex_price_tag,
            covered_dependant_ids=el.covered_dependant_ids,
            dependant_option_ids=el.dependant_option_ids,
            source=OverrideSource.enrollment,
            source_ref=enrollment.id,
            modified_by=user.user_id,
        )

    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enrollment.id)
    ).scalar_one_or_none()
    if leave is not None:
        leave.status = LeaveElectionStatus.confirmed

    enrollment.status = EnrollmentStatus.confirmed
    enrollment.confirmed_at = datetime.now(UTC)
    enrollment.confirmed_by = user.user_id
    db.flush()
    write_audit(
        db, user, action="confirm_enrollment", entity_type="enrollment",
        entity_id=enrollment.id, after={"elections": len(elections)},
        employee_id=enrollment.employee_id,
    )


def _decline_in_scope(
    db: Session, enrollment: Enrollment, user: CurrentUser
) -> None:
    """Write declined overrides for every in-scope voluntary product the employee has.

    Compulsory products are skipped — a deemed-decline cannot remove required coverage.
    """
    emp = db.get(Employee, enrollment.employee_id)
    if emp is None:
        return
    defaults = employee_category_defaults(db, emp)
    compulsory_ids = employee_compulsory_product_ids(db, emp)
    window = db.get(EnrollmentWindow, enrollment.window_id)
    scope = window.product_scope if window else None
    for product_id, (code, _plan) in defaults.items():
        if scope and code not in scope:
            continue
        if product_id in compulsory_ids:
            continue  # never decline a compulsory product
        upsert_override(
            db,
            employee_id=enrollment.employee_id,
            policy_year_id=enrollment.policy_year_id,
            client_id=enrollment.client_id,
            product_id=product_id,
            product_code=code,
            declined=True,
            plan_code=None,
            source=OverrideSource.enrollment,
            source_ref=enrollment.id,
            modified_by=user.user_id,
        )


def _finalize_enrollment_leave(
    db: Session, enrollment: Enrollment, *, keep: bool
) -> None:
    """Finalize a deemed enrollment's leave the same way coverage is finalized.

    ``keep`` (deemed-keep-current) confirms the member's in-progress leave trade so
    it counts in the materialized flex balance; otherwise (deemed-decline) the trade
    is zeroed — a declined member carries no leave impact. Mirrors how
    ``project_enrollment`` confirms leave for submitted enrollments, so leave can't
    be silently dropped or stranded at window close.
    """
    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enrollment.id)
    ).scalar_one_or_none()
    if leave is None:
        return
    if not keep:
        leave.action = LeaveAction.none
        leave.days = 0.0
        leave.flex_amount = None
    leave.status = LeaveElectionStatus.confirmed


def close_window(
    db: Session, window: EnrollmentWindow, user: CurrentUser
) -> dict[str, int]:
    """Finalize all enrollments per default_behavior and close the window.

    Every submitted enrollment is revalidated before any finalization begins.
    Invalid submissions block the close as one atomic operation; they never
    silently fall back to the window default.
    """
    enrollments = db.execute(
        select(Enrollment).where(Enrollment.window_id == window.id)
    ).scalars().all()
    invalid = _invalid_submissions(db, window, enrollments)
    if invalid:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "invalid_submissions",
                "message": (
                    "The enrolment period cannot close until every submitted "
                    "enrollment passes current eligibility and flex checks."
                ),
                "count": len(invalid),
                "enrollments": invalid,
            },
        )
    summary = {
        "confirmed": 0, "deemed_kept": 0, "deemed_declined": 0, "already": 0,
        "invalid_submitted": 0,
    }
    for enr in enrollments:
        if enr.status == EnrollmentStatus.confirmed:
            summary["already"] += 1
            continue
        if enr.status == EnrollmentStatus.submitted:
            project_enrollment(db, enr, user)
            summary["confirmed"] += 1
            continue
        # not_started / in_progress / declined → apply default behavior.
        if enr.status == EnrollmentStatus.declined or (
            window.default_behavior == DefaultBehavior.decline
        ):
            _decline_in_scope(db, enr, user)
            _finalize_enrollment_leave(db, enr, keep=False)
            enr.status = EnrollmentStatus.deemed
            summary["deemed_declined"] += 1
        else:
            # keep_current — current effective coverage stands; confirm any
            # in-progress leave trade so it isn't dropped from the balance.
            _finalize_enrollment_leave(db, enr, keep=True)
            enr.status = EnrollmentStatus.deemed
            summary["deemed_kept"] += 1
    window.status = WindowStatus.closed
    db.flush()
    write_audit(
        db, user, action="close_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id, after=summary,
    )
    return summary
