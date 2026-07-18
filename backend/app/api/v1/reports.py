"""Downloadable insurer reports (the Reports page).

Every download emits PII, so each one is audit-logged with the report type and
masking choice. Unmasked NRIC/FIN is for insurer submission only — gated to
write-capable roles (broker viewers always get masked output).
"""
from __future__ import annotations

from datetime import date
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import ROLE_BROKER_VIEWER, CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import PolicyYear
from app.services.insurer_listings import (
    build_dependant_listing,
    build_employee_listing,
    build_readiness,
    configured_insurers_for_year,
)
from app.services.insurer_reports import build_benefit_selection_workbook
from app.services.member_listing_template import build_member_listing_template
from app.services.placement_slip_export import (
    build_placement_slip_workbook,
    build_quotation_slip_workbook,
)


def _slug(insurer: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in insurer.lower()).strip("-")

router = APIRouter(prefix="/policy-years/{policy_year_id}/reports", tags=["reports"])

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _assert_masking_allowed(user: CurrentUser, masked: bool) -> None:
    if not masked and user.role == ROLE_BROKER_VIEWER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Unmasked NRIC/FIN downloads require write access.",
        )


def _require_configured_insurer(db: Session, py: PolicyYear, insurer: str) -> None:
    """Reject a blank/unknown insurer so a typo can't produce a real-looking
    listing with every coverage column silently missing (misleading-empty
    report to the insurer). Match case-insensitively against configured names."""
    wanted = (insurer or "").strip().lower()
    known = {i.lower() for i in configured_insurers_for_year(db, py)}
    if not wanted or wanted not in known:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No products are assigned to insurer {insurer!r} for this policy year.",
        )


def _xlsx_response(wb, filename: str) -> Response:
    buf = BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/benefit-selection")
@limiter.limit("20/minute")
def download_benefit_selection_report(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    window_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Benefit-selection status + buy/sell-leave report (.xlsx)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    _assert_masking_allowed(user, masked)
    wb = build_benefit_selection_workbook(
        db, py, masked=masked, window_id=window_id
    )
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={
            "report": "benefit-selection",
            "masked": masked,
            "window_id": window_id,
        },
    )
    db.commit()
    return _xlsx_response(
        wb,
        "benefit-selection-status-with-buy-sell-leave-report-"
        f"{date.today():%Y%m%d}.xlsx",
    )


@router.get("/member-listing-template")
@limiter.limit("20/minute")
def download_member_listing_template(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Full member-listing upload template (Employees + Dependants sheets).

    Pre-filled with the current roster (unmasked — it round-trips through the
    upload parser) so it also serves as an update template; audited like every
    PII export.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    _assert_masking_allowed(user, masked=False)
    wb = build_member_listing_template(db, py)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "member-listing-template", "masked": False},
    )
    db.commit()
    return _xlsx_response(wb, "member-listing-template.xlsx")


@router.get("/placement-slip")
@limiter.limit("20/minute")
def download_placement_slip_export(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Placement-slip style export of the configured products (.xlsx).

    Config-only (categories, rates, plans, SOB) — no member PII, so no masking
    gate; still audited like every export.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    wb = build_placement_slip_workbook(db, py)
    write_audit(
        db, user, action="export", entity_type="placement_slip",
        entity_id=policy_year_id, after={"report": "placement-slip"},
    )
    db.commit()
    return _xlsx_response(
        wb, f"placement-slip-{py.year}-{date.today():%Y%m%d}.xlsx"
    )


@router.get("/quotation-slip")
@limiter.limit("20/minute")
def download_quotation_slip_export(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Quotation-slip export (.xlsx) — the shopping document that accompanies
    the Fact-Find form. Same structure as the placement slip, but the insurer
    and every rate/premium cell are left blank for the quoting insurer.
    Config-only (no member PII), audited like every export.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    wb = build_quotation_slip_workbook(db, py)
    write_audit(
        db, user, action="export", entity_type="placement_slip",
        entity_id=policy_year_id, after={"report": "quotation-slip"},
    )
    db.commit()
    return _xlsx_response(
        wb, f"quotation-slip-{py.year}-{date.today():%Y%m%d}.xlsx"
    )


@router.get("/readiness")
def report_readiness(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Config gaps blocking the insurer listings (missing insurers/labels/IDs)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    return build_readiness(db, py)


@router.get("/employee-listing")
@limiter.limit("20/minute")
def download_employee_listing(
    request: Request,
    policy_year_id: str,
    insurer: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Per-insurer employee membership listing (.xlsx, insurer template)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    _assert_masking_allowed(user, masked)
    _require_configured_insurer(db, py, insurer)
    wb = build_employee_listing(db, py, insurer, masked=masked)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "employee-listing", "insurer": insurer, "masked": masked},
    )
    db.commit()
    return _xlsx_response(
        wb,
        f"employee-listing-for-insurer-report-{_slug(insurer)}-"
        f"{date.today():%Y%m%d}.xlsx",
    )


@router.get("/dependant-listing")
@limiter.limit("20/minute")
def download_dependant_listing(
    request: Request,
    policy_year_id: str,
    insurer: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Per-insurer dependant listing (.xlsx, insurer template)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    _assert_masking_allowed(user, masked)
    _require_configured_insurer(db, py, insurer)
    wb = build_dependant_listing(db, py, insurer, masked=masked)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "dependant-listing", "insurer": insurer, "masked": masked},
    )
    db.commit()
    return _xlsx_response(
        wb,
        f"dependant-listing-for-insurer-report-{_slug(insurer)}-"
        f"{date.today():%Y%m%d}.xlsx",
    )
