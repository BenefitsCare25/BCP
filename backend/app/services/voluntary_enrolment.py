"""Voluntary cover is eligibility until the member enrols.

A category whose employee participation is ``voluntary`` (CDL's GD Plan 2:
"All Employees & their Eligible Dependants — Voluntary, named basis") makes a
member ELIGIBLE. It was rendered as held cover for all 482 CDL staff although
the slip names nobody. The member holds it once there is a record of taking it
up: a non-declined coverage override (enrolment projection, bulk update or a
broker's manual edit) or a confirmed enrolment election for that product.

An enrolment snapshots a product the member is only eligible for as NOT held
(``eligible_only``), so electing any of its tiers records ``enroll`` and
projects a take-up override; declining it changes nothing.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, EmployeePlanOverride
from app.models.enrollment import (
    ElectionAction,
    Enrollment,
    EnrollmentElection,
    EnrollmentStatus,
)

# "keep" is NOT a take-up: it is what the enrolment records for a product the
# member already holds and leaves as it is (an eligible-only product starts as
# not held, so electing it records "enroll"), and older enrolments recorded it
# for untouched voluntary products too — counting it would enrol everyone who
# submitted without opening voluntary dental. A keep that elects dependants
# projects an override, which the override query below already counts.
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


def election_take_ups(
    db: Session, policy_year_id: str, employee_ids: list[str]
) -> set[tuple[str, str]]:
    """``{(employee_id, product_id)}`` taken up by a confirmed enrolment election."""
    if not employee_ids:
        return set()
    return set(
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
    taken.update(election_take_ups(db, policy_year_id, employee_ids))
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


def voluntary_products_by_employee(
    db: Session, employees: list[Employee]
) -> dict[str, set[str]]:
    """Per member, the products their matched categories make them only
    ELIGIBLE for: employee participation ``voluntary``, and never one a
    compulsory category of theirs requires (that cover can't be declined, so it
    is always held). One query for the whole list."""
    cats_by_employee = {
        emp.id: [m["category_id"] for m in (emp.matched_categories or []) if m.get("category_id")]
        for emp in employees
    }
    cat_ids = {cat_id for ids in cats_by_employee.values() for cat_id in ids}
    rows = {
        row.id: row
        for row in db.execute(
            select(
                Category.id,
                Category.product_id,
                Category.participation_detail,
                Category.participation_model,
            ).where(Category.id.in_(cat_ids), Category.product_id.is_not(None))
        ).all()
    } if cat_ids else {}
    out: dict[str, set[str]] = {}
    for emp_id, ids in cats_by_employee.items():
        mine = [rows[cat_id] for cat_id in ids if cat_id in rows]
        voluntary = {r.product_id for r in mine if employee_participation(r) == "voluntary"}
        compulsory = {r.product_id for r in mine if r.participation_model == "compulsory"}
        out[emp_id] = voluntary - compulsory
    return out


def voluntary_product_ids(db: Session, employee: Employee) -> set[str]:
    """``voluntary_products_by_employee`` for one member."""
    return voluntary_products_by_employee(db, [employee])[employee.id]


def eligible_only_product_ids(
    db: Session,
    employee: Employee,
    *,
    voluntary: set[str] | None = None,
    overrides: dict[tuple[str, str], Any] | None = None,
) -> set[str]:
    """Voluntary products the member is eligible for and holds nothing of: no
    coverage override at all (a take-up or a decline) and no confirmed take-up
    election. They start an enrolment as not enrolled, draw no flex, and a
    decline of one changes nothing — the statement keeps showing 'eligible'.

    ``voluntary`` / ``overrides`` (``load_overrides``' shape) let a caller that
    already loaded them skip the queries."""
    products = voluntary if voluntary is not None else voluntary_product_ids(db, employee)
    if not products:
        return set()
    if overrides is None:
        held = set(
            db.execute(
                select(EmployeePlanOverride.product_id).where(
                    EmployeePlanOverride.policy_year_id == employee.policy_year_id,
                    EmployeePlanOverride.employee_id == employee.id,
                    EmployeePlanOverride.product_id.in_(products),
                )
            ).scalars()
        )
    else:
        held = {pid for emp_id, pid in overrides if emp_id == employee.id}
    candidates = products - held
    if not candidates:
        return set()
    taken = {
        pid
        for _emp_id, pid in election_take_ups(db, employee.policy_year_id, [employee.id])
    }
    return candidates - taken
