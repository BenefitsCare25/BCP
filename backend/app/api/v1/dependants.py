"""Dependant upload + list endpoints with multi-key linking."""
from __future__ import annotations

from io import BytesIO
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_for_user,
    load_dependant,
    require_client_id,
)
from app.core.pagination import MAX_LIMIT
from app.core.rate_limit import limiter
from app.core.storage import get_storage
from app.core.uploads import WORKBOOK_SUFFIXES, saved_upload
from app.db.session import get_db
from app.models import Dependant, Employee, StoredDocument
from app.models.dependant import (
    DEPENDANT_STATUS_ACTIVE,
    DEPENDANT_STATUS_PENDING,
    DEPENDANT_STATUS_REJECTED,
)
from app.models.stored_document import DOC_ENTITY_DEPENDANT
from app.schemas.api import (
    AutoMatchResult,
    DependantList,
    DependantOut,
    DependantPatch,
    DuplicateEntry,
    UploadResult,
)
from app.schemas.claims import DependantApprovalIn, StoredDocumentOut
from app.services.flex_assignment import assign_flex_safe
from app.services.roster_attributes import first_value, mask_nric
from app.services.roster_dedup import dependant_candidate_keys, dependant_nric
from app.services.roster_parser import parse_dependant_workbook
from app.services.roster_reports import build_dependant_report_workbook

router = APIRouter(prefix="/dependants", tags=["dependants"])


@router.get("", response_model=DependantList)
def list_dependants(
    policy_year_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    unlinked_only: bool = Query(False),
    status_filter: str | None = Query(default=None, alias="status"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DependantList:
    assert_policy_year_for_user(policy_year_id, user, db)
    conditions = [Dependant.policy_year_id == policy_year_id]
    if unlinked_only:
        conditions.append(Dependant.employee_id == None)  # noqa: E711
    if status_filter:
        conditions.append(Dependant.status == status_filter)
    base = select(Dependant).where(*conditions).order_by(Dependant.id)
    total = db.scalar(select(func.count(Dependant.id)).where(*conditions)) or 0
    rows = list(db.execute(base.offset(offset).limit(limit)).scalars().all())
    return DependantList(
        total=total,
        offset=offset,
        limit=limit,
        items=[DependantOut.model_validate(r) for r in rows],
    )


@router.get("/coverage-report/export")
def dependant_coverage_report(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Dependant listing with the insurance products covering each dependant and
    the sponsoring employee's flex tier (.xlsx). NRIC masked; audited (PII)."""
    assert_policy_year_for_user(policy_year_id, user, db)
    wb = build_dependant_report_workbook(db, policy_year_id)
    buf = BytesIO()
    wb.save(buf)
    write_audit(
        db, user, action="export", entity_type="dependant_coverage_report",
        entity_id=policy_year_id, after={"policy_year_id": policy_year_id},
    )
    db.commit()
    return Response(
        content=buf.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                'attachment; filename="dependant-coverage.xlsx"'
            )
        },
    )


@router.post("/auto-match", response_model=AutoMatchResult)
@limiter.limit("10/minute")
def auto_match_dependants(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoMatchResult:
    """Re-run employee-linking for all unlinked dependants.

    Uses employee_staff_id / employee_name / employee_id_no stored in
    attribute_values (populated since roster parser v2) to re-attempt the same
    multi-key lookup used during upload.
    """
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)

    employees = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
            )
        ).scalars().all()
    )
    by_staff = {e.staff_id.lower(): e for e in employees if e.staff_id}
    by_name = {
        (e.employee_name or "").lower().strip(): e
        for e in employees
        if e.employee_name
    }

    unlinked = list(
        db.execute(
            select(Dependant).where(
                Dependant.client_id == client_id,
                Dependant.policy_year_id == policy_year_id,
                Dependant.employee_id == None,  # noqa: E711
            )
        ).scalars().all()
    )

    matched = 0
    for dep in unlinked:
        attrs = dep.attribute_values or {}
        staff_id = attrs.get("employee_staff_id")
        emp_name = attrs.get("employee_name")

        emp = None
        method = None
        if staff_id and str(staff_id).lower() in by_staff:
            emp = by_staff[str(staff_id).lower()]
            method = "staff_id"
        elif emp_name:
            key = str(emp_name).lower().strip()
            if key in by_name:
                emp = by_name[key]
                method = "name"

        if emp:
            dep.employee_id = emp.id
            dep.link_method = f"auto_{method}"
            matched += 1

    if matched:
        write_audit(
            db, user, action="auto_match", entity_type="dependants", entity_id=None,
            after={"matched": matched, "policy_year_id": policy_year_id},
        )
        db.commit()

    return AutoMatchResult(matched=matched, unmatched=len(unlinked) - matched)


@router.patch("/{dependant_id}", response_model=DependantOut)
def update_dependant(
    payload: DependantPatch,
    d: Dependant = Depends(load_dependant),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DependantOut:
    """Edit a dependant's attributes and/or relink it to a different employee.

    Pass `relink: true` with `employee_id` (or null to unlink). A target
    employee must belong to the same tenant + policy year as the dependant.
    """
    before = {
        "attribute_values": d.attribute_values,
        "employee_id": d.employee_id,
        "link_method": d.link_method,
    }
    if payload.attribute_values is not None:
        d.attribute_values = payload.attribute_values
    if payload.relink:
        if payload.employee_id is not None:
            emp = db.get(Employee, payload.employee_id)
            if (
                emp is None
                or emp.client_id != d.client_id
                or emp.policy_year_id != d.policy_year_id
            ):
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    "Employee not found for this policy year",
                )
            d.employee_id = emp.id
            d.link_method = "manual"
        else:
            d.employee_id = None
            d.link_method = "unlinked"
    write_audit(
        db, user, action="update", entity_type="dependant", entity_id=d.id,
        before=before,
        after={"attribute_values": d.attribute_values, "employee_id": d.employee_id,
               "link_method": d.link_method},
    )
    db.commit()
    db.refresh(d)
    return DependantOut.model_validate(d)


@router.post("/{dependant_id}/approval", response_model=DependantOut)
def decide_dependant_approval(
    body: DependantApprovalIn,
    d: Dependant = Depends(load_dependant),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DependantOut:
    """Approve or reject a portal self-added dependant.

    Approval activates the dependant and re-runs flex assignment (family
    status may change → wallet size), exactly like the bulk-upload path.
    """
    if d.status != DEPENDANT_STATUS_PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only a pending dependant can be approved or rejected.",
        )
    before = {"status": d.status}
    d.status = (
        DEPENDANT_STATUS_ACTIVE if body.action == "approve" else DEPENDANT_STATUS_REJECTED
    )
    write_audit(
        db, user, action=f"dependant.{body.action}", entity_type="dependant",
        entity_id=d.id,
        before=before,
        after={"status": d.status, "note": body.note},
        employee_id=d.employee_id,
    )
    db.commit()

    flex_errors: list[str] = []
    if body.action == "approve":
        assign_flex_safe(
            db, user, d.policy_year_id, d.client_id,
            trigger="auto_on_dependant_approval", errors=flex_errors,
        )
    db.refresh(d)
    out = DependantOut.model_validate(d)
    # A flex re-assign failure must not masquerade as full success — the UI
    # branches its toast on this.
    out.flex_errors = flex_errors
    return out


@router.get(
    "/{dependant_id}/documents", response_model=list[StoredDocumentOut]
)
def list_dependant_documents(
    d: Dependant = Depends(load_dependant),
    db: Session = Depends(get_db),
) -> list[StoredDocumentOut]:
    """Proof documents a member attached to a pending self-added dependant."""
    docs = db.execute(
        select(StoredDocument)
        .where(
            StoredDocument.entity_type == DOC_ENTITY_DEPENDANT,
            StoredDocument.entity_id == d.id,
        )
        .order_by(StoredDocument.created_at)
    ).scalars().all()
    return [StoredDocumentOut.model_validate(doc) for doc in docs]


@router.get("/{dependant_id}/documents/{doc_id}/download")
def download_dependant_document(
    doc_id: str,
    d: Dependant = Depends(load_dependant),
    db: Session = Depends(get_db),
) -> Response:
    doc = db.get(StoredDocument, doc_id)
    if (
        doc is None
        or doc.entity_type != DOC_ENTITY_DEPENDANT
        or doc.entity_id != d.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        content = get_storage().read(doc.storage_path)
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes are no longer available"
        ) from None
    safe_name = doc.file_name.replace('"', "")
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.delete("", response_model=dict)
@limiter.limit("10/minute")
def bulk_delete_dependants(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    rows = list(
        db.execute(
            select(Dependant).where(
                Dependant.client_id == client_id,
                Dependant.policy_year_id == policy_year_id,
            )
        )
        .scalars()
        .all()
    )
    deleted = len(rows)
    for r in rows:
        db.delete(r)
    write_audit(
        db,
        user,
        action="bulk_delete",
        entity_type="dependants",
        entity_id=None,
        after={"deleted": deleted, "policy_year_id": policy_year_id},
    )
    db.commit()
    return {"deleted": deleted}


@router.post("/upload", response_model=UploadResult)
@limiter.limit("20/minute")
async def upload_dependants(
    request: Request,
    file: Annotated[UploadFile, File()],
    policy_year_id: Annotated[str, Form()],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadResult:
    client_id = require_client_id(user)
    py = assert_policy_year_for_user(policy_year_id, user, db)
    # Invariant: a dependant's client must own the policy year (guards the
    # system_admin path, where the cross-client check above is bypassed).
    if py.client_id != client_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Policy year belongs to a different client than the active one.",
        )

    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        records = parse_dependant_workbook(tmp_path)

    # Build employee lookup indexes for linking.
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
            )
        )
        .scalars()
        .all()
    )
    by_staff = {e.staff_id.lower(): e for e in employees if e.staff_id}
    by_name = {
        (e.employee_name or "").lower().strip(): e
        for e in employees
        if e.employee_name
    }

    # Existing dependant identity keys — dedup fixes the historical bug where a
    # re-uploaded dependant file blindly doubled every row.
    existing_keys: dict[str, str] = {}
    for did, d_emp_id, d_nid, d_attrs in db.execute(
        select(
            Dependant.id,
            Dependant.employee_id,
            Dependant.national_id_normalized,
            Dependant.attribute_values,
        ).where(
            Dependant.client_id == client_id,
            Dependant.policy_year_id == policy_year_id,
        )
    ).all():
        if d_nid:
            existing_keys.setdefault(f"nric:{d_nid}", did)
        # Emit the employee-agnostic key only for currently-unlinked existing
        # dependants, so a later linked re-upload still dedups against them
        # without letting two families' NRIC-less dependants false-match.
        for k in dependant_candidate_keys(
            d_attrs, d_emp_id, include_agnostic=d_emp_id is None
        ):
            existing_keys.setdefault(k, did)

    inserted = 0
    errors: list[str] = []
    duplicates: list[DuplicateEntry] = []
    seen: set[str] = set()
    for rec in records:
        emp = None
        method = None
        if rec.employee_staff_id and rec.employee_staff_id.lower() in by_staff:
            emp = by_staff[rec.employee_staff_id.lower()]
            method = "staff_id"
        elif rec.employee_name:
            key = rec.employee_name.lower().strip()
            if key in by_name:
                emp = by_name[key]
                method = "name"
        if emp is None:
            method = "unlinked"

        emp_id = emp.id if emp else None
        keys = dependant_candidate_keys(rec.attributes, emp_id)
        existing_hit = next((existing_keys[k] for k in keys if k in existing_keys), None)
        in_file_hit = any(k in seen for k in keys)
        if existing_hit or in_file_hit:
            duplicates.append(
                DuplicateEntry(
                    row=rec.row,
                    name=first_value(rec.attributes, ("dependant_name", "name")),
                    staff_id=rec.employee_staff_id,
                    nric_masked=mask_nric(dependant_nric(rec.attributes)) or None,
                    reason="existing" if existing_hit else "in_file",
                    existing_id=existing_hit,
                )
            )
            continue
        seen.update(keys)
        db.add(
            Dependant(
                client_id=client_id,
                policy_year_id=policy_year_id,
                employee_id=emp_id,
                attribute_values=rec.attributes,
                link_method=method,
                national_id_normalized=dependant_nric(rec.attributes),
            )
        )
        inserted += 1

    write_audit(
        db,
        user,
        action="upload",
        entity_type="dependants",
        entity_id=None,
        after={
            "inserted": inserted,
            "skipped": len(duplicates),
            "filename": file.filename,
        },
    )
    db.commit()

    # Dependant records are the authoritative source for family status, which
    # sizes the Flex wallet — so refresh assignments when a confirmed scheme
    # exists. Best-effort: a failure here must not fail the upload.
    assign_flex_safe(
        db, user, policy_year_id, client_id,
        trigger="auto_on_dependant_upload", errors=errors,
    )

    return UploadResult(
        inserted=inserted,
        skipped=len(duplicates),
        errors=errors,
        duplicates=duplicates,
    )
