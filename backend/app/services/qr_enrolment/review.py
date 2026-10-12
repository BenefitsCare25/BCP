"""Broker review of QR submissions: list, inspect, match to the roster, file or reject.

A QR submission names who the visitor CLAIMS to be. The broker confirms the
person with HR, then links the submission to that roster employee, which files
it into the e-form register exactly like a scanned paper form: the same PDF,
the same reference, no elections applied. Elections and family changes are
then entered and confirmed through the existing enrolment workflow.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Employee,
    EnrollmentFormSubmission,
    EnrollmentWindow,
    PublicIntake,
)
from app.models.enrollment_form import FORM_STATUS_SUPERSEDED
from app.models.qr_enrolment import INTAKE_LINKED, INTAKE_PENDING, INTAKE_REJECTED
from app.schemas.qr_enrolment import (
    IntakeCandidateOut,
    IntakeDetailOut,
    IntakeItemOut,
    IntakeListOut,
)
from app.services.enrollment_forms.submission import record_qr_form
from app.services.qr_enrolment.batches import aware
from app.services.qr_enrolment.intake import intake_pdf
from app.services.roster_attributes import mask_nric

_REJECT_LABEL = {
    "duplicate": "Duplicate submission",
    "not_employee": "Not an employee of this company",
    "wrong_company": "Wrong company",
    "withdrawn": "Withdrawn by the employee",
    "other": "Other",
}
CANDIDATE_LIMIT = 10


def _request_line(sel: dict[str, Any]) -> str:
    name = sel.get("product_name") or sel.get("product_code")
    action = sel.get("action")
    if action == "no_change":
        return f"{name}: no change"
    if action == "withdraw":
        who = list(sel.get("withdrawn") or [])
        if sel.get("declined"):
            who.insert(0, "own cover")
        return f"{name}: withdraw {', '.join(who)}"
    covered = sel.get("covered") or []
    family = f" + {len(covered)} family" if covered else ""
    return f"{name}: {sel.get('elected_plan')}{family}"


def _item(intake: PublicIntake, windows: dict[str, str], names: dict[str, str]) -> IntakeItemOut:
    snap = intake.snapshot or {}
    selections = snap.get("selections") or []
    category = next((s.get("category") for s in selections if s.get("category")), None)
    return IntakeItemOut(
        id=intake.id,
        reference_no=intake.reference_no,
        status=intake.status,  # type: ignore[arg-type]
        window_id=intake.window_id,
        window_name=windows.get(intake.window_id or ""),
        submitted_at=intake.created_at,
        claimed_staff_id=intake.claimed_staff_id,
        claimed_name=intake.claimed_name,
        id_masked=mask_nric(intake.claimed_national_id) if intake.claimed_national_id else None,
        category_label=category,
        requests=[_request_line(s) for s in selections if s.get("action") != "no_change"],
        dependants=len(snap.get("dependants") or []),
        linked_employee_id=intake.linked_employee_id,
        linked_employee_name=names.get(intake.linked_employee_id or ""),
        linked_submission_id=intake.linked_submission_id,
        rejected_reason=intake.rejected_reason,
    )


def _labels(db: Session, intakes: list[PublicIntake]) -> tuple[dict[str, str], dict[str, str]]:
    window_ids = {i.window_id for i in intakes if i.window_id}
    employee_ids = {i.linked_employee_id for i in intakes if i.linked_employee_id}
    windows = {
        w.id: w.name
        for w in db.scalars(select(EnrollmentWindow).where(EnrollmentWindow.id.in_(window_ids)))
    } if window_ids else {}
    names = {
        e.id: e.employee_name or e.staff_id
        for e in db.scalars(select(Employee).where(Employee.id.in_(employee_ids)))
    } if employee_ids else {}
    return windows, names


def list_intakes(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str,
    window_id: str | None,
    status_: str | None,
    query: str | None,
    offset: int,
    limit: int,
) -> IntakeListOut:
    conds = [PublicIntake.client_id == client_id, PublicIntake.policy_year_id == policy_year_id]
    if window_id:
        conds.append(PublicIntake.window_id == window_id)
    counts = {
        str(state): int(n)
        for state, n in db.execute(
            select(PublicIntake.status, func.count(PublicIntake.id))
            .where(*conds).group_by(PublicIntake.status)
        ).all()
    }
    if status_:
        conds.append(PublicIntake.status == status_)
    term = (query or "").strip().lower()
    if term:
        like = f"%{term}%"
        conds.append(or_(
            func.lower(PublicIntake.claimed_name).like(like),
            func.lower(PublicIntake.claimed_staff_id).like(like),
            func.lower(PublicIntake.reference_no).like(like),
        ))
    total = db.execute(select(func.count(PublicIntake.id)).where(*conds)).scalar_one()
    rows = list(db.scalars(
        select(PublicIntake).where(*conds)
        .order_by(PublicIntake.created_at.desc()).offset(offset).limit(limit)
    ))
    windows, names = _labels(db, rows)
    return IntakeListOut(
        items=[_item(r, windows, names) for r in rows],
        total=int(total), offset=offset, limit=limit, counts=counts,
    )


def find_intake(db: Session, client_id: str, intake_id: str, *, lock: bool = False) -> PublicIntake:
    query = select(PublicIntake).where(
        PublicIntake.id == intake_id, PublicIntake.client_id == client_id
    )
    if lock:
        query = query.with_for_update()
    intake = db.execute(query).scalar_one_or_none()
    if intake is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "QR submission not found")
    return intake


def _category_names(db: Session, employees: list[Employee]) -> dict[str, str]:
    ids = {e.matched_category_id for e in employees if e.matched_category_id}
    return {
        c.id: c.display_name
        for c in db.scalars(select(Category).where(Category.id.in_(ids)))
    } if ids else {}


def candidates(db: Session, intake: PublicIntake, query: str | None) -> list[IntakeCandidateOut]:
    base = [
        Employee.client_id == intake.client_id,
        Employee.policy_year_id == intake.policy_year_id,
    ]
    staff = (intake.claimed_staff_id or "").strip().lower()
    name = (intake.claimed_name or "").strip().lower()
    nric = intake.claimed_national_id
    term = (query or "").strip().lower()
    if term:
        like = f"%{term}%"
        match = or_(
            func.lower(Employee.staff_id).like(like),
            func.lower(Employee.employee_name).like(like),
        )
    else:
        options = [func.lower(Employee.staff_id) == staff]
        if nric:
            options.append(Employee.national_id_normalized == nric)
        if name:
            options.append(func.lower(Employee.employee_name) == name)
        match = or_(*options)
    rows = list(db.scalars(select(Employee).where(*base, match).limit(CANDIDATE_LIMIT * 3)))
    cats = _category_names(db, rows)
    forms = _latest_forms(db, intake.window_id, [e.id for e in rows])
    out: list[IntakeCandidateOut] = []
    for e in rows:
        hits = []
        if staff and (e.staff_id or "").strip().lower() == staff:
            hits.append("Staff ID")
        if nric and e.national_id_normalized == nric:
            hits.append("NRIC / FIN")
        if name and (e.employee_name or "").strip().lower() == name:
            hits.append("Name")
        out.append(IntakeCandidateOut(
            employee_id=e.id,
            staff_id=e.staff_id,
            employee_name=e.employee_name,
            id_masked=mask_nric(e.national_id_normalized) if e.national_id_normalized else None,
            category=cats.get(e.matched_category_id or ""),
            status=e.status,
            match=hits,
            existing_form=forms.get(e.id),
        ))
    out.sort(key=lambda c: (-len(c.match), c.staff_id))
    return out[:CANDIDATE_LIMIT]


def _current_forms(
    db: Session, window_id: str | None, employee_ids: list[str]
) -> dict[str, EnrollmentFormSubmission]:
    """Each employee's current (not superseded) form in the period — the
    period-less ones when the intake's period was deleted, exactly the set
    filing would supersede."""
    if not employee_ids:
        return {}
    sub = EnrollmentFormSubmission
    return {
        row.employee_id: row
        for row in db.scalars(
            select(sub).where(
                sub.window_id.is_(None) if window_id is None else sub.window_id == window_id,
                sub.employee_id.in_(employee_ids),
                sub.status != FORM_STATUS_SUPERSEDED,
            ).order_by(sub.version)
        )
    }


def _latest_forms(db: Session, window_id: str | None, employee_ids: list[str]) -> dict[str, str]:
    return {e: f.reference_no for e, f in _current_forms(db, window_id, employee_ids).items()}


def detail(db: Session, intake: PublicIntake) -> IntakeDetailOut:
    windows, names = _labels(db, [intake])
    snap = intake.snapshot or {}
    return IntakeDetailOut(
        item=_item(intake, windows, names),
        particulars=dict(snap.get("particulars") or {}),
        selections=list(snap.get("selections") or []),
        family=list(snap.get("dependants") or []),
        life_event=snap.get("life_event"),
        candidates=candidates(db, intake, None) if intake.status == INTAKE_PENDING else [],
    )


def _require_pending(intake: PublicIntake) -> None:
    if intake.status != INTAKE_PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "intake_not_pending",
            "message": f"This submission is already {intake.status}.",
        })


def link_intake(
    db: Session,
    intake: PublicIntake,
    employee_id: str,
    user_id: str,
    note: str | None,
    *,
    confirm_supersede: bool = False,
) -> EnrollmentFormSubmission:
    _require_pending(intake)
    employee = db.get(Employee, employee_id)
    if (
        employee is None or employee.client_id != intake.client_id
        or employee.policy_year_id != intake.policy_year_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found in this benefit year")
    existing = _current_forms(db, intake.window_id, [employee.id]).get(employee.id)
    submitted = aware(intake.created_at)
    existing_at = aware(existing.signed_at or existing.created_at) if existing else None
    if existing is not None and submitted and existing_at and existing_at > submitted:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "newer_form",
            "message": f"This employee sent form {existing.reference_no} after this QR form, "
            "so it stays current. Reject this QR submission, or ask HR which one is right.",
            "reference_no": existing.reference_no,
        })
    current = existing.reference_no if existing is not None else None
    if current and not confirm_supersede:
        # Anyone holding the shared QR can claim a colleague's name and staff
        # ID; filing would mark the colleague's own form superseded.
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "existing_form",
            "message": f"This employee already has form {current} for this period. Filing "
            "this one marks it superseded; confirm with HR first.",
            "reference_no": current,
        })
    found = intake_pdf(db, intake)
    if found is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The submitted form file is not available.")
    window = db.get(EnrollmentWindow, intake.window_id) if intake.window_id else None
    sub = record_qr_form(
        db,
        employee=employee,
        window=window,
        reference_no=intake.reference_no,
        snapshot=dict(intake.snapshot),
        sha256=intake.content_sha256,
        pdf=found[0],
        signed_at=intake.created_at,
        signer_ip=intake.signer_ip,
        signer_user_agent=intake.signer_user_agent,
        user_id=user_id,
        note=(note or "").strip() or None,
    )
    intake.status = INTAKE_LINKED
    intake.linked_employee_id = employee.id
    intake.linked_submission_id = sub.id
    intake.linked_by = user_id
    intake.linked_at = datetime.now(UTC)
    return sub


def reject_intake(
    intake: PublicIntake, reason: str, note: str | None, user_id: str
) -> None:
    _require_pending(intake)
    label = _REJECT_LABEL.get(reason, reason)
    detail_note = (note or "").strip()
    intake.status = INTAKE_REJECTED
    intake.rejected_reason = f"{label}: {detail_note}" if detail_note else label
    intake.rejected_by = user_id
    intake.rejected_at = datetime.now(UTC)


def restore_intake(intake: PublicIntake) -> None:
    """Undo a rejection made in error; the submission is pending again."""
    if intake.status != INTAKE_REJECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a rejected submission can be restored.")
    intake.status = INTAKE_PENDING
    intake.rejected_reason = intake.rejected_by = intake.rejected_at = None



