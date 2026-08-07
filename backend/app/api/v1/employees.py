"""Employee upload + list endpoints."""
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
    load_employee,
    require_client_id,
    tenant_or_global,
)
from app.core.pagination import MAX_LIMIT
from app.core.rate_limit import limiter
from app.core.uploads import WORKBOOK_SUFFIXES, saved_upload
from app.db.session import get_db
from app.models import (
    Claim,
    Employee,
    EmployeeAttributeSchema,
    Enrollment,
    LeaveElection,
    MemberAccount,
)
from app.models.employee import EMPLOYEE_STATUS_TERMINATED
from app.models.enrollment import EnrollmentStatus
from app.models.member_account import MEMBER_STATUS_DISABLED
from app.schemas.api import (
    BenefitStatementOut,
    CoverageSummary,
    DuplicateEntry,
    EmployeeList,
    EmployeeOut,
    EmployeePatch,
    UploadResult,
)
from app.schemas.claims import UtilizationOut
from app.services.benefit_statement import build_benefit_statement
from app.services.coverage_resolver import find_orphan_overrides, load_overrides
from app.services.coverage_summary import build_coverage_items
from app.services.derivation_engine import derive
from app.services.flex_assignment import assign_flex_safe
from app.services.matching_engine import match_policy_year
from app.services.member_query import looks_like_nric
from app.services.plan_hydration import hydrate_plans as _hydrate_plans
from app.services.roster_attributes import (
    mask_nric,
    normalize_nric,
    suspect_nric_warning,
)
from app.services.roster_dedup import employee_candidate_keys, employee_nric
from app.services.roster_parser import parse_employee_workbook
from app.services.roster_reports import build_employee_report_workbook
from app.services.utilization import build_utilization

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/employees", tags=["employees"])


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so a literal % or _ isn't matched as one."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=EmployeeList)
def list_employees(
    policy_year_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    q: str | None = None,
    match_status: str | None = Query(None, pattern="^(matched|unmatched)$"),
    status_filter: str | None = Query(
        None, alias="status", pattern="^(active|terminated|all)$"
    ),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmployeeList:
    assert_policy_year_for_user(policy_year_id, user, db)

    filters = [Employee.policy_year_id == policy_year_id]
    # Default view excludes soft-terminated leavers; opt in with status=terminated|all.
    if status_filter == "terminated":
        filters.append(Employee.status == EMPLOYEE_STATUS_TERMINATED)
    elif status_filter != "all":
        filters.append(Employee.status != EMPLOYEE_STATUS_TERMINATED)
    if q:
        like = f"%{_escape_like(q)}%"
        legs = [
            Employee.staff_id.ilike(like, escape="\\"),
            Employee.employee_name.ilike(like, escape="\\"),
        ]
        # NRIC too — the Dependants tab has always matched on it and this one
        # did not, so the same search text found a person on one tab only.
        # Normalized both sides, so typed punctuation still matches.
        nric = normalize_nric(q)
        if looks_like_nric(nric):
            legs.append(
                Employee.national_id_normalized.ilike(
                    f"%{_escape_like(nric)}%", escape="\\"
                )
            )
        filters.append(or_(*legs))
    if match_status == "matched":
        filters.append(Employee.matched_category_id.isnot(None))
    elif match_status == "unmatched":
        filters.append(Employee.matched_category_id.is_(None))

    total = db.scalar(select(func.count(Employee.id)).where(*filters)) or 0

    rows = list(
        db.execute(
            select(Employee)
            .where(*filters)
            .order_by(Employee.staff_id)
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    plans_by_emp = _hydrate_plans(rows, db, policy_year_id)

    items: list[EmployeeOut] = []
    for emp in rows:
        out = EmployeeOut.model_validate(emp)
        out.matched_plans = plans_by_emp.get(emp.id, [])
        items.append(out)

    return EmployeeList(
        total=total,
        offset=offset,
        limit=limit,
        items=items,
    )


@router.get("/coverage-summary", response_model=CoverageSummary)
def coverage_summary(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverageSummary:
    """Whole-roster product-coverage summary for the benefit-statement picker.

    Lightweight (no SOB hydration) so the entire policy year fits one response,
    letting the client filter by product count + search without server paging.
    """
    assert_policy_year_for_user(policy_year_id, user, db)
    items = build_coverage_items(db, policy_year_id)
    return CoverageSummary(total=len(items), items=items)


# The four-column `/coverage-summary/export` (staff ID, name, product count,
# product names) was deleted: every column of it is in the employee coverage
# report below, which also carries the resolved plans, financials and flex —
# so the two sheets were the same artifact at two depths, on two pages.


@router.get("/coverage-report/export")
def employee_coverage_report(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Employee listing with resolved insurance + flex coverage (.xlsx).

    One row per active employee, NRIC masked. Audited because it emits PII.
    """
    assert_policy_year_for_user(policy_year_id, user, db)
    wb = build_employee_report_workbook(db, policy_year_id)
    buf = BytesIO()
    wb.save(buf)
    write_audit(
        db, user, action="export", entity_type="employee_coverage_report",
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
                'attachment; filename="employee-coverage.xlsx"'
            )
        },
    )


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(
    e: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    out = EmployeeOut.model_validate(e)
    plans = _hydrate_plans([e], db, e.policy_year_id)
    out.matched_plans = plans.get(e.id, [])
    return out


@router.get("/{employee_id}/benefit-statement", response_model=BenefitStatementOut)
def get_benefit_statement(
    e: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> BenefitStatementOut:
    """Read-only, benefits-only coverage statement for one employee.

    Assembles the employee's resolved plan + Schedule of Benefits per product
    and derives which of their dependants are covered. Premiums/financials are
    deliberately omitted (see ``services/benefit_statement``).
    """
    return build_benefit_statement(db, e)


@router.get("/{employee_id}/utilization", response_model=UtilizationOut)
def get_employee_utilization(
    e: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> UtilizationOut:
    """Claim usage vs limits for one employee (computed on read) — the broker
    view of the same numbers the member sees on /portal/utilization."""
    return build_utilization(db, e)


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    payload: EmployeePatch,
    e: Employee = Depends(load_employee),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    """Edit an employee's name / raw attributes.

    Raw attributes are replaced wholesale (the UI sends the full set), then
    derived attributes are recomputed so downstream views stay consistent. The
    match assignment is left untouched — run matching to re-evaluate.
    """
    before = {
        "employee_name": e.employee_name,
        "attribute_values": e.attribute_values,
    }
    if payload.employee_name is not None:
        e.employee_name = payload.employee_name
    if payload.attribute_values is not None:
        e.attribute_values = payload.attribute_values
        # The NRIC is the durable dedup key on the next upload — recompute it
        # so a corrected NRIC here can't leave a stale key that duplicates or
        # false-matches the person on re-import.
        e.national_id_normalized = employee_nric(e.attribute_values or {})
        schemas = list(
            db.execute(
                select(EmployeeAttributeSchema).where(
                    tenant_or_global(EmployeeAttributeSchema.client_id, e.client_id)
                )
            ).scalars()
        )
        e.derived_attribute_values = derive(e.attribute_values or {}, schemas)
    write_audit(
        db, user, action="update", entity_type="employee", entity_id=e.id,
        before=before,
        after={"employee_name": e.employee_name, "attribute_values": e.attribute_values},
    )
    db.commit()
    db.refresh(e)
    out = EmployeeOut.model_validate(e)
    out.matched_plans = _hydrate_plans([e], db, e.policy_year_id).get(e.id, [])
    return out


def _enrollment_risk(
    db: Session, client_id: str, policy_year_id: str, employees: list[Employee]
) -> dict[str, int]:
    """Data a roster wipe would cascade-delete beyond the bare employee rows.

    ``Enrollment``/``EnrollmentElection``/``LeaveElection``/``Claim``/
    ``EmployeePlanOverride`` all cascade from ``Employee`` (ondelete="CASCADE"),
    so deleting the roster silently destroys any submitted/confirmed elections,
    leave trades, member claims (plus their retained receipt blobs), and active
    coverage overrides with them — not just the employee rows.

    Orphan overrides (product no longer in the member's cohort) are inert — the
    resolver skips them — so they're excluded here: warning about them would
    force a confirm on the routine re-match-then-re-upload flow to protect data
    that changes nothing.
    """
    enrollments_at_risk = db.execute(
        select(func.count(Enrollment.id)).where(
            Enrollment.client_id == client_id,
            Enrollment.policy_year_id == policy_year_id,
            Enrollment.status != EnrollmentStatus.not_started,
        )
    ).scalar_one()
    leave_at_risk = db.execute(
        select(func.count(LeaveElection.id)).where(
            LeaveElection.client_id == client_id,
            LeaveElection.policy_year_id == policy_year_id,
        )
    ).scalar_one()
    claims_at_risk = db.execute(
        select(func.count(Claim.id)).where(
            Claim.client_id == client_id,
            Claim.policy_year_id == policy_year_id,
        )
    ).scalar_one()
    # Live coverage overrides only — orphans are inert (see docstring).
    all_overrides = load_overrides(db, policy_year_id, [e.id for e in employees])
    orphan_ids = {o.id for o in find_orphan_overrides(db, policy_year_id, employees)}
    overrides_at_risk = sum(1 for o in all_overrides.values() if o.id not in orphan_ids)
    return {
        "enrollments_at_risk": enrollments_at_risk,
        "leave_elections_at_risk": leave_at_risk,
        "claims_at_risk": claims_at_risk,
        "overrides_at_risk": overrides_at_risk,
    }


_RISK_LABELS: list[tuple[str, str]] = [
    ("enrollments_at_risk", "in-progress/confirmed enrollment(s)"),
    ("leave_elections_at_risk", "leave election(s)"),
    ("claims_at_risk", "member claim(s) with retained receipts"),
    ("overrides_at_risk", "active coverage override(s)"),
]


@router.delete("", response_model=dict)
@limiter.limit("10/minute")
def bulk_delete_employees(
    request: Request,
    policy_year_id: str,
    confirm: bool = False,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Delete every employee for a policy year. Cascades to dependants,
    enrollments/elections, leave elections, claims, and plan overrides.

    409s with code ``enrollment_data_at_risk`` (unless ``confirm=true``) when the
    roster carries in-progress/confirmed enrollments, leave elections, member
    claims, or active coverage overrides, so a broker can't silently wipe that
    data — e.g. by clearing the roster before re-uploading it.
    """
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    rows = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
            )
        )
        .scalars()
        .all()
    )
    if rows and not confirm:
        risk = _enrollment_risk(db, client_id, policy_year_id, rows)
        if any(risk.values()):
            parts = [
                f"{risk[key]} {label}" for key, label in _RISK_LABELS if risk[key]
            ]
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "enrollment_data_at_risk",
                    "message": (
                        "Deleting these employees will permanently destroy "
                        + ", ".join(parts)
                        + ". Pass confirm=true to proceed anyway."
                    ),
                    **risk,
                },
            )
    deleted = len(rows)
    linked_account_ids = {r.member_account_id for r in rows if r.member_account_id}
    for r in rows:
        db.delete(r)
    db.flush()

    # Portal accounts whose ONLY employee link was just deleted must fail
    # closed: without this, the member keeps a working sign-in that resolves
    # to nothing (permanent "no coverage" screens) and the broker gets no
    # signal. Accounts still linked from another policy year are kept.
    accounts_disabled = 0
    if linked_account_ids:
        still_linked = set(
            db.execute(
                select(Employee.member_account_id).where(
                    Employee.member_account_id.in_(linked_account_ids)
                )
            ).scalars()
        )
        stranded = linked_account_ids - still_linked
        if stranded:
            accounts = db.execute(
                select(MemberAccount).where(
                    MemberAccount.id.in_(stranded),
                    MemberAccount.status != MEMBER_STATUS_DISABLED,
                )
            ).scalars().all()
            for account in accounts:
                account.status = MEMBER_STATUS_DISABLED
                accounts_disabled += 1

    write_audit(
        db,
        user,
        action="bulk_delete",
        entity_type="employees",
        entity_id=None,
        after={
            "deleted": deleted,
            "policy_year_id": policy_year_id,
            "member_accounts_disabled": accounts_disabled,
        },
    )
    db.commit()
    return {"deleted": deleted, "member_accounts_disabled": accounts_disabled}


@router.post("/upload", response_model=UploadResult)
@limiter.limit("20/minute")
async def upload_employees(
    request: Request,
    file: Annotated[UploadFile, File()],
    policy_year_id: Annotated[str, Form()],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadResult:
    client_id = require_client_id(user)
    py = assert_policy_year_for_user(policy_year_id, user, db)
    # Invariant: an employee's client must own the policy year it's loaded into.
    # Holds for tenant users by construction; this guards the system_admin path,
    # where user_owns bypasses the cross-client check above.
    if py.client_id != client_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Policy year belongs to a different client than the active one.",
        )

    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        records = parse_employee_workbook(tmp_path)

    inserted = 0
    errors: list[str] = []
    warnings: list[str] = []
    duplicates: list[DuplicateEntry] = []

    # Checked across EVERY parsed row, duplicates included: a mistyped NRIC is
    # worth reporting whether or not that row was the one imported.
    nric_warning = suspect_nric_warning(employee_nric(r.attributes) for r in records)
    if nric_warning:
        warnings.append(nric_warning)

    # Existing identity keys in this policy year (both NRIC and staff), including
    # terminated leavers — re-adding a terminated person's exact roster row is a
    # duplicate (the ADC flow re-activates them intentionally).
    existing_keys: dict[str, str] = {}
    for eid, sid, nid in db.execute(
        select(
            Employee.id, Employee.staff_id, Employee.national_id_normalized
        ).where(
            Employee.client_id == client_id,
            Employee.policy_year_id == policy_year_id,
        )
    ).all():
        if nid:
            existing_keys.setdefault(f"nric:{nid}", eid)
        if sid:
            existing_keys.setdefault(f"staff:{str(sid).strip().lower()}", eid)

    seen: set[str] = set()
    for rec in records:
        keys = employee_candidate_keys(rec.attributes, rec.staff_id)
        existing_hit = next((existing_keys[k] for k in keys if k in existing_keys), None)
        in_file_hit = any(k in seen for k in keys)
        if existing_hit or in_file_hit:
            duplicates.append(
                DuplicateEntry(
                    row=rec.row,
                    name=rec.employee_name,
                    staff_id=rec.staff_id,
                    nric_masked=mask_nric(employee_nric(rec.attributes)) or None,
                    reason="existing" if existing_hit else "in_file",
                    existing_id=existing_hit,
                )
            )
            continue
        seen.update(keys)
        db.add(
            Employee(
                client_id=client_id,
                policy_year_id=policy_year_id,
                staff_id=rec.staff_id,
                employee_name=rec.employee_name,
                attribute_values=rec.attributes,
                derived_attribute_values={},
                national_id_normalized=employee_nric(rec.attributes),
                source="csv_import",
            )
        )
        inserted += 1

    skipped = len(duplicates)
    write_audit(
        db,
        user,
        action="upload",
        entity_type="employees",
        entity_id=None,
        after={"inserted": inserted, "skipped": skipped, "filename": file.filename},
    )
    db.commit()

    # Auto-run matching so the user sees results immediately. Failure here
    # must not roll back the upload — the audit row above already records
    # the persisted rows. Matching can always be re-run from the UI button.
    try:
        summary = match_policy_year(db, policy_year_id, user)
        write_audit(
            db,
            user,
            action="run_matching",
            entity_type="policy_year",
            entity_id=policy_year_id,
            after={
                "employees_total": summary.employees_total,
                "employees_matched": summary.employees_matched,
                "by_method": summary.by_method,
                "duration_ms": summary.duration_ms,
                "errors": summary.errors,
                "trigger": "auto_on_upload",
            },
        )
        db.commit()
        if summary.errors:
            errors.append(
                f"{summary.errors} employee(s) hit a matching error (distinct "
                "from unmatched) — re-run matching or check the logs."
            )
    except Exception:
        db.rollback()
        logger.exception("auto-matching after upload failed")
        errors.append("Auto-matching failed; click 'Re-run matching' to retry.")

    # Refresh Flex wallets too when a confirmed scheme exists — family status is
    # roster-derived, so new staff need wallets sized. Best-effort, like matching.
    assign_flex_safe(
        db, user, policy_year_id, client_id,
        trigger="auto_on_employee_upload", errors=errors,
    )

    return UploadResult(
        inserted=inserted, skipped=skipped, errors=errors, warnings=warnings,
        duplicates=duplicates,
    )
