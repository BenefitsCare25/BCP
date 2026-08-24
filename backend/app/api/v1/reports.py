"""Downloadable insurer reports (the Reports page).

Every download emits PII, so each one is audit-logged with the report type,
masking choice and the user who pulled it. Unmasked NRIC/FIN is for insurer
submission only — gated to write-capable roles (broker viewers always get masked
output).

**A download of a submission-grade report also RETAINS what it produced**
(`_retain_download`, 2026-08-08). The audit row records that someone pulled a
file; it cannot reproduce the file, because the report is derived from live data
and the roster is overwritten in place by the listing sync. Those are different
questions — "who sent this" and "what did we send" — and only the second one an
insurer disputes. Retention used to be a separate button a broker had to
remember, so the archive held whatever anybody thought to press Save on and
nothing about the rest. See `report_registry.RETAINED_ON_DOWNLOAD` for which
reports retain and why the internal registers do not.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import ROLE_BROKER_VIEWER, CurrentUser, get_current_user
from app.core.clock import today as business_today
from app.core.deps import assert_policy_year_for_user
from app.core.rate_limit import limiter
from app.core.storage import get_storage
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
    build_quotation_slip_archive,
)
from app.services.report_registry import (
    RETENTION_KEEP,
    retained_type_for,
)
from app.services.report_registry import (
    scope_key_for as version_scope_key_for,
)
from app.services.report_registry import (
    spec_for as version_spec_for,
)
from app.services.report_versions import create_version, prune_series
from app.services.report_workbooks import (
    WORKBOOKS,
    BuildContext,
    build_workbook,
    spec_for,
    workbook_filename,
)

log = logging.getLogger(__name__)


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


def _xlsx_bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_response(wb: Workbook, filename: str) -> Response:
    return _bytes_response(_xlsx_bytes(wb), filename)


def _bytes_response(
    data: bytes,
    filename: str,
    retained: dict[str, Any] | None = None,
    media_type: str = _XLSX_MEDIA_TYPE,
) -> Response:
    """The file, plus what its retention did.

    The outcome rides a HEADER because the body is the workbook. Without it the
    page can only re-read the record line, which is unchanged both when nothing
    needed filing and when filing failed — and "the roster moved" fires on any
    roster or config edit while filing compares the report's BYTES, so a change
    that does not reach this insurer leaves a badge showing with no way to learn
    why downloading did not clear it. Same-origin (the SPA ships inside this
    image), so no CORS exposure header is needed.
    """
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if retained:
        if retained.get("retention_error"):
            headers["X-Inspro-Report-Filed"] = "error"
        elif retained.get("version_created"):
            headers["X-Inspro-Report-Filed"] = f"v{retained['version_no']}"
        else:
            headers["X-Inspro-Report-Filed"] = f"unchanged:v{retained['version_no']}"
    return Response(content=data, media_type=media_type, headers=headers)


def _retain_download(
    db: Session,
    user: CurrentUser,
    py: PolicyYear,
    download_key: str,
    params: dict[str, Any],
    data: bytes,
) -> tuple[dict[str, Any], list[str]]:
    """Retain the bytes this download is about to stream, if it is that kind of
    report AND this pull is a submission. Returns ``(audit_fields, blobs)``.

    **Only an UNMASKED pull by a write-capable user is retained**, and both
    halves of that are load-bearing:

    - *Unmasked* is what makes the archive mean one thing. The masked copy is an
      internal preview — an insurer matches members on the identification
      number, which is why this module gates unmasked output as "for insurer
      submission only" in the first place. Retaining previews put them in the
      same numbered series, so "Last sent v5" could name a file nobody sent, and
      a preview pulled after a roster change CLEARED the changed-since badge —
      telling the broker the insurer holds a roster it has never seen. They also
      shared the 24-slot retention budget, so previews evicted real submissions.
    - *Write-capable* because retention WRITES a row and a blob and
      ``prune_series`` DELETES the oldest — through a GET.
      ``deps.require_write_access`` only blocks non-read methods, so without
      this the role documented as "read-only across the entire API surface"
      mutated and destroyed the submission archive. The masking rule already
      403s a viewer's unmasked pull, so this is belt-and-braces — but the
      invariant is "a read-only role never writes", and it should not depend on
      a masking rule that could be relaxed later.

    (The rejected alternative was to retain both and prune per masking bucket,
    reading "last sent" off the newest unmasked row. It keeps more data, but the
    data is previews — roster PII in blob storage, serving nothing — and it puts
    two meanings in one series.)

    **Never raises.** A broker's file must not be withheld because the archive
    could not be written — storage being unreachable is our problem, and the
    report they asked for is derived from data they can see on screen. The
    failure is logged and named in the audit row instead, and because staleness
    is measured against the newest retained version, a failed retention leaves
    the row reading "changed since" rather than silently claiming to be filed.

    Retention is deduplicated on content, so repeatedly downloading an unchanged
    report reuses one version and writes no further blobs — the archive grows
    with data changes, not with clicks.
    """
    report_type = retained_type_for(download_key)
    if report_type is None:
        return {}, []
    if params.get("masked", True) or user.role == ROLE_BROKER_VIEWER:
        return {}, []
    try:
        rv, created, _superseded = create_version(
            db, user, py, report_type, params, blob_bytes=data
        )
        pruned = prune_series(
            db, py, report_type,
            version_scope_key_for(version_spec_for(report_type), params),
            RETENTION_KEEP,
        )
        return (
            {
                "report_version_id": rv.id,
                "version_no": rv.version_no,
                "version_created": created,
            },
            pruned,
        )
    except Exception:
        log.exception(
            "Retaining %s for policy year %s failed; serving the download anyway.",
            report_type, py.id,
        )
        db.rollback()
        return {"retention_error": True}, []


def _drop_blobs(paths: list[str]) -> None:
    """Remove pruned blobs — only ever called AFTER the caller's commit, so a
    rollback can never leave a live row pointing at a deleted file. A failure
    leaves an orphan, which is inert; nothing references it."""
    if not paths:
        return
    storage = get_storage()
    for path in paths:
        try:
            storage.delete(path)
        except Exception:  # pragma: no cover - storage backend specific
            log.warning("Could not delete pruned report blob %s", path)


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
    data = _xlsx_bytes(
        build_benefit_selection_workbook(db, py, masked=masked, window_id=window_id)
    )
    retained, pruned = _retain_download(
        db, user, py, "benefit_selection",
        {"masked": masked, "window_id": window_id}, data,
    )
    write_audit(
        db, user, action="export", entity_type="insurer_report",
        entity_id=policy_year_id,
        after={
            "report": "benefit-selection",
            "masked": masked,
            "window_id": window_id,
            **retained,
        },
    )
    db.commit()
    _drop_blobs(pruned)
    return _bytes_response(
        data,
        "benefit-selection-status-with-buy-sell-leave-report-"
        f"{business_today():%Y%m%d}.xlsx",
        retained,
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
        wb, f"placement-slip-{py.year}-{business_today():%Y%m%d}.xlsx"
    )


@router.get("/quotation-slip")
@limiter.limit("20/minute")
def download_quotation_slip_export(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Insurer-separated quotation workbooks in one ZIP download.

    Each workbook contains only that insurer's tagged product sheets. Rates and
    premiums stay blank, and untagged configuration is retained in an explicit
    Unassigned workbook instead of being silently omitted.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    data = build_quotation_slip_archive(db, py)
    write_audit(
        db, user, action="export", entity_type="placement_slip",
        entity_id=policy_year_id, after={"report": "quotation-slip"},
    )
    db.commit()
    return _bytes_response(
        data,
        f"quotation-slips-by-insurer-{py.year}-{business_today():%Y%m%d}.zip",
        media_type="application/zip",
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
        f"{business_today():%Y%m%d}.xlsx",
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
        f"{business_today():%Y%m%d}.xlsx",
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
    resolved_end = end or business_today()
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
            # Whether downloading this workbook also files a retained copy —
            # SERVED, like every other control on the row, so the page shows a
            # submission record on exactly the workbooks that keep one. Derived
            # client-side it would announce an archive that does not exist.
            "retained_type": retained_type_for(spec.key),
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
    data = _xlsx_bytes(
        build_workbook(
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
    )
    retained, pruned = _retain_download(
        db, user, py, spec.key, {"insurer": insurer, "masked": masked}, data,
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
            **retained,
        },
    )
    db.commit()
    _drop_blobs(pruned)
    return _bytes_response(data, workbook_filename(spec, py, insurer), retained)

