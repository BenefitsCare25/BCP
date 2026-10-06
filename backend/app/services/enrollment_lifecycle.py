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

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
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
from app.services.enrollment_events import record_event
from app.services.override_writer import upsert_override

logger = logging.getLogger(__name__)


def _reason(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or "Failed checks.")
    return str(detail)


def _submission_problem(
    db: Session,
    window: EnrollmentWindow,
    enrollment: Enrollment,
    *,
    require_priced: bool,
) -> str | None:
    """Why this enrollment's saved choices can't be finalized now, or None.

    Re-runs eligibility + the wallet guard (and, for a draft being submitted on
    the member's behalf, the unpriced guard — nobody acknowledged it). Runs in a
    SAVEPOINT that is rolled back on failure, so a failed check never leaves a
    half-revalidated election behind."""
    from app.services.enrollment_elections import revalidate_enrollment
    from app.services.enrollment_flex_guard import (
        assert_elections_priced,
        assert_within_wallet,
    )

    savepoint = db.begin_nested()
    try:
        revalidate_enrollment(db, enrollment)
        assert_within_wallet(db, enrollment, window)
        if require_priced:
            assert_elections_priced(db, enrollment, window, acknowledge=False)
    except HTTPException as exc:
        savepoint.rollback()
        return _reason(exc)
    savepoint.commit()
    return None


def _invalid_submissions(
    db: Session,
    window: EnrollmentWindow,
    enrollments: Sequence[Enrollment],
) -> list[dict[str, Any]]:
    """Validate every submitted row before any projection begins."""
    invalid: list[dict[str, Any]] = []
    for enrollment in enrollments:
        if enrollment.status != EnrollmentStatus.submitted:
            continue
        problem = _submission_problem(db, window, enrollment, require_priced=False)
        if problem is not None:
            invalid.append(
                {
                    "enrollment_id": enrollment.id,
                    "employee_id": enrollment.employee_id,
                    "detail": problem,
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


def create_missing_enrollments(db: Session, window: EnrollmentWindow) -> int:
    """Give every active employee of the window's year an enrollment, snapshotting
    their current coverage as the baseline. Idempotent — returns the count created.

    Must run AFTER matching: the baseline is read off ``matched_categories``."""
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
    db.flush()
    return created


def sync_open_windows(
    db: Session, user: CurrentUser, policy_year_id: str, *, trigger: str
) -> int:
    """Enrol new staff into every open period of the year that members can still
    act in. Run after a roster change adds employees, so a period's population
    never silently lags the roster (it used to need a manual "Sync" press).

    A period past its deadline is skipped: its members are locked out, so a new
    hire added there could never choose — and under deemed-decline they would
    lose voluntary cover at close without having been asked. The broker can
    still add them deliberately (extend the deadline first) from the Overview."""
    windows = db.execute(
        select(EnrollmentWindow).where(
            EnrollmentWindow.policy_year_id == policy_year_id,
            EnrollmentWindow.status == WindowStatus.open,
        )
    ).scalars().all()
    total = 0
    for window in windows:
        if window.phase == "overdue":
            continue
        created = create_missing_enrollments(db, window)
        total += created
        if created:
            write_audit(
                db, user, action="sync_enrollment_window",
                entity_type="enrollment_window", entity_id=window.id,
                after={"enrollments_created": created, "trigger": trigger},
            )
    return total


OPEN_PERIOD_SYNC_SKIPPED = (
    "New staff were not added to the open enrolment period because matching "
    "did not complete. Re-run matching, then add them from Enrollment → Overview."
)


def sync_open_windows_safe(
    db: Session,
    user: CurrentUser,
    policy_year_id: str,
    *,
    trigger: str,
    errors: list[str],
) -> None:
    """Best-effort ``sync_open_windows`` for the roster paths, which have already
    committed their own work: a failure rolls back only the sync and tells the
    broker, it never undoes the upload."""
    try:
        sync_open_windows(db, user, policy_year_id, trigger=trigger)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("open enrolment sync failed (trigger=%s)", trigger)
        errors.append(
            "New staff could not be added to the open enrolment period; use "
            "Sync new employees on the Enrollment page."
        )


def open_window(
    db: Session, window: EnrollmentWindow, user: CurrentUser
) -> int:
    """Create enrollments for every active employee that lacks one. Returns count."""
    # Source selection was removed from the unified price book. Normalize an
    # untouched pre-upgrade draft at the irreversible draft -> open boundary so
    # legacy ``manual`` entries cannot suppress slip recommendations. Do not
    # rewrite an already-open window during the idempotent employee-sync path.
    migrated_legacy_source = (
        window.status == WindowStatus.draft
        and isinstance(window.flex_price_source, dict)
        and bool(window.flex_price_source)
    )
    if window.status == WindowStatus.draft:
        window.flex_price_source = None
    created = create_missing_enrollments(db, window)
    window.status = WindowStatus.open
    db.flush()
    write_audit(
        db, user, action="open_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id,
        after={
            "enrollments_created": created,
            "legacy_price_source_migrated": migrated_legacy_source,
        },
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
    record_event(db, enrollment, "confirmed", actor_id=user.user_id)
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


def _discard_unsent_leave(db: Session, enrollment: Enrollment) -> None:
    """Zero and finalize the leave trade of an enrollment that was never sent.

    Unsent choices are discarded at close — coverage AND leave alike. This used
    to confirm the trade under deemed-keep-current, which (a) contradicted the
    close dialog's "saved changes are discarded", (b) was inconsistent with the
    member's unsent plan changes being dropped, and (c) could put a trade live
    that had just FAILED its submit check (an overdrawn buy). The broker keeps a
    member's saved choices by submitting them — the close dialog offers that
    for everyone in one tick. A member with no saved trade has no row; only
    saving moves an enrollment off ``not_started``.
    """
    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enrollment.id)
    ).scalar_one_or_none()
    if leave is None:
        return
    leave.action = LeaveAction.none
    leave.days = 0.0
    leave.flex_amount = None
    leave.status = LeaveElectionStatus.confirmed


def _apply_default(
    db: Session,
    window: EnrollmentWindow,
    enr: Enrollment,
    user: CurrentUser,
    summary: dict[str, int],
) -> None:
    """Finalize an unsubmitted enrollment per the window's default behavior.

    A member who chose "decline all" (status ``declined``) is declined whatever
    the default — ``close_preview`` reports them as their own group for that
    reason."""
    declines = enr.status == EnrollmentStatus.declined or (
        window.default_behavior == DefaultBehavior.decline
    )
    _discard_unsent_leave(db, enr)
    if declines:
        _decline_in_scope(db, enr, user)
    enr.status = EnrollmentStatus.deemed
    record_event(db, enr, "deemed_declined" if declines else "deemed_kept", actor_id=user.user_id)
    summary["deemed_declined" if declines else "deemed_kept"] += 1


def close_window(
    db: Session,
    window: EnrollmentWindow,
    user: CurrentUser,
    *,
    submit_saved: bool = False,
) -> dict[str, int]:
    """Finalize all enrollments per default_behavior and close the window.

    Every submitted enrollment is revalidated before any finalization begins.
    Invalid submissions block the close as one atomic operation; they never
    silently fall back to the window default.

    ``submit_saved`` submits, on the member's behalf, every enrollment with saved
    but unsent choices (``in_progress``) that passes the same checks a submit
    would — including the unpriced guard, since nobody acknowledged it. One that
    fails falls back to the default behavior; ``close_preview`` lists those by
    name before the broker confirms, so the fallback is never silent.
    """
    enrollments = db.execute(
        select(Enrollment).where(Enrollment.window_id == window.id).with_for_update()
    ).scalars().all()
    invalid = _invalid_submissions(db, window, enrollments)
    returned = [enr for enr in enrollments if enr.status == EnrollmentStatus.returned]
    if returned:
        raise HTTPException(status.HTTP_409_CONFLICT,
            "Resolve returned enrolments before closing: employees must resubmit, "
            "or a system administrator must cancel their choices with a reason.")
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
        "invalid_submitted": 0, "submitted_at_close": 0, "saved_discarded": 0,
    }
    now = datetime.now(UTC)
    for enr in enrollments:
        if enr.status in (EnrollmentStatus.confirmed, EnrollmentStatus.deemed):
            summary["already"] += 1
            continue
        if enr.status == EnrollmentStatus.submitted:
            project_enrollment(db, enr, user)
            summary["confirmed"] += 1
            continue
        if enr.status == EnrollmentStatus.in_progress:
            if submit_saved and _submission_problem(
                db, window, enr, require_priced=True
            ) is None:
                enr.status = EnrollmentStatus.submitted
                enr.submitted_at = now
                enr.submitted_by = user.user_id
                project_enrollment(db, enr, user)
                summary["confirmed"] += 1
                summary["submitted_at_close"] += 1
                continue
            summary["saved_discarded"] += 1
        # not_started / in_progress (unsent) / declined → default behavior.
        _apply_default(db, window, enr, user, summary)
    window.status = WindowStatus.closed
    db.flush()
    write_audit(
        db, user, action="close_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id, after={**summary, "submit_saved": submit_saved},
    )
    return summary


_PREVIEW_LIST_LIMIT = 50


def close_preview(db: Session, window: EnrollmentWindow) -> dict[str, Any]:
    """What closing would do to each member, WITHOUT doing it.

    Runs the same checks close runs, so its lists are the truth: submissions
    that would block the close, and saved-but-unsent choices that could (or
    could not) be submitted on the member's behalf. Every check runs in a
    savepoint; the caller must still roll back (the GET handler does)."""
    rows = db.execute(
        select(Enrollment, Employee.staff_id, Employee.employee_name)
        .join(Employee, Enrollment.employee_id == Employee.id)
        .where(Enrollment.window_id == window.id)
        .order_by(Employee.staff_id)
    ).all()
    counts = {
        "total": len(rows), "confirmed": 0, "submitted": 0, "saved_not_sent": 0,
        "not_started": 0, "declined": 0, "returned": 0,
    }
    invalid: list[dict[str, Any]] = []
    saved_blocked: list[dict[str, Any]] = []
    saved_submittable = 0
    for enr, staff_id, name in rows:
        who = {"enrollment_id": enr.id, "staff_id": staff_id, "employee_name": name}
        if enr.status in (EnrollmentStatus.confirmed, EnrollmentStatus.deemed):
            counts["confirmed"] += 1
        elif enr.status == EnrollmentStatus.submitted:
            counts["submitted"] += 1
            problem = _submission_problem(db, window, enr, require_priced=False)
            if problem is not None:
                invalid.append({**who, "reason": problem})
        elif enr.status == EnrollmentStatus.returned:
            counts["returned"] += 1
            invalid.append({**who, "reason": "Awaiting correction and a new signed submission."})
        elif enr.status == EnrollmentStatus.in_progress:
            counts["saved_not_sent"] += 1
            problem = _submission_problem(db, window, enr, require_priced=True)
            if problem is None:
                saved_submittable += 1
            else:
                saved_blocked.append({**who, "reason": problem})
        elif enr.status == EnrollmentStatus.declined:
            counts["declined"] += 1
        else:
            counts["not_started"] += 1
    return {
        **counts,
        "default_behavior": window.default_behavior,
        "invalid_submitted": invalid[:_PREVIEW_LIST_LIMIT],
        "invalid_submitted_count": len(invalid),
        "saved_submittable": saved_submittable,
        "saved_blocked": saved_blocked[:_PREVIEW_LIST_LIMIT],
        "saved_blocked_count": len(saved_blocked),
    }


def window_progress(db: Session, window: EnrollmentWindow) -> dict[str, int]:
    """Members per status, plus active staff the period has not picked up yet
    (added to the roster after it opened and before anyone synced)."""
    counts = dict.fromkeys(
        (
            EnrollmentStatus.not_started, EnrollmentStatus.in_progress,
            EnrollmentStatus.submitted, EnrollmentStatus.confirmed,
            EnrollmentStatus.deemed, EnrollmentStatus.declined,
            EnrollmentStatus.returned,
        ),
        0,
    )
    for status_value, n in db.execute(
        select(Enrollment.status, func.count(Enrollment.id))
        .where(Enrollment.window_id == window.id)
        .group_by(Enrollment.status)
    ).all():
        counts[str(status_value)] = int(n)
    enrolled = select(Enrollment.employee_id).where(Enrollment.window_id == window.id)
    not_in_period = db.execute(
        select(func.count(Employee.id)).where(
            Employee.policy_year_id == window.policy_year_id,
            Employee.status == "active",
            Employee.id.not_in(enrolled),
        )
    ).scalar_one()
    return {
        **counts,
        "total": sum(counts.values()),
        "not_in_period": 0 if window.status == WindowStatus.closed else int(not_in_period),
    }


def confirm_submitted(
    db: Session, window: EnrollmentWindow, user: CurrentUser
) -> tuple[list[str], list[dict[str, Any]]]:
    """Confirm every submitted enrollment that still passes its checks.

    Each one is checked and projected inside its own savepoint, so one member
    failing (an overdrawn wallet, a plan that is no longer offered) never stops
    the rest. Returns ``(confirmed employee ids, failures)``."""
    rows = db.execute(
        select(Enrollment, Employee.staff_id, Employee.employee_name)
        .join(Employee, Enrollment.employee_id == Employee.id)
        .where(
            Enrollment.window_id == window.id,
            Enrollment.status == EnrollmentStatus.submitted,
        )
        .order_by(Employee.staff_id)
        .with_for_update(of=Enrollment)
    ).all()
    confirmed: list[str] = []
    failed: list[dict[str, Any]] = []
    for enr, staff_id, name in rows:
        problem = _submission_problem(db, window, enr, require_priced=False)
        if problem is None:
            # Projection gets its own savepoint too: an unexpected failure
            # writing one member's overrides is reported against that member
            # rather than failing the whole batch with nobody confirmed.
            savepoint = db.begin_nested()
            try:
                project_enrollment(db, enr, user)
            except Exception:
                savepoint.rollback()
                logger.exception("bulk confirm: projecting enrollment %s failed", enr.id)
                problem = "Could not be confirmed — open it and confirm it individually."
            else:
                savepoint.commit()
                confirmed.append(enr.employee_id)
                continue
        failed.append({
            "enrollment_id": enr.id, "staff_id": staff_id,
            "employee_name": name, "reason": problem,
        })
    return confirmed, failed
