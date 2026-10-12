"""Excel records for QR submissions, appended to the e-form register export.

* **QR submissions** — one row per submission with its review status, the
  identity it claims, the category and every request, the employee's annual
  share and who it was matched to.
* **Dependants enrolment** — laid out like the insurer renewal listing: per
  submission and benefit, an employee row carrying the category, plan, family
  group (EO/ES/EC/EF), annual premium and premium to be deducted, then one row
  per family member with the listing's A (addition) / D (deletion) /
  C (change) action.

Pending submissions are listed with their status: they are claims of identity
until a broker matches them, never confirmed cover. Identification numbers
follow the register's masking.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from openpyxl import Workbook
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Employee, EnrollmentWindow, PolicyYear, PublicIntake
from app.models.qr_enrolment import INTAKE_REJECTED
from app.services.insurer_reports import autosize, bold_header, naive
from app.services.roster_attributes import age_next_birthday_as_of, mask_nric
from app.services.xlsx_safe import append_safe

_SUBMISSIONS_HEADER = [
    "Reference", "Status", "Benefit period", "Submitted", "Staff ID (as entered)",
    "Name (as entered)", "NRIC / FIN", "Category", "Requests", "Family members named",
    "Employee share (annual)", "Contact number", "Email", "Matched to staff ID",
    "Rejected reason",
]
_LISTING_HEADER = [
    "A - Addition / D - Deletion / C - Changes", "Reference", "Status", "Staff ID",
    "Employee name", "Relationship", "Name", "Sex (F/M)", "NRIC / BC / FIN", "Nationality",
    "Country of residence", "Date of birth", "Age next birthday", "Benefit", "Category",
    "Plan", "Family group", "Annual premium", "Premium to be deducted",
]
_ACTION_CODE = {"add": "A", "withdraw": "D", "update": "C"}
_STATUS = {"pending": "Pending verification", "linked": "Matched", "rejected": "Rejected"}


def _intakes(db: Session, client_id: str, policy_year_id: str | None,
             window_id: str | None, query: str | None,
             include_rejected: bool) -> list[PublicIntake]:
    conds = [PublicIntake.client_id == client_id]
    if not include_rejected:
        conds.append(PublicIntake.status != INTAKE_REJECTED)
    if policy_year_id:
        conds.append(PublicIntake.policy_year_id == policy_year_id)
    if window_id:
        conds.append(PublicIntake.window_id == window_id)
    term = (query or "").strip().lower()
    if term:
        like = f"%{term}%"
        conds.append(or_(
            PublicIntake.claimed_name.ilike(like),
            PublicIntake.claimed_staff_id.ilike(like),
            PublicIntake.reference_no.ilike(like),
        ))
    return list(db.scalars(
        select(PublicIntake).where(*conds).order_by(PublicIntake.reference_no)
    ))


def _request(sel: dict[str, Any]) -> str:
    name = sel.get("product_name") or sel.get("product_code")
    if sel.get("action") == "withdraw":
        who = list(sel.get("withdrawn") or [])
        if sel.get("declined"):
            who.insert(0, "own cover")
        return f"{name}: withdraw {', '.join(who)}"
    covered = ", ".join(sel.get("covered") or [])
    return f"{name}: {sel.get('elected_plan')}" + (f" + {covered}" if covered else "")


def _id(value: object, masked: bool) -> str:
    text = str(value or "")
    return mask_nric(text) if masked and text else text


def _share_total(selections: list[dict[str, Any]]) -> float | None:
    values = [float(v) for s in selections if isinstance(v := s.get("you_pay"), (int, float))]
    return round(sum(values), 2) if values else None


def append_qr_sheets(
    wb: Workbook,
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    window_id: str | None,
    query: str | None,
    masked: bool,
    include_rejected: bool = True,
) -> int:
    """Rejected submissions (often not this company's staff at all) stay out
    of exports that leave the broker, such as HR's."""
    intakes = _intakes(db, client_id, policy_year_id, window_id, query, include_rejected)
    if not intakes:
        return 0
    windows = {
        w.id: w.name for w in db.scalars(select(EnrollmentWindow).where(
            EnrollmentWindow.id.in_({i.window_id for i in intakes if i.window_id})
        ))
    }
    years = {
        y.id: y.start_date for y in db.scalars(select(PolicyYear).where(
            PolicyYear.id.in_({i.policy_year_id for i in intakes})
        ))
    }
    linked = {
        e.id: e.staff_id for e in db.scalars(select(Employee).where(
            Employee.id.in_({i.linked_employee_id for i in intakes if i.linked_employee_id})
        ))
    }
    summary = wb.create_sheet("QR submissions")
    append_safe(summary, _SUBMISSIONS_HEADER)
    listing = wb.create_sheet("Dependants enrolment")
    append_safe(listing, _LISTING_HEADER)
    for intake in intakes:
        snap = intake.snapshot or {}
        p = snap.get("particulars") or {}
        selections = snap.get("selections") or []
        category = next((s.get("category") for s in selections if s.get("category")), "")
        append_safe(summary, [
            intake.reference_no, _STATUS.get(intake.status, intake.status),
            windows.get(intake.window_id or "", ""), naive(intake.created_at),
            p.get("staff_id") or "", p.get("name") or "", _id(p.get("id_no"), masked),
            category,
            "; ".join(_request(s) for s in selections if s.get("action") != "no_change"),
            len(snap.get("dependants") or []), _share_total(selections),
            p.get("contact_no") or "", p.get("email") or "",
            linked.get(intake.linked_employee_id or "", ""), intake.rejected_reason or "",
        ])
        _listing_rows(listing, intake, snap, years.get(intake.policy_year_id), masked)
    for ws in (summary, listing):
        bold_header(ws)
        autosize(ws)
    return len(intakes)


def _listing_rows(
    ws: Any, intake: PublicIntake, snap: dict[str, Any], ref: date | None, masked: bool
) -> None:
    p = snap.get("particulars") or {}
    deps = {d.get("id"): d for d in snap.get("dependants") or []}
    status_ = _STATUS.get(intake.status, intake.status)
    base = [intake.reference_no, status_, p.get("staff_id") or "", p.get("name") or ""]

    def person(d: dict[str, Any] | None) -> list[object]:
        if d is None:
            sex = (p.get("gender") or "")[:1]
            return ["Employee", p.get("name") or "", sex, _id(p.get("id_no"), masked),
                    "", "", p.get("dob") or "", _anb(p.get("dob"), ref)]
        return [d.get("relationship") or "", d.get("name") or "", (d.get("gender") or "")[:1],
                _id(d.get("id_no"), masked), d.get("nationality") or "",
                d.get("residence") or "", d.get("dob") or "", _anb(d.get("dob"), ref)]

    for sel in snap.get("selections") or []:
        action = sel.get("action")
        if action == "no_change":
            continue
        benefit = [sel.get("product_name") or sel.get("product_code"), sel.get("category") or ""]
        if action == "enrol":
            own_code = "" if sel.get("compulsory") else "A"
            append_safe(ws, [own_code, *base, *person(None), *benefit, sel.get("elected_plan"),
                             sel.get("composition") or "", sel.get("full_premium"),
                             sel.get("you_pay")])
            for key in sel.get("covered_keys") or []:
                append_safe(ws, ["A", *base, *person(deps.get(key)), *benefit,
                                 sel.get("elected_plan"), "", None, None])
        else:
            if sel.get("declined"):
                append_safe(ws, ["D", *base, *person(None), *benefit, "", "", None, None])
            for key in sel.get("withdrawn_keys") or []:
                append_safe(ws, ["D", *base, *person(deps.get(key)), *benefit, "", "", None, None])
    for d in deps.values():
        if d.get("action") == "update":
            append_safe(ws, ["C", *base, *person(d), "", "", "", "", None, None])


def _anb(dob: object, ref: date | None) -> int | None:
    if not dob or ref is None:
        return None
    try:
        return age_next_birthday_as_of(date.fromisoformat(str(dob)), ref)
    except ValueError:
        return None
