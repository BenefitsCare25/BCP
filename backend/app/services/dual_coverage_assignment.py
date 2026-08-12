"""Drop or restore ONE dependant's cover under ONE employee.

The roster keeps both parents' rows — a child whose mother and father both work
here is two coverage lines, and the import stopped collapsing them
(``roster_dedup``). Both lines are covered by default, because that is what the
placement file says. This module is the other half of the user's ask: the
ability to take the child OFF one side, and to put them back.

**It writes through ``EmployeePlanOverride``, which is the only source of truth
for per-employee coverage** (see CLAUDE.md). It deliberately does NOT invent a
second exclusion flag on the dependant: coverage would then have two answers,
and every reader — benefit statement, insurer listing, flex family tier, claims
— would have to consult both to be right.

Two things worth knowing:

- Excluding a dependant MATERIALIZES an override for each product it touches,
  but deliberately without a ``plan_code``: such an override keeps the cohort
  default plan (``coverage_resolver``), so this records WHO is covered without
  freezing WHAT they are covered for. A plan the member genuinely elected is
  read back and carried through, because passing None over it would silently
  demote them to the cohort default.
- The write goes through ``plan_overrides.set_plan_override``, the endpoint the
  broker's own coverage pane uses, so pricing, flex re-tagging, dependant-option
  validation and audit all happen exactly once and identically. Calling a path
  operation as a plain function is unusual, and it is the point: duplicating
  sixty lines of pricing context here is how the two paths would come to
  disagree about what a member costs.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.models import Dependant, Employee, EmployeePlanOverride, PolicyYear
from app.models.dependant import DEPENDANT_STATUS_ACTIVE
from app.schemas.enrollment import PlanOverrideUpsert
from app.services.dual_coverage import coverage_by_employee
from app.services.flex_membership import resolve_roster
from app.services.plan_hydration import hydrate_plans


def set_dependant_cover(
    db: Session,
    py: PolicyYear,
    user: CurrentUser,
    *,
    dependant: Dependant,
    covered: bool,
) -> list[str]:
    """Include or exclude ``dependant`` across its own employee's plans.

    Returns the product codes whose cover actually moved — an empty list means
    the request agreed with what was already on file, which is a no-op rather
    than an error (the same click arriving twice must not cost a premium).
    """
    from app.api.v1.plan_overrides import set_plan_override

    employee = db.get(Employee, dependant.employee_id or "")
    if employee is None:
        raise ValueError("This dependant is not linked to an employee.")

    # The effective covered set per product, resolved exactly as the review
    # sheet resolves it — including the cohort-default sweep for an employee
    # with no explicit election. Reading overrides directly would disagree with
    # the sheet the broker is looking at while they click.
    roster = resolve_roster(db, py.id, py.client_id, with_dependant_detail=True)
    effective = coverage_by_employee(db, py, roster, {employee.id}).get(employee.id, {})
    plans = hydrate_plans([employee], db, py.id).get(employee.id, [])

    # Whatever plan each product already has an OPINION about. An override with
    # no plan_code keeps the cohort default (`coverage_resolver`), which is what
    # this write wants — it is changing who is covered, not what they are
    # covered for. Passing the resolved plan instead would PIN it, so a later
    # re-match or slip change would stop reaching this member. Passing None
    # blindly is the opposite hazard: it would wipe a plan the member actually
    # elected, so an existing election is carried through untouched.
    pinned = {
        row.product_code: row.plan_code
        for row in db.execute(
            select(EmployeePlanOverride).where(
                EmployeePlanOverride.employee_id == employee.id
            )
        ).scalars()
    }

    changed: list[str] = []
    for mp in plans:
        current = effective.get(mp.product_code)
        if current is None:
            continue  # product carries no dependant cover at all
        wanted = set(current) | {dependant.id} if covered else set(current) - {dependant.id}
        if wanted == set(current):
            continue
        set_plan_override(
            employee_id=employee.id,
            product_code=mp.product_code,
            body=PlanOverrideUpsert(
                plan_code=pinned.get(mp.product_code),
                declined=False,
                # Omitted deliberately, NOT passed as None: the endpoint reads
                # `model_fields_set` to tell "leave the stored level alone" from
                # "clear it", and clearing a member's elected dependant option
                # as a side effect of a dual-coverage click would silently
                # change what they are covered for.
                covered_dependant_ids=sorted(wanted),
            ),
            emp=employee,
            user=user,
            db=db,
        )
        changed.append(mp.product_code)
    return changed


def active_dependant_ids(db: Session, employee_id: str) -> set[str]:
    return set(
        db.execute(
            select(Dependant.id).where(
                Dependant.employee_id == employee_id,
                Dependant.status == DEPENDANT_STATUS_ACTIVE,
            )
        ).scalars()
    )
