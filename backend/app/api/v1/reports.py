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
from app.services.activity_reports import (
    build_company_activity_workbook,
    build_portal_activity_workbook,
)
from app.services.built_in_listings import (
    build_built_in_dependant_listing,
    build_built_in_employee_listing,
    built_in_filename,
    normalize_employee_status,
)
from app.services.claims_reports import (
    build_employee_claims_workbook,
    build_insurance_claims_workbook,
    normalize_scope,
)
from app.services.flex_ledger import (
    build_utilisation_summary_workbook,
    build_utilisation_workbook,
)
from app.services.insurer_listings import (
    build_dependant_listing,
    build_employee_listing,
    build_readiness,
    configured_insurers_for_year,
)
from app.services.insurer_reports import build_benefit_selection_workbook
from app.services.leaver_reports import (
    build_leaver_details_workbook,
    build_leaver_summary_workbook,
)
from app.services.member_listing_template import build_member_listing_template
from app.services.placement_slip_export import (
    build_placement_slip_workbook,
    build_quotation_slip_workbook,
)
from app.services.portal_access_report import build_portal_access_workbook
from app.services.report_bundles import (
    BUNDLES,
    build_bundle,
    bundle_filename,
    spec_for,
)
from app.services.underwriting import adopt_orphan_cases
from app.services.underwriting_report import build_underwriting_report


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


@router.get("/built-in-employee-listing")
@limiter.limit("20/minute")
def download_built_in_employee_listing(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    employee_status: str = Query(default="all"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Full-company employee listing across every insurer (.xlsx, internal)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wanted = normalize_employee_status(employee_status)
    wb = build_built_in_employee_listing(
        db, py, masked=masked, employee_status=wanted
    )
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={
            "report": "built-in-employee-listing",
            "masked": masked,
            "employee_status": wanted,
        },
    )
    db.commit()
    return _xlsx_response(wb, built_in_filename("employee"))


@router.get("/built-in-dependant-listing")
@limiter.limit("20/minute")
def download_built_in_dependant_listing(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    employee_status: str = Query(default="all"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Full-company dependant listing (.xlsx, internal)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wanted = normalize_employee_status(employee_status)
    wb = build_built_in_dependant_listing(
        db, py, masked=masked, employee_status=wanted
    )
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={
            "report": "built-in-dependant-listing",
            "masked": masked,
            "employee_status": wanted,
        },
    )
    db.commit()
    return _xlsx_response(wb, built_in_filename("dependant"))


@router.get("/underwriting")
@limiter.limit("20/minute")
def download_underwriting_report(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Underwriting case register across every insurer (.xlsx, internal).

    Deliberately NOT insurer-scoped: this is the broker's own working record,
    and one member is usually underwritten with several insurers at once.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    # Same lazy adoption the queue GET performs: rows written before the
    # insurer-grouped model carry no review, so without it their workflow
    # status would export blank.
    if adopt_orphan_cases(db, policy_year_id):
        db.commit()
    wb = build_underwriting_report(db, py, masked=masked)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "underwriting", "masked": masked},
    )
    db.commit()
    return _xlsx_response(
        wb, f"underwriting-report-{py.year}-{date.today():%Y%m%d}.xlsx"
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


@router.get("/bundles")
def list_report_bundles(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """The bundles this year can produce, with which insurers apply."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    insurers = configured_insurers_for_year(db, py)
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "requires_insurer": spec.requires_insurer,
            "file_count": len(spec.members),
            # Served, never derived client-side: the picker must offer exactly
            # the insurers the download will accept.
            "insurers": insurers if spec.requires_insurer else [],
        }
        for spec in BUNDLES.values()
    ]


@router.get("/bundles/{bundle_key}")
@limiter.limit("20/minute")
def download_report_bundle(
    request: Request,
    policy_year_id: str,
    bundle_key: str,
    insurer: str | None = Query(default=None),
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """One click, one submission — every file in the bundle as a .zip."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    spec = spec_for(bundle_key)
    if spec is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown report bundle {bundle_key!r}."
        )
    if spec.requires_insurer:
        if not insurer:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{spec.label} is produced per insurer — name one.",
            )
        # Same gate the single listings use: a typo'd insurer would otherwise
        # yield a real-looking zip with every coverage column blank.
        _require_configured_insurer(db, py, insurer)
    payload = build_bundle(db, py, spec, masked=masked, insurer=insurer)
    write_audit(
        db, user, action="export", entity_type="report_bundle",
        entity_id=policy_year_id,
        after={"bundle": spec.key, "insurer": insurer, "masked": masked},
    )
    db.commit()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{bundle_filename(spec, insurer)}"'
            )
        },
    )


@router.get("/leaver-summary")
@limiter.limit("20/minute")
def download_leaver_summary(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Leavers with their cover window and final wallet position (.xlsx)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wb = build_leaver_summary_workbook(db, py, masked=masked)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "leaver-summary", "masked": masked},
    )
    db.commit()
    return _xlsx_response(wb, f"leaver-summary-report-{date.today():%Y%m%d}.xlsx")


@router.get("/leaver-details")
@limiter.limit("20/minute")
def download_leaver_details(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Every claim belonging to a leaver (.xlsx)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wb = build_leaver_details_workbook(db, py, masked=masked)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "leaver-details", "masked": masked},
    )
    db.commit()
    return _xlsx_response(wb, f"leaver-details-report-{date.today():%Y%m%d}.xlsx")


@router.get("/wallet-utilisation")
@limiter.limit("20/minute")
def download_wallet_utilisation(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Flex wallet ledger — one row per dated movement (.xlsx)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wb = build_utilisation_workbook(db, py, masked=masked)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "wallet-utilisation", "masked": masked},
    )
    db.commit()
    return _xlsx_response(wb, f"utilisation-report-{date.today():%Y%m%d}.xlsx")


@router.get("/wallet-utilisation-summary")
@limiter.limit("20/minute")
def download_wallet_utilisation_summary(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Flex wallet position — one row per member (.xlsx)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wb = build_utilisation_summary_workbook(db, py, masked=masked)
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={"report": "wallet-utilisation-summary", "masked": masked},
    )
    db.commit()
    return _xlsx_response(
        wb, f"utilisation-summary-report-{date.today():%Y%m%d}.xlsx"
    )


@router.get("/insurance-claims")
@limiter.limit("20/minute")
def download_insurance_claims(
    request: Request,
    policy_year_id: str,
    scope: str = Query(default="all"),
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Insurance claims in the year (.xlsx) — all / inpatient / outpatient."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wanted = normalize_scope(scope)
    wb = build_insurance_claims_workbook(db, py, scope=wanted, masked=masked)
    write_audit(
        db, user, action="export", entity_type="claims_register",
        entity_id=policy_year_id,
        after={"report": "insurance-claims", "scope": wanted, "masked": masked},
    )
    db.commit()
    prefix = {
        "all": "all-insurance-claims",
        "inpatient": "inpatient-claims",
        "outpatient": "outpatient-claims",
    }[wanted]
    return _xlsx_response(
        wb, f"{prefix}-in-benefit-year-{date.today():%Y%m%d}.xlsx"
    )


@router.get("/employee-claims")
@limiter.limit("20/minute")
def download_employee_claims(
    request: Request,
    policy_year_id: str,
    masked: bool = True,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Every claim in the year, flex and insured together (.xlsx)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    assert_masking_allowed(user, masked)
    wb = build_employee_claims_workbook(db, py, masked=masked)
    write_audit(
        db, user, action="export", entity_type="claims_register",
        entity_id=policy_year_id,
        after={"report": "employee-claims", "masked": masked},
    )
    db.commit()
    return _xlsx_response(
        wb, f"employee-claims-in-benefit-year-{date.today():%Y%m%d}.xlsx"
    )


# Longest activity span one request may pull. `auth_events` is high-volume and
# append-only across every surface, so an unbounded range is a way to ask the
# server to materialize years of rows into one workbook.
_MAX_ACTIVITY_DAYS = 366


def _activity_range(start: date | None, end: date | None) -> tuple[date, date]:
    """Validate + default an inclusive activity date range.

    Defaults to the last 30 days ending today — the question these reports are
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


@router.get("/portal-activity")
@limiter.limit("20/minute")
def download_portal_activity(
    request: Request,
    policy_year_id: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Sign-in activity across every surface, for a date range (.xlsx).

    No NRIC masking gate: the sheet carries names and staff ids but no
    identification numbers, so there is nothing for the toggle to govern.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    lo, hi = _activity_range(start, end)
    wb = build_portal_activity_workbook(db, py, lo, hi)
    write_audit(
        db, user, action="export", entity_type="activity_report",
        entity_id=policy_year_id,
        after={
            "report": "portal-activity",
            "start": lo.isoformat(),
            "end": hi.isoformat(),
        },
    )
    db.commit()
    return _xlsx_response(
        wb, f"portal-login-activity-report-{lo:%Y%m%d}-{hi:%Y%m%d}.xlsx"
    )


@router.get("/company-activity")
@limiter.limit("20/minute")
def download_company_activity(
    request: Request,
    policy_year_id: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Configuration + administration activity for a date range (.xlsx)."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    lo, hi = _activity_range(start, end)
    wb = build_company_activity_workbook(db, py, lo, hi)
    write_audit(
        db, user, action="export", entity_type="activity_report",
        entity_id=policy_year_id,
        after={
            "report": "company-activity",
            "start": lo.isoformat(),
            "end": hi.isoformat(),
        },
    )
    db.commit()
    return _xlsx_response(
        wb, f"company-activity-report-{lo:%Y%m%d}-{hi:%Y%m%d}.xlsx"
    )


@router.get("/portal-access")
@limiter.limit("20/minute")
def download_portal_access(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Roster beside its portal accounts (.xlsx) — provisioning + sign-in state.

    Carries no identification numbers, so no masking gate.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    wb = build_portal_access_workbook(db, py)
    write_audit(
        db, user, action="export", entity_type="activity_report",
        entity_id=policy_year_id, after={"report": "portal-access"},
    )
    db.commit()
    return _xlsx_response(wb, f"portal-access-report-{date.today():%Y%m%d}.xlsx")
