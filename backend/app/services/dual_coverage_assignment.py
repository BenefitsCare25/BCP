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

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.models import Dependant, Employee, EmployeePlanOverride, PolicyYear
from app.schemas.enrollment import PlanOverrideUpsert
from app.services.dual_coverage import coverage_by_employee, dependants_by_employee
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
    from app.api.v1.plan_overrides import delete_plan_override, set_plan_override

    employee = db.get(Employee, dependant.employee_id or "")
    if employee is None:
        raise ValueError("This dependant is not linked to an employee.")

    # The effective covered set per product, resolved exactly as the review
    # sheet resolves it — including the cohort-default sweep for an employee
    # with no explicit election. Reading overrides directly would disagree with
    # the sheet the broker is looking at while they click.
    roster = resolve_roster(db, py.id, py.client_id, with_dependant_detail=True)
    # `scope` is the set of products whose cover EXTENDS to dependants at all,
    # read from the cohort rather than from who happens to be selected now.
    # Restoring must be bounded by it: without that, dropping a child from the
    # three products that carry dependants and then restoring them put the child
    # on all eight the employee held — group term life among them — because a
    # product covering no dependants looks identical to one this employee simply
    # has not elected. Found in the browser, not by a test.
    scope: dict[str, set[str]] = {}
    effective = coverage_by_employee(db, py, roster, {employee.id}, scope).get(
        employee.id, {}
    )
    covering = scope.get(employee.id, set())
    plans = hydrate_plans([employee], db, py.id).get(employee.id, [])

    # Whatever plan each product already has an OPINION about. An override with
    # no plan_code keeps the cohort default (`coverage_resolver`), which is what
    # this write wants — it is changing who is covered, not what they are
    # covered for. Passing the resolved plan instead would PIN it, so a later
    # re-match or slip change would stop reaching this member. Passing None
    # blindly is the opposite hazard: it would wipe a plan the member actually
    # elected, so an existing election is carried through untouched.
    stored = {
        row.product_code: row
        for row in db.execute(
            select(EmployeePlanOverride).where(
                EmployeePlanOverride.employee_id == employee.id
            )
        ).scalars()
    }
    pinned = {code: row.plan_code for code, row in stored.items()}

    # What the cohort default WOULD sweep in for this employee — the one set a
    # restore has to recognise, read through the same helper the resolver uses.
    sweep = {d.id for d in dependants_by_employee(roster).get(employee.id, [])}

    changed: list[str] = []
    for mp in plans:
        current = effective.get(mp.product_code)
        if current is None:
            continue
        # A product that does not carry dependants can still be DROPPED from —
        # an override may have put the life there before this rule existed — but
        # it can never be added to.
        if covered and mp.product_code not in covering:
            continue
        wanted = set(current) | {dependant.id} if covered else set(current) - {dependant.id}
        if wanted == set(current):
            continue
        # Restoring the whole cohort set leaves nothing for this override to
        # say. `coverage_by_employee` short-circuits on ANY explicit list, so
        # re-stating the set left the sweep permanently off: a drop followed by
        # a restore looked like a clean round trip, and the next dependant added
        # to that employee was silently uncovered on every product the broker
        # had touched. So a restore to the default RETRACTS the override
        # instead — entirely where it carries nothing else, or down to just its
        # covered list where it also holds an elected plan worth keeping.
        row = stored.get(mp.product_code)
        back_to_default = covered and wanted == sweep
        drop_row = back_to_default and not _has_other_opinion(row)

        # Filed BEFORE the write, in the same uncommitted transaction, so the
        # two land together or not at all. Every write below commits on its own,
        # so a summary row written after the loop was simply lost when a later
        # product raised — leaving cover half-moved and nothing to say so.
        write_audit(
            db,
            user,
            action="dual_coverage.set_cover",
            entity_type="dependant",
            entity_id=dependant.id,
            after={
                "covered": covered,
                "product": mp.product_code,
                "employee_staff_id": employee.staff_id,
            },
            employee_id=employee.id,
        )
        if drop_row:
            delete_plan_override(
                employee_id=employee.id,
                product_code=mp.product_code,
                emp=employee,
                user=user,
                db=db,
            )
        else:
            set_plan_override(
                employee_id=employee.id,
                product_code=mp.product_code,
                body=PlanOverrideUpsert(
                    plan_code=pinned.get(mp.product_code),
                    declined=False,
                    # None CLEARS the stored list (`override_writer` keeps a
                    # value only at its `_KEEP` sentinel). The schema refuses an
                    # override that states nothing at all, which is why the
                    # plan_code has to survive for this branch to be legal.
                    # `dependant_option_ids` is omitted rather than nulled: the
                    # endpoint reads `model_fields_set`, and clearing a member's
                    # elected dependant option as a side effect of a
                    # dual-coverage click would change what they are covered
                    # for, not just who is covered.
                    covered_dependant_ids=(
                        None
                        if back_to_default and pinned.get(mp.product_code)
                        else sorted(wanted)
                    ),
                ),
                emp=employee,
                user=user,
                db=db,
            )
        changed.append(mp.product_code)
    return changed


def _has_other_opinion(row: EmployeePlanOverride | None) -> bool:
    """Whether the override says anything beyond who is covered.

    Decides retract-vs-clear on a restore. A row created purely to exclude one
    dual-covered life holds nothing else and must go, or the cohort sweep never
    resumes; a row carrying an elected plan, a decline, an elected dependant
    option level or a dated effective_from is somebody's real election and is
    kept.
    """
    if row is None:
        return False
    return bool(
        row.plan_code
        or row.declined
        or row.dependant_option_ids
        or row.effective_from
    )
