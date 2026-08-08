"""Persist each employee's Flexible-Benefits wallet onto the employee rows.

This is the Flex counterpart to ``matching_engine.match_policy_year``: where the
matching engine writes ``Employee.matched_categories`` for insured products, this
writes the resolved Flex wallet (family status, tier, amount, currency) onto the
``flex_*`` columns so the benefit statement, activation and reporting can read a
stable entitlement without recomputing it per request.

The resolution itself is delegated to ``compute_flex_membership`` — the same
read-only logic that powers the live membership card — so the persisted snapshot
can never diverge from the preview. Assignment writes only happen for a confirmed
scheme (see the endpoint guard); the snapshot is refreshed on re-assign and
cleared only for a row that resolves to nothing. A LEAVER KEEPS THEIRS, pro-rated
to the period they were covered — see ``assign_flex_membership``.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.models import Employee, FlexScheme
from app.models.employee import EMPLOYEE_STATUS_ACTIVE, EMPLOYEE_STATUS_TERMINATED
from app.models.flex_scheme import FlexSchemeStatus
from app.services.flex_membership import EmployeeFlex, compute_flex_membership

logger = logging.getLogger(__name__)

# Mirror the matching engine's batched-flush size for large rosters.
FLUSH_BATCH_SIZE = 500



@dataclass(frozen=True)
class FlexAssignmentSummary:
    employees_total: int
    # Employees who landed in an eligibility tier (i.e. carry a wallet).
    employees_assigned: int
    # Employees with a resolved family status (superset of the above).
    employees_with_status: int
    by_tier: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0


def confirmed_flex_scheme(db: Session, policy_year_id: str) -> FlexScheme | None:
    """Return the policy year's Flex scheme iff it exists and is confirmed."""
    row = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    if row is None or row.status != FlexSchemeStatus.confirmed:
        return None
    return row


def _clear_flex(emp: Employee) -> None:
    emp.flex_family_status = None
    emp.flex_tier_name = None
    emp.flex_wallet_amount = None
    emp.flex_proration = None
    emp.flex_currency = None
    emp.flex_source = None
    emp.flex_assigned_at = None


def _apply(emp: Employee, fx: EmployeeFlex, now: datetime) -> None:
    emp.flex_family_status = fx.family_status
    emp.flex_tier_name = fx.tier_name
    emp.flex_wallet_amount = fx.wallet_amount
    emp.flex_proration = fx.proration
    emp.flex_currency = fx.currency
    emp.flex_source = fx.source
    emp.flex_assigned_at = now


def assign_flex_membership(
    db: Session, policy_year_id: str, client_id: str | None
) -> FlexAssignmentSummary:
    """Resolve and persist every employee's Flex wallet for a policy year.

    Computes the membership snapshot (the single source of truth, shared with the
    read-only preview) and writes it onto the ``flex_*`` columns. NEVER commits —
    the caller owns the audit-log entry and the transaction (matching
    ``match_policy_year``'s contract), so a failure mid-run rolls back the whole
    assignment instead of leaving half the roster on new wallets. Large rosters
    are flushed in batches.

    **A LEAVER KEEPS THEIR WALLET, pro-rated to the period they were covered.**
    This used to end with a bulk ``UPDATE … SET flex_* = NULL WHERE status !=
    'active'``, which every listing-sync apply triggered in the same request that
    terminated the member — so the leaver sheet, whose whole purpose is to settle
    up, printed their claims against a BLANK allocation and a blank balance. A
    pro-rated allowance is both the honest figure and a better bound on what they
    may still draw than a null (which left ``available`` undefined, so nothing
    bounded them at all). Under a scheme that does not pro-rate they simply keep
    the full annual allowance, which is what such a scheme owes them.

    ``_clear_flex`` still fires for a row that resolves to nothing — a member who
    matches no tier, or whose cover does not intersect the scheme period.
    """
    started = time.monotonic()
    now = datetime.now(tz=UTC)

    membership = compute_flex_membership(
        db, policy_year_id, client_id, include_terminated=True
    )
    by_emp: dict[str, EmployeeFlex] = {fx.employee_id: fx for fx in membership.assignments}

    rows = list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == policy_year_id,
                Employee.status.in_([EMPLOYEE_STATUS_ACTIVE, EMPLOYEE_STATUS_TERMINATED]),
            )
        ).scalars()
    )

    assigned = 0
    with_status = 0
    active_total = 0
    by_tier: dict[str, int] = defaultdict(int)
    pending = 0

    for emp in rows:
        fx = by_emp.get(emp.id)
        is_active = emp.status == EMPLOYEE_STATUS_ACTIVE
        active_total += int(is_active)
        if fx is None:
            # A row compute didn't resolve (shouldn't happen) — clear it.
            _clear_flex(emp)
        else:
            _apply(emp, fx, now)
            # Headcounts describe the ACTIVE roster, matching the preview.
            if is_active and fx.family_status:
                with_status += 1
            if is_active and fx.tier_name:
                assigned += 1
                by_tier[fx.tier_name] += 1
        pending += 1
        if pending >= FLUSH_BATCH_SIZE:
            db.flush()
            pending = 0

    return FlexAssignmentSummary(
        employees_total=active_total,
        employees_assigned=assigned,
        employees_with_status=with_status,
        by_tier=dict(by_tier),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def assign_and_audit(
    db: Session,
    user: CurrentUser,
    policy_year_id: str,
    client_id: str | None,
    *,
    trigger: str | None = None,
) -> FlexAssignmentSummary:
    """Assign Flex wallets and append an audit row. Caller owns the commit.

    ``trigger`` tags how the run was initiated (e.g. ``"auto_on_upload"``) in the
    audit entry.
    """
    summary = assign_flex_membership(db, policy_year_id, client_id)
    after: dict[str, object] = {
        "employees_total": summary.employees_total,
        "employees_assigned": summary.employees_assigned,
        "employees_with_status": summary.employees_with_status,
        "by_tier": summary.by_tier,
        "duration_ms": summary.duration_ms,
    }
    if trigger:
        after["trigger"] = trigger
    write_audit(db, user, "flex_scheme.assign", "flex_scheme", policy_year_id, after=after)
    return summary


def assign_flex_safe(
    db: Session,
    user: CurrentUser,
    policy_year_id: str,
    client_id: str | None,
    *,
    trigger: str,
    errors: list[str],
) -> None:
    """Best-effort wallet (re)assignment for the upload paths.

    No-op when no confirmed scheme exists. Never raises: on failure it rolls back
    the uncommitted flex mutations (the already-committed upload is untouched) and
    appends a user-facing note to ``errors``. Shared by the employee and
    dependant upload handlers so the contract lives in one place.
    """
    try:
        if confirmed_flex_scheme(db, policy_year_id) is None:
            return
        assign_and_audit(db, user, policy_year_id, client_id, trigger=trigger)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("flex auto-assignment failed (trigger=%s)", trigger)
        errors.append("Flex wallet assignment failed; re-assign from the Flex tab.")


__all__ = [
    "FlexAssignmentSummary",
    "assign_and_audit",
    "assign_flex_membership",
    "assign_flex_safe",
    "confirmed_flex_scheme",
]
