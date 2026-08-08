"""Downloadable insurer reports (the Reports page).

Every download emits PII, so each one is audit-logged with the report type and
masking choice. Unmasked NRIC/FIN is for insurer submission only — gated to
write-capable roles (broker viewers always get masked output).
"""
from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import ROLE_BROKER_VIEWER, CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import PolicyYear
from app.services.built_in_listings import normalize_employee_status
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
from app.services.report_workbooks import (
    WORKBOOKS,
    BuildContext,
    build_workbook,
    spec_for,
    workbook_filename,
)


def _slug(insurer: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in insurer.lower()).strip("-")

router = APIRouter(prefix="/policy-years/{policy_year_id}/reports", tags=["reports"])

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def assert_masking_allowed(user: CurrentUser, masked: bool) -> None:
    """Only write-access roles may pull unmasked NRIC/FIN.

    Public because `report_versions` applies the same rule to a RETAINED blob —
    the identical PII reached by a different route. Keep it as the single
    implementation so the live and retained paths cannot drift apart.
    """
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
    assert_masking_allowed(user, masked)
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
    assert_masking_allowed(user, masked=False)
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
) -> dict[str, Any]:
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
    assert_masking_allowed(user, masked)
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
    assert_masking_allowed(user, masked)
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


# Longest activity span one request may pull. `auth_events` is high-volume and
# append-only across every surface, so an unbounded range is a way to ask the
# server to materialize years of rows into one workbook.
_MAX_ACTIVITY_DAYS = 366


def _activity_range(start: date | None, end: date | None) -> tuple[date, date]:
    """Validate + default an inclusive activity date range.

    Defaults to the last 30 days ending today — the question these sheets are
    opened to answer is almost always "recently", and a default of "everything"
    would make the first click the slowest one.
    """
    resolved_end = end or date.today()
    resolved_start = start or (resolved_end - timedelta(days=30))
    if resolved_start > resolved_end:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "start must not be after end."
        )
    if (resolved_end - resolved_start).days + 1 > _MAX_ACTIVITY_DAYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Date range must not exceed {_MAX_ACTIVITY_DAYS} days.",
        )
    return resolved_start, resolved_end


@router.get("/workbooks")
def list_report_workbooks(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """The composite workbooks this year can produce, and what is in each.

    The SHEET LIST is served, not a frontend constant: adding a sheet must not
    require a matching edit in TypeScript to be described, or the page ends up
    naming a workbook's contents wrongly — which is worse than not naming them,
    because a broker files against what the page said was inside.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    insurers = configured_insurers_for_year(db, py)
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "requires_insurer": spec.requires_insurer,
            "supports_masking": spec.supports_masking,
            "supports_date_range": spec.supports_date_range,
            "supports_employee_status": spec.supports_employee_status,
            # Served, never derived client-side: the picker must offer exactly
            # the insurers the download will accept.
            "insurers": insurers if spec.requires_insurer else [],
            "sheets": [
                {"title": s.title, "description": s.description}
                for s in spec.sheets
            ],
        }
        for spec in WORKBOOKS.values()
    ]


@router.get("/workbooks/{workbook_key}")
@limiter.limit("20/minute")
def download_report_workbook(
    request: Request,
    policy_year_id: str,
    workbook_key: str,
    insurer: str | None = Query(default=None),
    masked: bool = True,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    employee_status: str = Query(default="all"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """One submission, one workbook — every sheet named for what it holds.

    Replaces the zip "report sets": a zip of five workbooks and a workbook of
    five sheets carry the same bytes, but only the second can be read and
    cross-referenced in place, and only the second keeps the sheet names
    attached to the file once it is forwarded on.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    spec = spec_for(workbook_key)
    if spec is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown report workbook {workbook_key!r}."
        )
    # Only gate masking on a workbook that actually carries an identification
    # number. Refusing an unmasked pull of a sheet with no NRIC on it would
    # block a viewer from a report there is nothing to protect in.
    if spec.supports_masking:
        assert_masking_allowed(user, masked)
    if spec.requires_insurer:
        if not insurer:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{spec.label} is produced per insurer — name one.",
            )
        # Same gate the single listings use: a typo'd insurer would otherwise
        # yield a real-looking workbook with every coverage column blank.
        _require_configured_insurer(db, py, insurer)
    lo = hi = None
    if spec.supports_date_range:
        lo, hi = _activity_range(start, end)
    wanted_status = normalize_employee_status(employee_status)
    wb = build_workbook(
        db,
        py,
        spec,
        BuildContext(
            masked=masked,
            insurer=insurer,
            start=lo,
            end=hi,
            employee_status=wanted_status,
        ),
    )
    write_audit(
        db, user, action="export", entity_type="report_workbook",
        entity_id=policy_year_id,
        after={
            "workbook": spec.key,
            "insurer": insurer,
            "masked": masked,
            "start": lo.isoformat() if lo else None,
            "end": hi.isoformat() if hi else None,
            "employee_status": (
                wanted_status if spec.supports_employee_status else None
            ),
        },
    )
    db.commit()
    return _xlsx_response(wb, workbook_filename(spec, py, insurer))

