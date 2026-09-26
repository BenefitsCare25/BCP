"""Voluntary cover is eligibility until the member enrols.

A category whose employee participation is ``voluntary`` (CDL's GD Plan 2:
"All Employees & their Eligible Dependants — Voluntary, named basis") makes a
member ELIGIBLE. It was rendered as held cover for all 482 CDL staff although
the slip names nobody. The member holds it once there is a record of taking it
up: a non-declined coverage override (enrolment projection, bulk update or a
broker's manual edit) or a confirmed enrolment election for that product.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmployeePlanOverride
from app.models.enrollment import (
    ElectionAction,
    Enrollment,
    EnrollmentElection,
    EnrollmentStatus,
)

# "keep" is NOT a take-up: the enrolment form records it for every product the
# member leaves untouched, so counting it would enrol everyone who submitted
# without opening voluntary dental. A keep that elects dependants projects an
# override, which the override query below already counts.
_TAKE_UP = (
    ElectionAction.upgrade,
    ElectionAction.downgrade,
    ElectionAction.enroll,
)


def employee_participation(category: Any) -> str | None:
    """``compulsory`` / ``voluntary`` for the employee, from the category."""
    detail = getattr(category, "participation_detail", None)
    mode = detail.get("employee") if isinstance(detail, dict) else None
    mode = mode or getattr(category, "participation_model", None)
    text = str(mode or "").strip().lower()
    return text if text in {"compulsory", "voluntary"} else None


def enrolled_products(
    db: Session, policy_year_id: str, employee_ids: list[str]
) -> set[tuple[str, str]]:
    """``{(employee_id, product_id)}`` the member has taken up this year."""
    if not employee_ids:
        return set()
    taken: set[tuple[str, str]] = set(
        db.execute(
            select(EmployeePlanOverride.employee_id, EmployeePlanOverride.product_id).where(
                EmployeePlanOverride.policy_year_id == policy_year_id,
                EmployeePlanOverride.employee_id.in_(employee_ids),
                EmployeePlanOverride.declined.is_(False),
            )
        ).tuples()
    )
    taken.update(
        db.execute(
            select(Enrollment.employee_id, EnrollmentElection.product_id)
            .join(Enrollment, EnrollmentElection.enrollment_id == Enrollment.id)
            .where(
                Enrollment.policy_year_id == policy_year_id,
                Enrollment.employee_id.in_(employee_ids),
                Enrollment.status == EnrollmentStatus.confirmed,
                EnrollmentElection.action.in_(_TAKE_UP),
            )
        ).tuples()
    )
    # A later decline (enrolment, bulk or manual) outranks an older take-up.
    declined = set(
        db.execute(
            select(EmployeePlanOverride.employee_id, EmployeePlanOverride.product_id).where(
                EmployeePlanOverride.policy_year_id == policy_year_id,
                EmployeePlanOverride.employee_id.in_(employee_ids),
                EmployeePlanOverride.declined.is_(True),
            )
        ).tuples()
    )
    return taken - declined
