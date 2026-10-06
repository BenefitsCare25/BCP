"""Sign-and-submit: validate the e-form, freeze it as a snapshot, version it,
render the PDF and store it. Also files scanned paper forms.

Elections go through the SAME ``apply_elections`` / ``apply_leave`` /
``perform_submit`` core as every other enrolment write, so the e-form can never
accept a choice the enrolment rules refuse. What the form adds — declarations,
cross-product rules, per-product family eligibility, requests for family
members awaiting verification — is checked in ``selections.py`` against the one
resolved view of what is being signed.

Versions and reference numbers are allocated under a lock on the employee row,
so a double-click or two brokers filing at once can't produce two "latest"
versions; references come from the highest number already issued, never a row
count (rows are cascade-deleted with their employee).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.storage import document_path, get_storage
from app.models import (
    Client,
    Dependant,
    Employee,
    Enrollment,
    EnrollmentWindow,
    LeaveElection,
    PolicyYear,
)
from app.models.enrollment import EnrollmentElection
from app.models.enrollment_form import (
    FORM_SOURCE_PAPER,
    FORM_SOURCE_PORTAL,
    FORM_STATUS_SUBMITTED,
    FORM_STATUS_SUPERSEDED,
    EnrollmentFormSubmission,
)
from app.models.stored_document import DOC_ENTITY_ENROL_FORM, StoredDocument
from app.schemas.enrollment import EnrollmentElectionIn, LeaveElectionIn
from app.schemas.enrollment_forms import FormClause, FormSignIn, MemberFormContextOut
from app.services.claims import attach_document, track_pending_blob
from app.services.enrollment_elections import apply_elections, apply_leave, perform_submit
from app.services.enrollment_forms.context import build_form_parts, employee_full_id
from app.services.enrollment_forms.pdf import render_form_pdf
from app.services.enrollment_forms.selections import (
    Selection,
    check_eligibility,
    check_pending,
    check_rules,
    names_family,
    resolve_selections,
    selection_rows,
    unprocessable,
)
from app.services.roster_attributes import DEPENDANT_ID_KEYS, first_value

_REF_ATTEMPTS = 5
_REF_SEQ = re.compile(r"-(\d+)$")


@dataclass(frozen=True)
class SignatureMeta:
    ip: str | None
    user_agent: str | None
    member_account_id: str


def required_clause_ids(clauses: list[FormClause], family: bool) -> set[str]:
    return {c.id for c in clauses if c.applies_to == "all" or family}


def _elections_by_code(db: Session, enrollment: Enrollment) -> dict[str, EnrollmentElection]:
    rows = db.execute(
        select(EnrollmentElection).where(EnrollmentElection.enrollment_id == enrollment.id)
    ).scalars().all()
    return {row.product_code: row for row in rows}


# -- Snapshot -----------------------------------------------------------------


def _dependant_rows(
    db: Session,
    ctx: MemberFormContextOut,
    selections: list[Selection],
    pending: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    covered_on: dict[str, list[str]] = {}
    for sel in selections:
        for dep_id in sel.covered_ids:
            covered_on.setdefault(dep_id, []).append(sel.code)
    for req in pending:
        covered_on.setdefault(req["dependant_id"], []).extend(req["product_codes"])
    ids = [d.id for d in ctx.dependants if d.id in covered_on]
    full_ids = {
        dep.id: first_value(dep.attribute_values or {}, DEPENDANT_ID_KEYS)
        for dep in db.execute(select(Dependant).where(Dependant.id.in_(ids))).scalars()
    } if ids else {}
    return [
        {
            "id": d.id,
            "relationship": d.relationship,
            "name": d.name,
            "gender": d.gender,
            "id_no": full_ids.get(d.id) or d.id_masked,
            "occupation": d.occupation,
            "dob": d.dob,
            "status": d.status,
            "covered_on": sorted(set(covered_on[d.id])),
        }
        for d in ctx.dependants
        if d.id in covered_on
    ]


def _changed(entered: str | None, on_record: str | None) -> bool:
    return bool(entered) and entered != on_record


def enrollment_leave(db: Session, enrollment: Enrollment) -> dict[str, Any] | None:
    row = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enrollment.id)
    ).scalar_one_or_none()
    if row is None or row.action not in ("buy", "sell"):
        return None
    return {"action": row.action, "days": row.days, "flex_amount": row.flex_amount}


def flex_summary(
    ctx: MemberFormContextOut, rows: list[dict[str, Any]], leave: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The wallet ledger the member saw on the review step, frozen."""
    if ctx.flex_wallet is None:
        return None
    used = round(sum(r["price_tag"] or 0.0 for r in rows), 2)
    leave_amount = float((leave or {}).get("flex_amount") or 0.0)
    return {
        "currency": ctx.flex_currency,
        "allowance": ctx.flex_wallet,
        "proration_note": ctx.flex_proration_note,
        "price_tags_used": used,
        "leave_amount": round(leave_amount, 2),
        "balance": round(ctx.flex_wallet - used + leave_amount, 2),
        "unpriced": [
            r["product_code"] for r in rows if r["price_tag"] is None and not r["declined"]
        ],
    }


def gst_basis(ctx: MemberFormContextOut) -> str:
    """State the GST basis of the printed premiums — every paper form did. The
    figures carry GST only where the product's terms say so."""
    with_gst = [c.product_code for c in ctx.contributions if c.gst_included]
    if with_gst and len(with_gst) == len(ctx.contributions):
        return ", inclusive of GST"
    if not with_gst:
        return ", before GST (GST is added where it applies)"
    return f", inclusive of GST for {', '.join(with_gst)} and before GST for the others"


def build_snapshot(
    db: Session,
    *,
    enrollment: Enrollment,
    employee: Employee,
    ctx: MemberFormContextOut,
    selections: list[Selection],
    body: FormSignIn,
    pending: list[dict[str, Any]],
    family: bool,
    reference_no: str,
    version: int,
    signed_at: datetime,
) -> dict[str, Any]:
    p = ctx.particulars
    accepted = set(body.accepted_clause_ids)
    rows = selection_rows(selections, ctx)
    leave = enrollment_leave(db, enrollment)
    return {
        "schema": 2,
        "form": {
            "company_name": ctx.company_name,
            "title": ctx.title,
            "policy_start": ctx.policy_start.isoformat(),
            "policy_end": ctx.policy_end.isoformat(),
            "closes_at": ctx.closes_at.isoformat(),
            "reference_no": reference_no,
            "version": version,
            "submitted_at": signed_at.isoformat(),
            "submission_note": ctx.submission_note,
            "helpline": ctx.helpline,
        },
        "particulars": {
            "name": p.name,
            "id_no": employee_full_id(employee) or p.id_masked,
            "staff_id": p.staff_id,
            "gender": p.gender,
            "dob": p.dob,
            "job_grade": p.job_grade,
            "date_of_hire": p.date_of_hire,
            "contact_no": body.particulars.contact_no or p.contact_no,
            "email": body.particulars.email or p.email,
            "contact_updated": _changed(body.particulars.contact_no, p.contact_no),
            "email_updated": _changed(body.particulars.email, p.email),
        },
        "intro_lines": ctx.intro_lines,
        "selections": rows,
        "leave": leave,
        "flex": flex_summary(ctx, rows, leave),
        "premium_note": (
            f"Annual premiums{gst_basis(ctx)}. \"You pay\" is your share; it is "
            "pro-rated if cover starts part-way through the policy year."
            if ctx.contributions else None
        ),
        "dependants": _dependant_rows(db, ctx, selections, pending),
        "pending_requests": pending,
        "eligibility_notes": ctx.eligibility_notes,
        "documents": [{"id": d.id, "label": d.label} for d in ctx.documents],
        "declarations": [
            {"id": c.id, "text": c.text, "accepted": c.id in accepted}
            for c in ctx.clauses
            if c.applies_to == "all" or family
        ],
        "signature": {"name": body.signature_name, "signed_at": signed_at.isoformat()},
    }


def count_changes(snapshot: dict[str, Any]) -> int:
    changes = sum(
        1 for s in snapshot.get("selections", [])
        if s.get("action") not in (None, "keep") or s.get("withdrawn")
    )
    changes += len(snapshot.get("pending_requests") or [])
    return changes + (1 if snapshot.get("leave") else 0)


def content_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# -- Persistence ---------------------------------------------------------------


def _lock_employee(db: Session, employee_id: str) -> None:
    """Serialise form filing per member (no-op on SQLite)."""
    db.execute(select(Employee.id).where(Employee.id == employee_id).with_for_update()).first()


def _next_version(db: Session, employee_id: str, window_id: str | None) -> int:
    current = db.execute(
        select(func.max(EnrollmentFormSubmission.version)).where(
            EnrollmentFormSubmission.employee_id == employee_id,
            EnrollmentFormSubmission.window_id.is_(None)
            if window_id is None
            else EnrollmentFormSubmission.window_id == window_id,
        )
    ).scalar_one()
    return int(current or 0) + 1


def _reference(db: Session, client_id: str, year: int) -> str:
    """Next reference after the HIGHEST issued for the client and year. Ordered
    by length then value so EF-2026-100000 sorts after EF-2026-99999."""
    prefix = f"EF-{year}-"
    last = db.execute(
        select(EnrollmentFormSubmission.reference_no)
        .where(
            EnrollmentFormSubmission.client_id == client_id,
            EnrollmentFormSubmission.reference_no.like(f"{prefix}%"),
        )
        .order_by(
            func.length(EnrollmentFormSubmission.reference_no).desc(),
            EnrollmentFormSubmission.reference_no.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    match = _REF_SEQ.search(last or "")
    return f"{prefix}{(int(match.group(1)) if match else 0) + 1:05d}"


def _insert_with_reference(
    db: Session, sub: EnrollmentFormSubmission, year: int
) -> EnrollmentFormSubmission:
    """A concurrent submit (another member of the same client) can take the same
    number; the unique key refuses it and the next attempt re-reads the max."""
    for _attempt in range(_REF_ATTEMPTS):
        sub.reference_no = _reference(db, sub.client_id, year)
        try:
            with db.begin_nested():
                db.add(sub)
                db.flush()
            return sub
        except IntegrityError:
            if sub in db:
                db.expunge(sub)
    raise HTTPException(status.HTTP_409_CONFLICT, "Please try submitting again.")


def _supersede_previous(db: Session, sub: EnrollmentFormSubmission) -> None:
    previous = db.execute(
        select(EnrollmentFormSubmission).where(
            EnrollmentFormSubmission.employee_id == sub.employee_id,
            EnrollmentFormSubmission.window_id.is_(None)
            if sub.window_id is None
            else EnrollmentFormSubmission.window_id == sub.window_id,
            EnrollmentFormSubmission.id != sub.id,
            EnrollmentFormSubmission.status != FORM_STATUS_SUPERSEDED,
        )
    ).scalars().all()
    for row in previous:
        row.status = FORM_STATUS_SUPERSEDED
        row.superseded_by_id = sub.id


def store_pdf(
    db: Session,
    *,
    client: Client,
    sub: EnrollmentFormSubmission,
    content: bytes,
    file_name: str,
    uploaded_by_member_id: str | None = None,
) -> StoredDocument:
    from app.db.base import new_uuid

    doc_id = new_uuid()
    path = document_path(
        client.broker_firm_id, client.id, DOC_ENTITY_ENROL_FORM, sub.id, doc_id, ".pdf"
    )
    blob = get_storage().save(io.BytesIO(content), path)
    track_pending_blob(db, blob.path)
    doc = StoredDocument(
        id=doc_id,
        client_id=client.id,
        entity_type=DOC_ENTITY_ENROL_FORM,
        entity_id=sub.id,
        file_name=file_name,
        mime_type="application/pdf",
        size_bytes=blob.size_bytes,
        sha256=blob.sha256,
        storage_path=blob.path,
        uploaded_by_member_id=uploaded_by_member_id,
    )
    db.add(doc)
    sub.document_id = doc.id
    return doc


def pdf_file_name(reference_no: str, employee: Employee) -> str:
    return f"Enrolment form {reference_no} {employee.staff_id}.pdf".replace("/", "-")


def _validate(
    db: Session,
    *,
    window: EnrollmentWindow,
    enrollment: Enrollment,
    body: FormSignIn,
    ctx: MemberFormContextOut,
    products: list[Any],
) -> tuple[list[Selection], list[dict[str, Any]], bool]:
    baseline = (enrollment.baseline_snapshot or {}).get("products") or {}
    selections = resolve_selections(products, _elections_by_code(db, enrollment), ctx, baseline)
    check_eligibility(selections, ctx)
    check_rules(selections, ctx)
    pending = check_pending(
        body.pending_requests, selections, ctx, window.allow_dependant_changes
    )
    family = names_family(selections, body.pending_requests)
    if required_clause_ids(ctx.clauses, family) - set(body.accepted_clause_ids):
        raise unprocessable("Tick every declaration before signing.")
    return selections, pending, family


def _reprice_saved_elections(db: Session, enrollment: Enrollment) -> None:
    """Re-apply stored choices so their flex price tags are priced NOW. A form
    signed without fresh choices must not freeze a tag priced before the member
    had a wallet, or under rates since changed (``revalidate_enrollment`` keeps
    old tags on purpose — it serves confirm/close, not signing)."""
    saved = list(_elections_by_code(db, enrollment).values())
    if not saved:
        return
    apply_elections(
        db,
        enrollment,
        [
            EnrollmentElectionIn(
                product_code=e.product_code,
                plan_code=e.elected_plan_code,
                tier_category_id=e.tier_category_id,
                declined=e.elected_plan_code is None,
                covered_dependant_ids=e.covered_dependant_ids,
                dependant_option_ids=e.dependant_option_ids,
                notes=e.notes,
            )
            for e in saved
        ],
    )


def _reprice_saved_leave(db: Session, enrollment: Enrollment) -> None:
    row = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enrollment.id)
    ).scalar_one_or_none()
    if row is not None and row.action in ("buy", "sell"):
        action: Literal["buy", "sell"] = "buy" if row.action == "buy" else "sell"
        apply_leave(db, enrollment, LeaveElectionIn(action=action, days=row.days))


def sign_and_submit(
    db: Session,
    *,
    employee: Employee,
    window: EnrollmentWindow,
    enrollment: Enrollment,
    body: FormSignIn,
    meta: SignatureMeta,
) -> EnrollmentFormSubmission:
    """Apply the member's choices, validate the form, submit the enrolment,
    and record the signed submission + PDF. Flushes, does not commit."""
    if body.request_id:
        previous = db.scalars(select(EnrollmentFormSubmission).where(
            EnrollmentFormSubmission.enrollment_id == enrollment.id,
        )).all()
        for prior in previous:
            if prior.snapshot.get("request_id") == body.request_id:
                if prior.snapshot.get("request_hash") != content_hash(body.model_dump(mode="json")):
                    raise HTTPException(status.HTTP_409_CONFLICT, "Submission request has changed.")
                if prior.status not in ("submitted", "acknowledged"):
                    raise HTTPException(status.HTTP_409_CONFLICT,
                        "This submission is no longer current. Refresh and review your enrolment.")
                return prior
    if "expected_event_id" in body.model_fields_set:
        from app.services.enrollment_elections import enrollment_detail
        if enrollment_detail(db, enrollment).latest_event_id != body.expected_event_id:
            raise HTTPException(status.HTTP_409_CONFLICT,
                "Your enrolment changed while this page was open. "
                "Refresh and review it before signing.")
    if body.elections is not None:
        apply_elections(db, enrollment, body.elections)
    else:
        _reprice_saved_elections(db, enrollment)
    if body.leave is not None:
        apply_leave(db, enrollment, body.leave)
    else:
        _reprice_saved_leave(db, enrollment)

    ctx, products = build_form_parts(db, employee, window, enrollment)
    selections, pending, family = _validate(
        db, window=window, enrollment=enrollment, body=body, ctx=ctx, products=products
    )
    perform_submit(db, enrollment, acknowledge=False, actor_id=meta.member_account_id)

    year = db.get(PolicyYear, window.policy_year_id)
    client = db.get(Client, window.client_id)
    assert year is not None and client is not None
    _lock_employee(db, employee.id)
    signed_at = datetime.now(UTC)
    sub = EnrollmentFormSubmission(
        client_id=window.client_id,
        policy_year_id=window.policy_year_id,
        window_id=window.id,
        enrollment_id=enrollment.id,
        employee_id=employee.id,
        version=_next_version(db, employee.id, window.id),
        source=FORM_SOURCE_PORTAL,
        status=FORM_STATUS_SUBMITTED,
        snapshot={},
        signature_name=body.signature_name,
        signed_at=signed_at,
        signer_ip=meta.ip,
        signer_user_agent=meta.user_agent,
        submitted_by_member_id=meta.member_account_id,
    )
    _insert_with_reference(db, sub, year.year)
    snapshot = build_snapshot(
        db, enrollment=enrollment, employee=employee, ctx=ctx, selections=selections,
        body=body, pending=pending, family=family, reference_no=sub.reference_no,
        version=sub.version, signed_at=signed_at,
    )
    sub.snapshot = snapshot
    if body.request_id:
        snapshot["request_id"] = body.request_id
        snapshot["request_hash"] = content_hash(body.model_dump(mode="json"))
    sub.content_sha256 = content_hash(snapshot)
    store_pdf(
        db, client=client, sub=sub,
        content=render_form_pdf(snapshot, sub.content_sha256),
        file_name=pdf_file_name(sub.reference_no, employee),
        uploaded_by_member_id=meta.member_account_id,
    )
    _supersede_previous(db, sub)
    db.flush()
    return sub


async def record_paper_form(
    db: Session,
    *,
    employee: Employee,
    window: EnrollmentWindow | None,
    file: UploadFile,
    user_id: str,
    note: str | None,
) -> EnrollmentFormSubmission:
    """A scanned paper form, filed into the same register. The scan goes
    through ``attach_document`` (suffix allowlist, size cap, malware scan).
    Flushes, does not commit."""
    year = db.get(PolicyYear, employee.policy_year_id)
    client = db.get(Client, employee.client_id)
    assert year is not None and client is not None
    enrollment_id = None
    if window is not None:
        enrollment_id = db.execute(
            select(Enrollment.id).where(
                Enrollment.window_id == window.id, Enrollment.employee_id == employee.id
            )
        ).scalar_one_or_none()
    _lock_employee(db, employee.id)
    sub = EnrollmentFormSubmission(
        client_id=employee.client_id,
        policy_year_id=employee.policy_year_id,
        window_id=window.id if window else None,
        enrollment_id=enrollment_id,
        employee_id=employee.id,
        version=_next_version(db, employee.id, window.id if window else None),
        source=FORM_SOURCE_PAPER,
        status=FORM_STATUS_SUBMITTED,
        snapshot={},
        signed_at=datetime.now(UTC),
        submitted_by_user_id=user_id,
        broker_note=note,
    )
    _insert_with_reference(db, sub, year.year)
    doc = await attach_document(
        db,
        client_id=client.id,
        broker_firm_id=client.broker_firm_id,
        entity_type=DOC_ENTITY_ENROL_FORM,
        entity_id=sub.id,
        file=file,
        uploaded_by_user_id=user_id,
    )
    sub.document_id = doc.id
    _supersede_previous(db, sub)
    db.flush()
    return sub
