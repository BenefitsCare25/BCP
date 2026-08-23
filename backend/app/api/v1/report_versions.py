"""Report version retention endpoints (Reports Center).

Save a generated report as a retained version, list/download the history, check
whether the latest version is stale, and download a movement (adds/deletions/
changes) report for the insurer listings. See ``services/report_versions.py``
and ``services/report_registry.py``.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.reports import assert_masking_allowed
from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_for_user,
    load_report_version,
    user_owns,
)
from app.core.rate_limit import limiter
from app.core.storage import get_storage
from app.db.session import get_db
from app.models.report_version import ReportVersion
from app.services.insurer_listings import configured_insurers_for_year
from app.services.report_registry import REGISTRY, ReportSpec, spec_for
from app.services.report_versions import (
    ReportTooLargeError,
    actor_names,
    compute_movement,
    create_version,
    list_versions,
    load_version_blob,
    movement_summary,
    previous_version,
    report_status,
    version_out,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/policy-years/{policy_year_id}/report-versions", tags=["reports"]
)
item_router = APIRouter(prefix="/report-versions", tags=["reports"])
registry_router = APIRouter(tags=["reports"])

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# Ceiling on a merged history request. The real caller asks for a live series
# plus its superseded ones — three today, and the whole registry is six.
_MAX_MERGED_TYPES = 8


class CreateVersionIn(BaseModel):
    report_type: str
    insurer: str | None = None
    masked: bool = True
    window_id: str | None = None
    label: str | None = None


def _spec_or_404(report_type: str) -> ReportSpec:
    try:
        return spec_for(report_type)
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown report type {report_type!r}"
        ) from None


def _params_from(body: CreateVersionIn) -> dict[str, Any]:
    params: dict[str, Any] = {"masked": body.masked}
    if body.insurer:
        params["insurer"] = body.insurer
    if body.window_id:
        params["window_id"] = body.window_id
    return params


@registry_router.get("/report-registry")
def get_report_registry(
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
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
) -> dict[str, Any]:
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
    assert_masking_allowed(user, body.masked)

    try:
        rv, created, superseded_path = create_version(
            db, user, py, body.report_type, params, body.label
        )
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
        # Only now that the new row is committed is it safe to remove the
        # superseded latest-mode blob. A failure here leaves an orphan file
        # (harmless — the DB no longer references it), never a dangling row.
        if superseded_path:
            try:
                get_storage().delete(superseded_path)
            except Exception:
                logger.warning(
                    "Failed to delete superseded report blob %s", superseded_path
                )
    return {**version_out(rv), "unchanged": not created}


@router.get("")
def list_report_versions(
    policy_year_id: str,
    report_type: str,
    scope_key: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """The history for a series, newest first.

    ``report_type`` accepts a comma-separated list so the drawer can show a
    live series beside the SUPERSEDED ones it replaced (`report_registry.
    SUPERSEDED_TYPES`). Retiring a report type must not orphan the record of
    what was submitted under it — the bytes are the point, and a broker cannot
    reach them from anywhere else.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    # Deduplicated and bounded. Each entry costs a query returning up to
    # MAX_LIMIT rows, and `_spec_or_404` rejects an unknown type but not a
    # repeated one — so `?report_type=insurer_submission,insurer_submission,…`
    # was a single authenticated request that issued thousands of queries and
    # materialised the results of all of them.
    wanted = list(dict.fromkeys(t.strip() for t in report_type.split(",") if t.strip()))
    if not wanted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "report_type is required.")
    if len(wanted) > _MAX_MERGED_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"At most {_MAX_MERGED_TYPES} report types can be listed together.",
        )
    rows: list[ReportVersion] = []
    for rt in wanted:
        _spec_or_404(rt)
        rows.extend(list_versions(db, py, rt, scope_key))
    # One chronology across the merged series — version numbers restart per
    # series, so sorting on them would interleave nonsensically.
    rows.sort(key=lambda rv: (rv.created_at is not None, rv.created_at), reverse=True)
    names = actor_names(db, rows)
    return [version_out(rv, names) for rv in rows]


@router.get("/status")
def report_version_status(
    policy_year_id: str,
    report_type: str,
    scope_key: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    py = assert_policy_year_for_user(policy_year_id, user, db)
    _spec_or_404(report_type)
    return report_status(db, py, report_type, scope_key)


def _assert_version_readable(user: CurrentUser, rv: ReportVersion) -> None:
    """Apply the live reports' masking rule to a RETAINED version.

    `create_report_version` also checks this, but that check is unreachable —
    the router sits behind `require_write_access`, so a viewer can never POST.
    The GET is the only path a `broker_viewer` can take, and a retained listing
    holds the same unmasked NRIC/FIN the live endpoint refuses them. Read
    `masked` from the stored params (the exact request that produced the blob);
    a version predating the field is treated as unmasked, i.e. restricted.
    """
    assert_masking_allowed(user, bool((rv.params or {}).get("masked", False)))


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
    _assert_version_readable(user, rv)
    content = load_version_blob(rv)
    write_audit(
        db, user, action="export", entity_type="report_version", entity_id=rv.id,
        after={"report_type": rv.report_type, "version_no": rv.version_no},
    )
    db.commit()
    return _blob_response(rv, content)


@item_router.get("/{version_id}/movement-summary")
@limiter.limit("60/minute")
def movement_counts(
    request: Request,
    version_id: str,
    rv: ReportVersion = Depends(load_report_version),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """How much the roster moved since this version — counts only.

    Deliberately its OWN endpoint rather than a field on `/status`: the counts
    come from the same full baseline-vs-target diff `compute_movement` runs, and
    `/status` is polled by every report row whether stale or not. Splitting it
    out narrows the diff to stale, movement-capable rows — a reduction, NOT an
    elimination: staleness is the steady state for a live roster, so expect
    this to run for most rows on the page. Do not fold it back into `/status`.

    Unlike the blob and workbook downloads it does NOT call
    `_assert_version_readable`. That gate exists to keep a retained listing's
    raw NRIC/FIN away from a `broker_viewer`; three integers carry no
    identifier, and gating them would blank the staleness banner for exactly
    the read-only users most likely to be looking at it.
    """
    spec = _spec_or_404(rv.report_type)
    if not spec.has_movement:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This report type has no movement report.",
        )
    return movement_summary(db, rv, "live")


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
    # The movement workbook is built FROM the retained listings, so it carries
    # the same identifiers — gate it exactly like the blob download.
    _assert_version_readable(user, rv)
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
        # A MISSING predecessor is not an empty one. `compute_movement` reads
        # `None` as "initial submission — everything is an addition", which is
        # right for v1 and catastrophic for a version whose predecessor was
        # pruned: the sheet would list every member of the roster under
        # ADDITIONS and nothing under DELETIONS, i.e. wrong in the direction a
        # broker acts on. Refuse instead of reporting a diff we cannot compute.
        if baseline is None and rv.version_no > 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "baseline_pruned",
                    "message": (
                        f"The submission before v{rv.version_no} is no longer "
                        "retained, so there is nothing to compare it against. "
                        "Pick a later version, or diff against the current "
                        "roster."
                    ),
                },
            )

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
