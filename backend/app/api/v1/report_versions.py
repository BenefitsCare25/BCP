"""Report version retention endpoints (Reports Center).

Save a generated report as a retained version, list/download the history, check
whether the latest version is stale, and download a movement (adds/deletions/
changes) report for the insurer listings. See ``services/report_versions.py``
and ``services/report_registry.py``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import ROLE_BROKER_VIEWER, CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_for_user,
    load_report_version,
    user_owns,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.report_version import ReportVersion
from app.services.insurer_listings import configured_insurers_for_year
from app.services.report_registry import REGISTRY, spec_for
from app.services.report_versions import (
    ReportTooLargeError,
    compute_movement,
    create_version,
    list_versions,
    load_version_blob,
    previous_version,
    report_status,
    version_out,
)

router = APIRouter(
    prefix="/policy-years/{policy_year_id}/report-versions", tags=["reports"]
)
item_router = APIRouter(prefix="/report-versions", tags=["reports"])
registry_router = APIRouter(tags=["reports"])

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class CreateVersionIn(BaseModel):
    report_type: str
    insurer: str | None = None
    masked: bool = True
    window_id: str | None = None
    label: str | None = None


def _spec_or_404(report_type: str):
    try:
        return spec_for(report_type)
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown report type {report_type!r}"
        ) from None


def _params_from(body: CreateVersionIn) -> dict:
    params: dict = {"masked": body.masked}
    if body.insurer:
        params["insurer"] = body.insurer
    if body.window_id:
        params["window_id"] = body.window_id
    return params


@registry_router.get("/report-registry")
def get_report_registry(
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """The report-versioning classification (mode/scope/movement per report)."""
    return [
        {
            "report_type": s.report_type,
            "label": s.label,
            "mode": s.mode,
            "scope": s.scope,
            "fmt": s.fmt,
            "has_movement": s.has_movement,
        }
        for s in REGISTRY.values()
    ]


@router.post("")
@limiter.limit("10/minute")
def create_report_version(
    request: Request,
    policy_year_id: str,
    body: CreateVersionIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    py = assert_policy_year_for_user(policy_year_id, user, db)
    spec = _spec_or_404(body.report_type)
    params = _params_from(body)

    if spec.scope == "insurer":
        if not body.insurer:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "insurer is required.")
        known = {i.lower() for i in configured_insurers_for_year(db, py)}
        if body.insurer.strip().lower() not in known:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No products are assigned to insurer {body.insurer!r} for this year.",
            )
    if not body.masked and user.role == ROLE_BROKER_VIEWER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Unmasked NRIC/FIN downloads require write access.",
        )

    try:
        rv, created = create_version(db, user, py, body.report_type, params, body.label)
    except ReportTooLargeError as exc:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)
        ) from exc

    if created:
        write_audit(
            db, user, action="report_version_create", entity_type="report_version",
            entity_id=rv.id,
            after={
                "report_type": rv.report_type, "scope_key": rv.scope_key,
                "version_no": rv.version_no, "masked": params["masked"],
            },
        )
        db.commit()
        db.refresh(rv)
    return {**version_out(rv), "unchanged": not created}


@router.get("")
def list_report_versions(
    policy_year_id: str,
    report_type: str,
    scope_key: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    py = assert_policy_year_for_user(policy_year_id, user, db)
    _spec_or_404(report_type)
    return [
        version_out(rv) for rv in list_versions(db, py, report_type, scope_key)
    ]


@router.get("/status")
def report_version_status(
    policy_year_id: str,
    report_type: str,
    scope_key: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    py = assert_policy_year_for_user(policy_year_id, user, db)
    _spec_or_404(report_type)
    return report_status(db, py, report_type, scope_key)


def _blob_response(rv: ReportVersion, content: bytes) -> Response:
    return Response(
        content=content,
        media_type=rv.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{rv.file_name}"'},
    )


@item_router.get("/{version_id}/download")
@limiter.limit("30/minute")
def download_report_version(
    request: Request,
    version_id: str,
    rv: ReportVersion = Depends(load_report_version),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    content = load_version_blob(rv)
    write_audit(
        db, user, action="export", entity_type="report_version", entity_id=rv.id,
        after={"report_type": rv.report_type, "version_no": rv.version_no},
    )
    db.commit()
    return _blob_response(rv, content)


@item_router.get("/{version_id}/movement")
@limiter.limit("30/minute")
def download_movement(
    request: Request,
    version_id: str,
    since: str | None = None,
    rv: ReportVersion = Depends(load_report_version),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    spec = _spec_or_404(rv.report_type)
    if not spec.has_movement:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This report type has no movement report.",
        )

    baseline: ReportVersion | str | None
    if since == "live":
        baseline = "live"
    elif since:
        other = db.get(ReportVersion, since)
        if other is None or not user_owns(user, other.client_id):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Baseline version not found"
            )
        if (
            other.report_type != rv.report_type
            or other.scope_key != rv.scope_key
            or other.policy_year_id != rv.policy_year_id
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Baseline version is a different report series.",
            )
        baseline = other
    else:
        # Default: the previous version in the series ("since the last submission").
        baseline = previous_version(db, rv)

    wb = compute_movement(db, rv, baseline)
    write_audit(
        db, user, action="export", entity_type="report_version",
        entity_id=rv.id,
        after={
            "report_type": rv.report_type, "version_no": rv.version_no,
            "movement": True, "since": since or "previous",
        },
    )
    db.commit()

    from io import BytesIO

    buf = BytesIO()
    wb.save(buf)
    stem = rv.file_name.rsplit(".", 1)[0]
    return Response(
        content=buf.getvalue(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-movement.xlsx"'
        },
    )
