"""Receive a QR form: resolve the link, check and price it, freeze it, file the PDF.

An intake is evidence of what someone submitted through the company's shared
link. It changes no employee, dependant, election or coverage; a broker links
it to a roster employee (``review.link_intake``) before it joins the register.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.core.crypto import constant_time_eq
from app.core.request_context import get_client_ip, get_user_agent
from app.core.storage import StorageScopeError, company_firm_id, document_path, get_storage
from app.db.base import new_uuid
from app.models import (
    Client,
    Employee,
    EnrollmentWindow,
    PolicyYear,
    PublicFormEntry,
    PublicIntake,
    StoredDocument,
)
from app.models.enrollment_window import WindowStatus, WindowType
from app.models.qr_enrolment import INTAKE_PENDING
from app.schemas.qr_enrolment import PublicSubmissionIn
from app.services.claims import assert_document_scope, track_pending_blob
from app.services.enrollment_forms.pdf import render_form_pdf
from app.services.enrollment_forms.pricing import plan_facts
from app.services.enrollment_forms.submission import content_hash
from app.services.enrollment_validation import window_in_period
from app.services.flex_pricing.context import reference_date
from app.services.qr_enrolment.access import hash_token, link_not_found, write_public_audit
from app.services.qr_enrolment.catalog import Catalog, build_catalog
from app.services.qr_enrolment.choices import check_submission
from app.services.roster_attributes import normalize_nric

DOC_ENTITY_QR_INTAKE = "qr_intake"
COPY_MINUTES = 15
_REF_ATTEMPTS = 5
_REF_SEQ = re.compile(r"-(\d+)$")
PROVENANCE = "Submitted through the company QR form. Identity not yet verified."
SIGN_METHOD = "Typed name and declarations confirmed on the company QR form (no sign-in)"


def resolve_entry(
    db: Session, raw: str, firm_id: str | None
) -> tuple[PublicFormEntry, EnrollmentWindow]:
    """The entry behind a link — only within the host's firm. Schema routing
    already confines the lookup; the firm check keeps isolation even where
    schemas are shared (SQLite)."""
    entry = db.execute(
        select(PublicFormEntry).where(PublicFormEntry.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    window = db.get(EnrollmentWindow, entry.window_id) if entry is not None else None
    if entry is None or window is None or (
        firm_id is not None and company_firm_id(db, entry.client_id) != firm_id
    ):
        raise link_not_found()
    return entry, window


def form_status(entry: PublicFormEntry, window: EnrollmentWindow) -> str:
    if not entry.enabled:
        return "paused"
    if window_in_period(window):
        return "open"
    opens = window.opens_at if window.opens_at.tzinfo else window.opens_at.replace(tzinfo=UTC)
    if window.status == WindowStatus.draft or (
        window.status == WindowStatus.open and datetime.now(UTC) < opens
    ):
        return "not_yet_open"
    return "closed"


def _daily_cap(db: Session, window: EnrollmentWindow) -> int:
    headcount = db.execute(
        select(func.count(Employee.id)).where(
            Employee.policy_year_id == window.policy_year_id, Employee.status == "active"
        )
    ).scalar_one()
    return max(50, 3 * int(headcount or 0))


PENDING_PER_PERSON = 3


def _count(db: Session, *conds: Any) -> int:
    return int(db.execute(select(func.count(PublicIntake.id)).where(*conds)).scalar_one() or 0)


def _assert_under_cap(
    db: Session, entry: PublicFormEntry, window: EnrollmentWindow, national_id: str | None
) -> None:
    """Three limits, so one person holding the shared QR cannot fill it for
    everyone: per form per day (storage), per network address per day (sized
    for a whole office behind one address), and per claimed person while
    earlier submissions still await the broker."""
    since = datetime.now(UTC) - timedelta(days=1)
    cap = _daily_cap(db, window)
    busy = {
        "code": "form_busy",
        "message": "This form has received too many submissions. Try again tomorrow "
        "or contact your HR team.",
    }
    recent = (PublicIntake.entry_id == entry.id, PublicIntake.created_at >= since)
    if _count(db, *recent) >= cap:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, busy)
    ip = (get_client_ip() or "")[:64]
    # Half the daily cap from one address: every employee of a one-office
    # company plus resubmissions, while two addresses are needed to fill it.
    if ip and _count(db, *recent, PublicIntake.signer_ip == ip) >= max(50, cap // 2):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, busy)
    if national_id and _count(
        db, PublicIntake.entry_id == entry.id, PublicIntake.status == INTAKE_PENDING,
        PublicIntake.claimed_national_id == national_id,
    ) >= PENDING_PER_PERSON:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, {
            "code": "already_submitted",
            "message": "We already have your earlier forms waiting for review. Contact your "
            "HR team if something needs to change.",
        })


def _request_hash(body: PublicSubmissionIn) -> str:
    payload = body.model_dump(mode="json", exclude={"website"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _next_reference(db: Session, client_id: str, year: int) -> str:
    prefix = f"QR-{year}-"
    last = db.execute(
        select(PublicIntake.reference_no)
        .where(PublicIntake.client_id == client_id, PublicIntake.reference_no.like(f"{prefix}%"))
        .order_by(func.length(PublicIntake.reference_no).desc(), PublicIntake.reference_no.desc())
        .limit(1)
    ).scalar_one_or_none()
    match = _REF_SEQ.search(last or "")
    return f"{prefix}{(int(match.group(1)) if match else 0) + 1:05d}"


def _insert(db: Session, intake: PublicIntake, year: int) -> None:
    for _attempt in range(_REF_ATTEMPTS):
        intake.reference_no = _next_reference(db, intake.client_id, year)
        intake.snapshot["form"]["reference_no"] = intake.reference_no
        try:
            with db.begin_nested():
                db.add(intake)
                db.flush()
            return
        except IntegrityError:
            if intake in db:
                db.expunge(intake)
    raise HTTPException(status.HTTP_409_CONFLICT, "Please try submitting again.")


EVENT_DAYS = 90


def _event_window(window: EnrollmentWindow) -> bool:
    return window.window_type in (WindowType.new_hire, WindowType.life_event)


def _life_event(window: EnrollmentWindow, body: PublicSubmissionIn) -> date | None:
    """The event date to measure ages on: only in a new-hire or life-event
    period, and only a recent, past date (the 30-day rule with some grace).
    Anything else measures ages at the policy start, as the paper forms do."""
    if body.life_event is None or not _event_window(window):
        return None
    today = business_today()
    when = body.life_event.date
    return when if today - timedelta(days=EVENT_DAYS) <= when <= today else None


def _premium_note(catalog: Catalog) -> str:
    gst = any(p.gst_included for p in catalog.products)
    return (
        "Premiums are annual amounts"
        + (", inclusive of GST" if gst else "")
        + ". Your share is deducted from your salary, pro-rated where applicable. "
        "Premiums depending on age or salary are confirmed after verification."
    )


def _snapshot(
    db: Session,
    window: EnrollmentWindow,
    catalog: Catalog,
    body: PublicSubmissionIn,
    checked: Any,
    now: datetime,
) -> dict[str, Any]:
    client = db.get(Client, window.client_id)
    year = db.get(PolicyYear, window.policy_year_id)
    assert client is not None and year is not None
    p = body.particulars
    named = bool(body.dependants)
    accepted = set(body.accepted_clause_ids)
    return {
        "schema": 2,
        "source": "qr",
        "provenance": PROVENANCE,
        "form": {
            "company_name": client.legal_name or client.name,
            "title": catalog.settings.title,
            "policy_start": year.start_date.isoformat(),
            "policy_end": year.end_date.isoformat(),
            "closes_at": window.closes_at.isoformat(),
            "reference_no": "",
            "version": 1,
            "submitted_at": now.isoformat(),
            "submission_note": catalog.settings.submission_note,
            "helpline": catalog.settings.helpline,
        },
        "particulars": {
            "name": p.name,
            "id_no": p.id_no,
            "staff_id": p.staff_id,
            "gender": {"M": "Male", "F": "Female"}[p.gender],
            "dob": p.dob.isoformat() if p.dob else None,
            "job_grade": p.job_grade,
            "date_of_hire": p.date_of_hire.isoformat() if p.date_of_hire else None,
            "contact_no": p.contact_no,
            "email": p.email,
            "marital_status": p.marital_status.title() if p.marital_status else None,
        },
        "life_event": (
            {"kind": body.life_event.kind, "date": body.life_event.date.isoformat()}
            if body.life_event and _event_window(window) else None
        ),
        "intro_lines": [],
        "selections": checked.selections,
        "premium_note": _premium_note(catalog),
        "dependants": checked.dependants,
        "eligibility_notes": catalog.settings.eligibility_notes,
        "documents": [{"id": d.id, "label": d.label} for d in catalog.settings.documents],
        "declarations": [
            {"id": c.id, "text": c.text, "accepted": c.id in accepted}
            for c in catalog.clauses()
            if c.applies_to == "all" or named
        ],
        "signature": {
            "name": " ".join(body.signature_name.split()),
            "signed_at": now.isoformat(),
            "method": SIGN_METHOD,
        },
    }


def _store_pdf(db: Session, client: Client, intake: PublicIntake) -> None:
    content = render_form_pdf(intake.snapshot, intake.content_sha256)
    doc_id = new_uuid()
    path = document_path(
        client.broker_firm_id, client.id, DOC_ENTITY_QR_INTAKE, intake.id, doc_id, ".pdf"
    )
    blob = get_storage().save(io.BytesIO(content), path)
    track_pending_blob(db, blob.path)
    staff = re.sub(r"[^A-Za-z0-9 _-]", "", intake.claimed_staff_id or "").strip()
    db.add(StoredDocument(
        id=doc_id,
        client_id=client.id,
        entity_type=DOC_ENTITY_QR_INTAKE,
        entity_id=intake.id,
        file_name=f"Enrolment form {intake.reference_no} {staff}".strip() + ".pdf",
        mime_type="application/pdf",
        size_bytes=blob.size_bytes,
        sha256=blob.sha256,
        storage_path=blob.path,
    ))
    intake.document_id = doc_id


def _issue_copy_key(intake: PublicIntake, now: datetime) -> str:
    key = secrets.token_urlsafe(24)
    intake.copy_key_hash = hash_token(key)
    intake.copy_expires_at = now + timedelta(minutes=COPY_MINUTES)
    return key


def submit_intake(
    db: Session, entry: PublicFormEntry, window: EnrollmentWindow, body: PublicSubmissionIn
) -> tuple[PublicIntake, str]:
    """File one submission. Returns the intake and a one-time copy key. A retry
    of the same request returns the original intake; a different body under
    the same request id is refused."""
    if body.website.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This form could not be submitted.")
    if form_status(entry, window) != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "form_closed", "message": "This enrolment form is not open.",
        })
    now = datetime.now(UTC)
    request_hash = _request_hash(body)
    existing = db.execute(
        select(PublicIntake).where(
            PublicIntake.entry_id == entry.id, PublicIntake.request_id == body.request_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, {
                "code": "request_reused",
                "message": "This form was already submitted with different details.",
            })
        return existing, _issue_copy_key(existing, now)
    _assert_under_cap(db, entry, window, normalize_nric(body.particulars.id_no))
    catalog = build_catalog(db, window)
    facts = plan_facts(db, catalog.tier_sets, window.policy_year_id)
    event = _life_event(window, body)
    ref = event or reference_date(db, window.policy_year_id)
    checked = check_submission(catalog, body, facts, ref, window.allow_dependant_changes)
    if body.life_event and event is None and _event_window(window):
        checked.problems.append(
            "The event date must be within the last 90 days and not in the future."
        )
    if checked.problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "invalid_form",
            "message": "Please fix the highlighted details and submit again.",
            "problems": list(dict.fromkeys(checked.problems)),
        })
    client = db.get(Client, window.client_id)
    year = db.get(PolicyYear, window.policy_year_id)
    assert client is not None and year is not None
    snapshot = _snapshot(db, window, catalog, body, checked, now)
    intake = PublicIntake(
        client_id=window.client_id,
        policy_year_id=window.policy_year_id,
        window_id=window.id,
        entry_id=entry.id,
        reference_no="",
        status=INTAKE_PENDING,
        claimed_staff_id=body.particulars.staff_id,
        claimed_name=body.particulars.name,
        claimed_national_id=normalize_nric(body.particulars.id_no),
        claimed_category_id=checked.category_id,
        snapshot=snapshot,
        content_sha256="",
        request_id=body.request_id,
        request_hash=request_hash,
        signer_ip=(get_client_ip() or "")[:64] or None,
        signer_user_agent=(get_user_agent() or "")[:512] or None,
    )
    _insert(db, intake, year.year)
    intake.content_sha256 = content_hash(intake.snapshot)
    _store_pdf(db, client, intake)
    key = _issue_copy_key(intake, now)
    write_public_audit(
        db, client_id=intake.client_id, action="qr_intake.submitted",
        entity_type="public_intake", entity_id=intake.id,
        after={"reference_no": intake.reference_no, "window_id": window.id},
    )
    db.flush()
    return intake, key


def intake_pdf(db: Session, intake: PublicIntake) -> tuple[bytes, StoredDocument] | None:
    if intake.document_id is None:
        return None
    doc = db.get(StoredDocument, intake.document_id)
    if (
        doc is None or doc.client_id != intake.client_id
        or doc.entity_type != DOC_ENTITY_QR_INTAKE or doc.entity_id != intake.id
    ):
        return None
    try:
        firm_id = company_firm_id(db, intake.client_id)
        assert_document_scope(doc.storage_path, firm_id, intake.client_id)
        return get_storage().read(doc.storage_path), doc
    except (FileNotFoundError, StorageScopeError):
        return None


def redeem_copy(
    db: Session, entry: PublicFormEntry, reference_no: str, key: str
) -> tuple[bytes, str]:
    """The employee's own PDF, once, within ``COPY_MINUTES`` of submitting, for
    the holder of the key that submission returned. Anything else is a 404."""
    intake = db.execute(
        select(PublicIntake).where(
            PublicIntake.entry_id == entry.id, PublicIntake.reference_no == reference_no
        ).with_for_update()
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    expires = intake.copy_expires_at if intake is not None else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if (
        intake is None or intake.copy_key_hash is None or intake.copy_used_at is not None
        or expires is None or now > expires
        or not constant_time_eq(intake.copy_key_hash, hash_token(key))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "copy_unavailable",
            "message": "Your copy is no longer available from this page. Your form is safely "
            "received; ask your HR team if you need a copy.",
        })
    found = intake_pdf(db, intake)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Your copy is not available.")
    intake.copy_used_at = now
    write_public_audit(
        db, client_id=intake.client_id, action="qr_intake.copy_downloaded",
        entity_type="public_intake", entity_id=intake.id,
    )
    content, doc = found
    return content, doc.file_name
