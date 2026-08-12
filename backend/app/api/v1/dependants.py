"""Dependant upload + list endpoints with multi-key linking."""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Annotated, Any

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
from sqlalchemy import func, or_, select
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
from app.services.roster_attributes import (
    first_value,
    mask_nric,
    suspect_nric_warning,
)
from app.services.roster_dedup import (
    dependant_agnostic_keys,
    dependant_candidate_keys,
    dependant_nric,
)
from app.services.roster_parser import parse_dependant_workbook
from app.services.roster_reports import build_dependant_report_workbook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dependants", tags=["dependants"])


def _employee_link_indexes(
    employees: list[Employee],
) -> tuple[dict[str, Employee], dict[str, Employee], set[str]]:
    """Staff-id and name lookup maps for dependant linking.

    Names that resolve to more than one employee are AMBIGUOUS and excluded from
    ``by_name`` — linking a dependant by a shared name would silently attach it
    to whichever employee won a dict race. The ambiguous names are returned so
    the caller can leave those dependants unlinked and report them. Staff-id and
    name keys are ``.strip().lower()``d consistently so a stray-whitespace
    staff_id still links.
    """
    by_staff: dict[str, Employee] = {}
    name_groups: dict[str, list[Employee]] = {}
    for e in employees:
        if e.staff_id and str(e.staff_id).strip():
            by_staff[str(e.staff_id).strip().lower()] = e
        nm = (e.employee_name or "").strip().lower()
        if nm:
            name_groups.setdefault(nm, []).append(e)
    by_name = {nm: emps[0] for nm, emps in name_groups.items() if len(emps) == 1}
    ambiguous = {nm for nm, emps in name_groups.items() if len(emps) > 1}
    return by_staff, by_name, ambiguous


def _first_free(
    index: dict[str, str], keys: list[str], claimed: set[str]
) -> str | None:
    """The first stored row these keys reach that no earlier row has claimed.

    One stored row absorbs one uploaded row, mirroring ``adc._resolve``. Without
    the claim check a single stored row answers for every row that reaches it,
    which is how two parents' dependants were both discarded against one orphan.
    """
    for k in keys:
        hit = index.get(k)
        if hit is not None and hit not in claimed:
            return hit
    return None


@router.get("", response_model=DependantList)
def list_dependants(
    policy_year_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    unlinked_only: bool = Query(False),
    q: str | None = None,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(active|pending_approval|rejected|terminated|all)$",
    ),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DependantList:
    assert_policy_year_for_user(policy_year_id, user, db)
    conditions = [Dependant.policy_year_id == policy_year_id]
    if unlinked_only:
        conditions.append(Dependant.employee_id == None)  # noqa: E711
    if q and q.strip():
        # Dependant name lives in the JSON attribute blob (not a column), so
        # search the known name keys plus the normalized NRIC and the linked
        # employee's staff id/name. Portable JSON element access → JSON_EXTRACT
        # on SQLite, ->> on Postgres JSONB.
        like = f"%{q.strip().replace('%', '').replace('_', '')}%"
        searchable = [
            Dependant.national_id_normalized,
            Dependant.attribute_values["dependant_name"].as_string(),
            Dependant.attribute_values["name"].as_string(),
            Dependant.attribute_values["employee_staff_id"].as_string(),
            Dependant.attribute_values["employee_name"].as_string(),
        ]
        conditions.append(or_(*[col.ilike(like) for col in searchable]))
    # Default view shows only active dependants — pending self-adds live in the
    # approvals card, and rejected/terminated are historical. Opt in explicitly.
    if status_filter == "all":
        pass
    elif status_filter:
        conditions.append(Dependant.status == status_filter)
    else:
        conditions.append(Dependant.status == DEPENDANT_STATUS_ACTIVE)
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
    by_staff, by_name, _ambiguous = _employee_link_indexes(employees)

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
        if staff_id and str(staff_id).strip().lower() in by_staff:
            emp = by_staff[str(staff_id).strip().lower()]
            method = "staff_id"
        elif emp_name:
            key = str(emp_name).strip().lower()
            # Ambiguous names (shared by 2+ employees) are absent from by_name,
            # so this leaves the dependant unlinked rather than mis-linking it.
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
        # Keep the durable dedup key in sync with an edited NRIC (see the
        # employee PATCH for the same invariant).
        d.national_id_normalized = dependant_nric(d.attribute_values or {})
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

    # Editing attributes (e.g. relationship) or relinking can change a
    # sponsoring employee's family status, which sizes the flex wallet — refresh
    # assignments like the upload/approval paths. Best-effort; must not fail the
    # edit.
    flex_errors: list[str] = []
    if d.status == DEPENDANT_STATUS_ACTIVE and (
        payload.attribute_values is not None or payload.relink
    ):
        assign_flex_safe(
            db, user, d.policy_year_id, d.client_id,
            trigger="auto_on_dependant_edit", errors=flex_errors,
        )
    db.refresh(d)
    out = DependantOut.model_validate(d)
    out.flex_errors = flex_errors
    return out


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
    except Exception:
        # Transient/permission errors from the storage backend (e.g. Azure Blob
        # in prod) must not leak a stack trace or storage path to the caller.
        logger.exception("Failed to read dependant document %s", doc.id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Document storage is temporarily unavailable"
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
    confirm: bool = False,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Delete every dependant for a policy year.

    409s with code ``member_data_at_risk`` (unless ``confirm=true``) when the
    roster carries member-portal self-added dependants — pending ones awaiting
    review or approved ones a member is relying on for cover — so a broker can't
    silently discard member-submitted records. Family status is dependant-derived
    and sizes the flex wallet, so assignments are refreshed after the wipe.
    """
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
    if rows and not confirm:
        portal_added = sum(1 for r in rows if r.link_method == "member_portal")
        if portal_added:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "member_data_at_risk",
                    "message": (
                        f"Deleting these dependants will permanently destroy "
                        f"{portal_added} member self-added dependant"
                        f"{'' if portal_added == 1 else 's'}"
                        " (pending or approved). Pass confirm=true to proceed anyway."
                    ),
                    "member_added_at_risk": portal_added,
                },
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

    # Dependants size the flex wallet via family status — refresh assignments so
    # a wipe can't leave employees over-funded for cover that no longer exists.
    # Best-effort: a failure here must not fail the delete.
    flex_errors: list[str] = []
    assign_flex_safe(
        db, user, policy_year_id, client_id,
        trigger="auto_on_dependant_bulk_delete", errors=flex_errors,
    )
    return {"deleted": deleted, "flex_errors": flex_errors}


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

    # Nothing parsed is almost always the wrong file or the wrong tab (an
    # employee listing dropped on the Dependants upload). The parser drops those
    # rows rather than importing them, which without this reads as a successful
    # "0 rows added" and looks like the file was accepted.
    no_dependant_rows = (
        ["No dependant rows found — the file needs a Dependant Name or "
         "Dependant's Identification No. column. An employee listing uploaded "
         "here has neither; upload it on the Employees tab instead."]
        if not records
        else []
    )

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
    by_staff, by_name, ambiguous_names = _employee_link_indexes(employees)
    ambiguous_hits: set[str] = set()

    # Existing dependant identity keys — dedup fixes the historical bug where a
    # re-uploaded dependant file blindly doubled every row. Keys are scoped to
    # the sponsoring employee, so a row under a DIFFERENT employee is a second
    # coverage line rather than a collision.
    existing_keys: dict[str, str] = {}
    # The agnostic keys of LINKED rows, indexed apart and read ONLY when the
    # incoming row is unlinked (`dependant_agnostic_keys`). Merged into
    # `existing_keys` they would swallow the second parent's child again.
    linked_agnostic: dict[str, str] = {}
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
        # Emit the employee-agnostic key only for currently-unlinked existing
        # dependants, so a later linked re-upload still dedups against them
        # without letting two families' NRIC-less dependants false-match.
        for k in dependant_candidate_keys(
            d_attrs, d_emp_id, include_agnostic=d_emp_id is None, nric=d_nid
        ):
            existing_keys.setdefault(k, did)
        if d_emp_id:
            for k in dependant_agnostic_keys(d_attrs, nric=d_nid):
                linked_agnostic.setdefault(k, did)

    inserted = 0
    errors: list[str] = list(no_dependant_rows)
    warnings: list[str] = []

    nric_warning = suspect_nric_warning(dependant_nric(r.attributes) for r in records)
    if nric_warning:
        warnings.append(nric_warning)
    duplicates: list[DuplicateEntry] = []
    seen: set[str] = set()
    # Stored rows already absorbed by an earlier row of this file.
    claimed: set[str] = set()
    # NRIC → the employee already carrying that life, across the rows on file
    # and the rows in this upload. Used only to COUNT dual coverage for the
    # upload summary; nothing is skipped on the strength of it.
    cross_owner: dict[str, str | None] = {
        nid: owner
        for nid, owner in db.execute(
            select(Dependant.national_id_normalized, Dependant.employee_id).where(
                Dependant.client_id == client_id,
                Dependant.policy_year_id == policy_year_id,
                Dependant.national_id_normalized.isnot(None),
            )
        ).all()
    }
    dual_covered: set[str] = set()
    for rec in records:
        emp = None
        method = None
        if (
            rec.employee_staff_id
            and rec.employee_staff_id.strip().lower() in by_staff
        ):
            emp = by_staff[rec.employee_staff_id.strip().lower()]
            method = "staff_id"
        elif rec.employee_name:
            key = rec.employee_name.strip().lower()
            if key in by_name:
                emp = by_name[key]
                method = "name"
            elif key in ambiguous_names:
                # Two+ employees share this name — don't guess. Leave unlinked
                # and report so the broker can link it manually.
                ambiguous_hits.add(rec.employee_name.strip())
        if emp is None:
            method = "unlinked"

        emp_id = emp.id if emp else None
        keys = dependant_candidate_keys(rec.attributes, emp_id)
        # Identity is scoped to the sponsor, so a row under a DIFFERENT employee
        # never collides here — the same child under both parents is two
        # coverage lines, not a duplicate, and dropping the second is what left
        # a child on whichever parent the file listed first. It is COUNTED
        # instead, and the Dependants tab's "Covered twice" column names both
        # employees once the upload lands.
        own_keys = dependant_candidate_keys(
            rec.attributes, emp_id, include_agnostic=False
        )
        existing_hit = _first_free(existing_keys, keys, claimed)
        if existing_hit is None and emp_id is None:
            # The reverse bridge. This row names no sponsor, so it cannot be a
            # second parent's coverage line — it is a row already on file,
            # re-uploaded on a sheet whose employee link failed. Without this it
            # imported as a second person on every re-upload.
            existing_hit = _first_free(
                linked_agnostic, dependant_agnostic_keys(rec.attributes), claimed
            )
        in_file_hit = any(k in seen for k in own_keys)
        if existing_hit or in_file_hit:
            # One stored row absorbs ONE incoming row. Where the stored row is
            # unlinked, both parents' rows reach it through the same bridge key;
            # leaving it unclaimed skipped them both, so neither coverage line
            # was created and the orphan stayed orphaned.
            if existing_hit:
                claimed.add(existing_hit)
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
        row_nric = dependant_nric(rec.attributes)
        if row_nric:
            other = cross_owner.get(row_nric)
            if other is not None and other != emp_id:
                dual_covered.add(row_nric)
            cross_owner.setdefault(row_nric, emp_id)
        seen.update(own_keys)
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

    if ambiguous_hits:
        names = ", ".join(sorted(ambiguous_hits))
        errors.append(
            "Left unlinked — the employee name matches more than one person; "
            f"link manually: {names}"
        )
    if dual_covered:
        n = len(dual_covered)
        warnings.append(
            f"{n} dependant{'' if n == 1 else 's'} "
            f"{'is' if n == 1 else 'are'} listed under two employees — usually a "
            "child whose parents both work here. Both are on file and both are "
            "covered; see “Covered twice” on the Dependants tab to change that."
        )

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
        warnings=warnings,
        duplicates=duplicates,
    )
