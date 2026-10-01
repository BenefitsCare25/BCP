"""The e-form register — one list of signed online forms and scanned paper
forms, read by the broker (any company they serve) and HR (their own company).

Exports: a ZIP of the PDFs, and an Excel summary (one row per form plus a
family-members sheet laid out like the insurer renewal listing).
"""
from __future__ import annotations

import io
import re
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import IO, Any

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from starlette.background import BackgroundTask

from app.core.downloads import attachment_header
from app.core.storage import get_storage
from app.models import Employee, Enrollment, EnrollmentWindow, StoredDocument
from app.models.enrollment_form import FORM_STATUS_SUPERSEDED, EnrollmentFormSubmission
from app.models.stored_document import DOC_ENTITY_ENROL_FORM, STORAGE_AVAILABLE
from app.schemas.enrollment_forms import FormRegisterItem, FormRegisterOut
from app.services.enrollment_forms.submission import count_changes
from app.services.insurer_reports import append_safe, autosize, bold_header, naive
from app.services.roster_attributes import EMPLOYEE_ID_KEYS, first_value, mask_nric

MAX_EXPORT_FORMS = 1000


@dataclass(frozen=True)
class RegisterFilter:
    client_id: str
    policy_year_id: str | None = None
    window_id: str | None = None
    status: str | None = None
    source: str | None = None
    query: str | None = None
    include_superseded: bool = False
    employee_id: str | None = None


def _conditions(f: RegisterFilter) -> list[ColumnElement[bool]]:
    sub = EnrollmentFormSubmission
    conds: list[ColumnElement[bool]] = [sub.client_id == f.client_id]
    if f.policy_year_id:
        conds.append(sub.policy_year_id == f.policy_year_id)
    if f.window_id:
        conds.append(sub.window_id == f.window_id)
    if f.employee_id:
        conds.append(sub.employee_id == f.employee_id)
    if f.status:
        conds.append(sub.status == f.status)
    elif not f.include_superseded:
        conds.append(sub.status != FORM_STATUS_SUPERSEDED)
    if f.source:
        conds.append(sub.source == f.source)
    term = (f.query or "").strip()
    if term:
        like = f"%{term.lower()}%"
        conds.append(
            or_(
                func.lower(Employee.employee_name).like(like),
                func.lower(Employee.staff_id).like(like),
                func.lower(sub.reference_no).like(like),
            )
        )
    return conds


def _base_query(f: RegisterFilter) -> Any:
    return (
        select(EnrollmentFormSubmission, Employee, EnrollmentWindow.name, Enrollment.status)
        .join(Employee, Employee.id == EnrollmentFormSubmission.employee_id)
        .outerjoin(EnrollmentWindow, EnrollmentWindow.id == EnrollmentFormSubmission.window_id)
        .outerjoin(Enrollment, Enrollment.id == EnrollmentFormSubmission.enrollment_id)
        .where(*_conditions(f))
    )


def _item(
    sub: EnrollmentFormSubmission, emp: Employee, window_name: str | None, enr_status: str | None
) -> FormRegisterItem:
    return FormRegisterItem(
        id=sub.id,
        reference_no=sub.reference_no,
        version=sub.version,
        source=sub.source,  # type: ignore[arg-type]
        status=sub.status,  # type: ignore[arg-type]
        employee_id=emp.id,
        staff_id=emp.staff_id,
        employee_name=emp.employee_name,
        id_masked=mask_nric(first_value(emp.attribute_values or {}, EMPLOYEE_ID_KEYS)),
        window_id=sub.window_id,
        window_name=window_name,
        submitted_at=sub.signed_at or sub.created_at,
        signature_name=sub.signature_name,
        acknowledged_at=sub.acknowledged_at,
        enrollment_status=enr_status,
        changes=count_changes(sub.snapshot or {}),
        has_pdf=sub.document_id is not None,
    )


def list_register(db: Session, f: RegisterFilter, offset: int, limit: int) -> FormRegisterOut:
    total = db.execute(
        select(func.count()).select_from(_base_query(f).subquery())
    ).scalar_one()
    rows = db.execute(
        _base_query(f)
        .order_by(EnrollmentFormSubmission.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    count_filter = RegisterFilter(
        client_id=f.client_id,
        policy_year_id=f.policy_year_id,
        window_id=f.window_id,
        include_superseded=True,
    )
    counts: dict[str, int] = {
        str(status): int(n)
        for status, n in db.execute(
            select(EnrollmentFormSubmission.status, func.count())
            .where(*_conditions(count_filter))
            .group_by(EnrollmentFormSubmission.status)
        ).all()
    }
    return FormRegisterOut(
        items=[_item(*row) for row in rows],
        total=int(total),
        offset=offset,
        limit=limit,
        counts=counts,
    )


def find_submission(
    db: Session, client_id: str, submission_id: str
) -> EnrollmentFormSubmission | None:
    sub = db.get(EnrollmentFormSubmission, submission_id)
    return sub if sub is not None and sub.client_id == client_id else None


def submission_pdf(
    db: Session, sub: EnrollmentFormSubmission
) -> tuple[bytes, StoredDocument] | None:
    if sub.document_id is None:
        return None
    doc = db.get(StoredDocument, sub.document_id)
    if doc is None or not _valid_doc(sub, doc):
        return None
    content = _read_blob(doc.storage_path)
    return (content, doc) if content is not None else None


def _export_rows(db: Session, f: RegisterFilter) -> list[Any]:
    return list(
        db.execute(
            _base_query(f).order_by(Employee.staff_id, EnrollmentFormSubmission.version)
            .limit(MAX_EXPORT_FORMS)
        ).all()
    )


_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._-]+")


_FETCH_WORKERS = 8
_FETCH_BATCH = 32
_SPOOL_IN_MEMORY = 32 * 1024 * 1024


def _valid_doc(sub: EnrollmentFormSubmission, doc: StoredDocument | None) -> bool:
    return (
        doc is not None
        and doc.client_id == sub.client_id
        and doc.entity_type == DOC_ENTITY_ENROL_FORM
        and doc.entity_id == sub.id
        and doc.storage_state == STORAGE_AVAILABLE
    )


def _read_blob(path: str) -> bytes | None:
    try:
        return get_storage().read(path)
    except FileNotFoundError:
        return None


def build_zip(db: Session, f: RegisterFilter) -> tuple[IO[bytes], int]:
    """The matching forms' files as one ZIP, spooled to disk past 32 MB and
    returned rewound for streaming. Blobs are fetched in parallel, a bounded
    batch at a time, so neither memory nor the request time grows with the
    serial latency of up to ``MAX_EXPORT_FORMS`` storage reads. Stored, not
    deflated: PDFs and scans are already compressed."""
    rows = _export_rows(db, f)
    doc_ids = [sub.document_id for sub, *_ in rows if sub.document_id]
    docs = {
        d.id: d
        for d in db.execute(select(StoredDocument).where(StoredDocument.id.in_(doc_ids))).scalars()
    } if doc_ids else {}
    items: list[tuple[str, StoredDocument]] = []
    for sub, emp, _window, _status in rows:
        doc = docs.get(sub.document_id or "")
        if doc is None or not _valid_doc(sub, doc):
            continue
        suffix = (doc.file_name.rsplit(".", 1)[-1] if "." in doc.file_name else "pdf").lower()
        label = _SAFE_NAME.sub("", f"{emp.staff_id} {emp.employee_name or ''}").strip()
        items.append((f"{sub.reference_no} {label} v{sub.version}.{suffix}", doc))

    spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY)
    written = 0
    with (
        ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool,
        zipfile.ZipFile(spool, "w", zipfile.ZIP_STORED) as archive,
    ):
        for start in range(0, len(items), _FETCH_BATCH):
            batch = items[start:start + _FETCH_BATCH]
            contents = pool.map(_read_blob, [doc.storage_path for _, doc in batch])
            for (name, _doc), content in zip(batch, contents, strict=True):
                if content is not None:
                    archive.writestr(name, content)
                    written += 1
    spool.seek(0)
    return spool, written


def zip_response(spool: IO[bytes], filename: str) -> StreamingResponse:
    """Stream a spooled archive and close it once sent."""
    return StreamingResponse(
        iter(lambda: spool.read(1024 * 1024), b""),
        media_type="application/zip",
        headers={"Content-Disposition": attachment_header(filename)},
        background=BackgroundTask(spool.close),
    )


_SUMMARY_HEADER = [
    "Reference", "Version", "Source", "Status", "Benefit period", "Staff ID", "Employee",
    "NRIC / FIN", "Submitted", "Signed by", "Acknowledged", "Enrolment status", "Changes",
    "Selections", "Leave", "Flex allowance", "Flex price tags", "Flex balance",
    "Contact number", "Email",
]
_FAMILY_HEADER = [
    "Reference", "Staff ID", "Employee", "Relationship", "Full name", "Sex", "NRIC / BC / FIN",
    "Occupation", "Date of birth", "Covered on", "Status",
]


def _selections_text(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    for sel in snapshot.get("selections", []):
        plan = "Declined" if sel.get("declined") else sel.get("elected_plan") or "-"
        line = f"{sel.get('product_code')}: {plan}"
        if sel.get("covered"):
            line += f" (+ {', '.join(sel['covered'])})"
        parts.append(line)
    return "; ".join(parts)


def build_workbook(db: Session, f: RegisterFilter) -> bytes:
    wb = Workbook()
    summary = wb.active
    assert summary is not None
    summary.title = "Enrolment forms"
    summary.append(_SUMMARY_HEADER)
    family = wb.create_sheet("Family members")
    family.append(_FAMILY_HEADER)
    for sub, emp, window_name, enr_status in _export_rows(db, f):
        snap = sub.snapshot or {}
        p = snap.get("particulars") or {}
        leave = snap.get("leave") or {}
        append_safe(summary, [
            sub.reference_no, sub.version, sub.source.title(), sub.status.title(),
            window_name or "", emp.staff_id, emp.employee_name or "",
            p.get("id_no") or first_value(emp.attribute_values or {}, EMPLOYEE_ID_KEYS) or "",
            naive(sub.signed_at or sub.created_at), sub.signature_name or "",
            naive(sub.acknowledged_at), (enr_status or "").replace("_", " "),
            count_changes(snap),
            _selections_text(snap) or ("See scanned form" if sub.source == "paper" else ""),
            f"{leave.get('action', '').title()} {leave.get('days', '')}".strip() if leave else "",
            *(
                (flex.get("allowance"), flex.get("price_tags_used"), flex.get("balance"))
                if (flex := snap.get("flex")) else ("", "", "")
            ),
            p.get("contact_no") or "", p.get("email") or "",
        ])
        for dep in snap.get("dependants") or []:
            append_safe(family, [
                sub.reference_no, emp.staff_id, emp.employee_name or "",
                dep.get("relationship") or "", dep.get("name") or "", dep.get("gender") or "",
                dep.get("id_no") or "", dep.get("occupation") or "", dep.get("dob") or "",
                ", ".join(dep.get("covered_on") or []),
                "Pending verification" if dep.get("status") == "pending" else "On record",
            ])
    for ws in (summary, family):
        bold_header(ws)
        autosize(ws)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
