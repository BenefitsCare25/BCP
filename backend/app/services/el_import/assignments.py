"""Record each listed person's cover per product (``ListingAssignment``).

The listing states who is covered under what: an employee's own category per
product block, a dependant's category on their own row, and — in a block whose
dependant rows are blank — the employee's family tier. Matching reads the
employee rows; the Full EL reads both to rebuild the company's layout.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import Dependant, Employee
from app.models.employee_listing import (
    FAMILY_TIER_EXCLUDED_KEY,
    FAMILY_TIER_KEY,
    ListingAssignment,
)
from app.services.el_import.family_tiers import (
    employee_tier,
    tier_includes,
    tier_only_blocks,
    uses_tier,
)
from app.services.el_import.mapping import LabelSuggestion, normalize_label
from app.services.el_workbook import ElCover, ElEmployee, ElWorkbook
from app.services.el_workbook.models import ElDependant
from app.services.el_workbook.values import text
from app.services.roster_attributes import DOB_KEYS, first_value, iso_date
from app.services.roster_dedup import DEP_NAME_KEYS, employee_nric, staff_key

_DECISION_ROLES = {"category", "plan_type", "family_group", "admin_type"}


def _recorded(cover: ElCover) -> dict[str, Any]:
    return {
        k: (v.isoformat() if isinstance(v, date) else v)
        for k, v in cover.values.items()
        if k not in _DECISION_ROLES and v not in (None, "")
    }


def _listed_employee(
    listed: ElEmployee, by_staff: dict[str, Employee], by_nric: dict[str, Employee]
) -> Employee | None:
    staff_id = staff_key(listed.fields.get("staff_id"))
    employee = by_staff.get(staff_id) if staff_id else None
    if employee is None:
        nric = employee_nric({"id_no": listed.fields.get("national_id")})
        employee = by_nric.get(nric) if nric else None
    return employee


def _find_dependant(candidates: list[Dependant], fields: dict[str, Any]) -> Dependant | None:
    name = text(fields.get("name")).lower()
    dob = fields.get("date_of_birth")
    dob_text = dob.isoformat() if isinstance(dob, date) else ""
    for dep in candidates:
        attrs = dep.attribute_values or {}
        if text(first_value(attrs, DEP_NAME_KEYS)).lower() == name and (
            not dob_text or iso_date(first_value(attrs, DOB_KEYS)) == dob_text
        ):
            return dep
    return None


def _assignment(
    client_id: str, policy_year_id: str, employee_id: str, dependant_id: str | None,
    cover: ElCover, decision: LabelSuggestion, product_ids: dict[str, str], row: int,
) -> ListingAssignment:
    choice = decision.choice
    assert choice is not None
    return ListingAssignment(
        client_id=client_id,
        policy_year_id=policy_year_id,
        employee_id=employee_id,
        dependant_id=dependant_id,
        member_key=f"D:{dependant_id}" if dependant_id else f"E:{employee_id}",
        product_id=product_ids[choice.product_code],
        category_id=choice.category_id,
        block_index=cover.block,
        listed_category=text(cover.values.get("category"))[:512] or None,
        listed_plan=text(cover.values.get("plan_type"))[:255] or None,
        family_group=cover.values.get("family_group"),
        admin_type=cover.values.get("admin_type"),
        recorded=_recorded(cover),
        source_row=row,
    )


def _tier_assignment(
    client_id: str, policy_year_id: str, employee_id: str, dependant_id: str,
    block: int, tier: str, decision: LabelSuggestion | None, product_id: str, row: int,
) -> ListingAssignment:
    """A dependant's cover as the employee's tier states it: the employee's
    category when the tier includes them, no category when it excludes them.
    No listed wording, so the Full EL leaves the dependant's cells blank as the
    listing did."""
    included = decision is not None and decision.choice is not None
    recorded: dict[str, Any] = {FAMILY_TIER_KEY: tier}
    if not included:
        recorded[FAMILY_TIER_EXCLUDED_KEY] = True
    return ListingAssignment(
        client_id=client_id, policy_year_id=policy_year_id,
        employee_id=employee_id, dependant_id=dependant_id,
        member_key=f"D:{dependant_id}", product_id=product_id,
        category_id=decision.choice.category_id if decision and decision.choice else None,
        block_index=block, family_group=tier, recorded=recorded, source_row=row,
    )


class _Writer:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.written: set[tuple[str, str]] = set()

    def add(self, row: ListingAssignment) -> int:
        key = (row.member_key, row.product_id)
        if key in self.written:
            return 0
        self.written.add(key)
        self.db.add(row)
        return 1


def _dependants_by_employee(
    db: Session, client_id: str, policy_year_id: str
) -> dict[str, list[Dependant]]:
    out: dict[str, list[Dependant]] = {}
    for dep in db.execute(
        select(Dependant).where(Dependant.policy_year_id == policy_year_id,
                                Dependant.client_id == client_id)
    ).scalars():
        out.setdefault(dep.employee_id or "", []).append(dep)
    return out


def write_assignments(
    db: Session,
    client_id: str,
    policy_year_id: str,
    workbook: ElWorkbook,
    decisions: dict[tuple[int, str], LabelSuggestion],
    product_ids: dict[str, str],
    block_products: dict[int, list[str]],
) -> tuple[int, int]:
    """Replace the listed cover of everyone in the uploaded listing.

    People absent from the file keep the cover an earlier listing stated, as
    the roster keeps them. A block whose listed wording is mapped to "not
    covered", and every block of an employee listed with no cover at all, get a
    row per product with no category: the listing says
    they are not covered, so matching must not fall back to rules for them.
    A repeated row for the same person and product (a duplicate staff ID)
    keeps the first, as the movement planner does. Returns (employee covers,
    dependant covers) written.
    """
    employees = list(db.execute(
        select(Employee).where(Employee.policy_year_id == policy_year_id,
                               Employee.client_id == client_id)
    ).scalars())
    by_staff = {staff_key(e.staff_id): e for e in employees if e.staff_id}
    by_nric = {e.national_id_normalized: e for e in employees if e.national_id_normalized}
    dependants = _dependants_by_employee(db, client_id, policy_year_id)

    resolved = [
        (listed, employee) for listed in workbook.employees
        if (employee := _listed_employee(listed, by_staff, by_nric)) is not None
    ]
    listed_ids = {employee.id for _, employee in resolved}
    if listed_ids:
        # A dependant's row is keyed by the dependant, not the employee it was
        # listed under, so one re-linked since an earlier listing is cleared too.
        listed_dependant_ids = {d.id for e in listed_ids for d in dependants.get(e, [])}
        db.execute(delete(ListingAssignment).where(
            ListingAssignment.policy_year_id == policy_year_id,
            ListingAssignment.client_id == client_id,
            or_(
                ListingAssignment.employee_id.in_(listed_ids),
                ListingAssignment.dependant_id.in_(listed_dependant_ids),
            ),
        ))
    writer = _Writer(db)
    tier_only = tier_only_blocks(workbook)
    emp_count = dep_count = 0
    for listed, employee in resolved:
        own: dict[int, LabelSuggestion] = {}
        not_covered = [] if listed.covers else list(block_products)
        for cover in listed.covers:
            label = normalize_label(text(cover.values.get("category")))
            decision = decisions.get((cover.block, label))
            if decision is not None and decision.not_covered:
                not_covered.append(cover.block)
                continue
            if decision is None or decision.choice is None:
                continue
            own[cover.block] = decision
            emp_count += writer.add(_assignment(
                client_id, policy_year_id, employee.id, None, cover, decision,
                product_ids, listed.row,
            ))
        for block in not_covered:
            for code in block_products.get(block, []):
                writer.add(ListingAssignment(
                    client_id=client_id, policy_year_id=policy_year_id,
                    employee_id=employee.id, member_key=f"E:{employee.id}",
                    product_id=product_ids[code], category_id=None,
                    block_index=block, recorded={}, source_row=listed.row,
                ))
        candidates = dependants.get(employee.id, [])
        for listed_dep in listed.dependants:
            dependant = _find_dependant(candidates, listed_dep.fields)
            if dependant is None:
                continue
            dep_count += _write_dependant(
                writer, client_id, policy_year_id, employee.id, dependant.id, listed,
                listed_dep, own, decisions, product_ids, tier_only,
            )
    db.flush()
    return emp_count, dep_count


def _write_dependant(
    writer: _Writer, client_id: str, policy_year_id: str, employee_id: str,
    dependant_id: str, listed: ElEmployee, listed_dep: ElDependant,
    own: dict[int, LabelSuggestion], decisions: dict[tuple[int, str], LabelSuggestion],
    product_ids: dict[str, str], tier_only: frozenset[int],
) -> int:
    written = 0
    for cover in listed_dep.covers:
        key = normalize_label(text(cover.values.get("category")))
        decision = decisions.get((cover.block, key)) if key else own.get(cover.block)
        if decision is None or decision.choice is None:
            continue
        written += writer.add(_assignment(
            client_id, policy_year_id, employee_id, dependant_id, cover, decision,
            product_ids, listed_dep.row,
        ))
    for block in tier_only:
        if not uses_tier(listed, block, tier_only):
            continue
        decision = own.get(block)
        tier = employee_tier(listed, block)
        if decision is None or decision.choice is None or tier is None:
            continue
        included = tier_includes(tier, listed_dep.fields.get("relationship"))
        if included is None:
            continue
        added = writer.add(_tier_assignment(
            client_id, policy_year_id, employee_id, dependant_id, block, tier,
            decision if included else None, product_ids[decision.choice.product_code],
            listed_dep.row,
        ))
        if included:
            written += added
    return written
