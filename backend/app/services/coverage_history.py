"""Per-employee coverage change history (the 'track' view).

Reads the append-only ``AuditLog`` (filtered to this member via the indexed
``employee_id`` column) and projects the coverage-relevant events into a compact,
UI-friendly timeline: who changed what, when, and the plan before/after.

The data already exists — every override write, enrollment action, and revert
writes an audit row. This module is a pure read; it adds no storage.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Employee
from app.models.user import User
from app.schemas.enrollment import CoverageHistoryEntry

# Coverage-relevant audit actions, mapped to a human label for the timeline.
ACTION_LABELS: dict[str, str] = {
    "set_plan_override": "Plan override set",
    "update_plan_override": "Plan override updated",
    "delete_plan_override": "Reverted to cohort default",
    "bulk_plan_override": "Plan changed (bulk update)",
    "update_enrollment_elections": "Elections updated",
    "update_enrollment_leave": "Leave trade updated (buy/sell)",
    "revert_leave": "Leave trade cleared",
    "submit_enrollment": "Enrollment submitted",
    "confirm_enrollment": "Enrollment confirmed",
    "reset_enrollment": "Elections reset to baseline",
    "revert_coverage_to_baseline": "Reverted to window baseline",
    "revert_coverage_to_default": "Reverted to cohort default",
}


def _plan_of(payload: dict | None) -> tuple[str | None, str | None, bool | None]:
    """Extract (product_code, plan_code, declined) from an override snapshot."""
    if not isinstance(payload, dict):
        return None, None, None
    declined = payload.get("declined")
    plan = None if declined else payload.get("plan_code")
    return payload.get("product_code"), plan, declined


def coverage_history(
    db: Session, employee: Employee, limit: int = 50
) -> list[CoverageHistoryEntry]:
    """Newest-first coverage timeline for one employee."""
    rows = (
        db.execute(
            select(AuditLog)
            .where(
                AuditLog.employee_id == employee.id,
                AuditLog.action.in_(ACTION_LABELS.keys()),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    # Resolve actor display names in one query.
    actor_ids = {r.user_id for r in rows if r.user_id}
    names: dict[str, str] = {}
    if actor_ids:
        for uid, email, display in db.execute(
            select(User.id, User.email, User.display_name).where(User.id.in_(actor_ids))
        ).all():
            names[uid] = display or email or uid

    entries: list[CoverageHistoryEntry] = []
    for r in rows:
        before_code, before_plan, _bd = _plan_of(r.before)
        after_code, after_plan, after_declined = _plan_of(r.after)
        # Override events carry product/plan in before/after; enrollment-level
        # events (entity_type='enrollment') don't, so those stay coarse markers.
        product_code = after_code or before_code
        entries.append(CoverageHistoryEntry(
            id=r.id,
            at=r.created_at.isoformat() if r.created_at else "",
            action=r.action,
            label=ACTION_LABELS.get(r.action, r.action),
            actor=names.get(r.user_id) if r.user_id else None,
            product_code=product_code,
            from_plan=before_plan,
            to_plan=after_plan,
            declined=after_declined,
        ))
    return entries
