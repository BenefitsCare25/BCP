"""Policy years — list, read, activate, snapshot."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_policy_year, require_client_id
from app.db.session import get_db
from app.models import Category, PolicyYear
from app.models.category import CategoryStatus
from app.models.policy_year import PolicyYearStatus
from app.schemas.api import ActivationResult, PolicyYearCreate, PolicyYearOut, SnapshotOut
from app.services.product_terms import envelope_for, envelopes_for
from app.services.snapshot import build_snapshot

router = APIRouter(prefix="/policy-years", tags=["policy-years"])


def _policy_year_out(py: PolicyYear, envelope: tuple) -> PolicyYearOut:
    """Build the response, layering the derived coverage envelope over the ORM
    row (which only knows its own nominal span)."""
    start, end = envelope
    return PolicyYearOut(
        id=py.id,
        client_id=py.client_id,
        year=py.year,
        start_date=py.start_date,
        end_date=py.end_date,
        coverage_start=start,
        coverage_end=end,
        status=py.status.value if isinstance(py.status, PolicyYearStatus) else py.status,
        activated_at=py.activated_at,
    )


@router.get("", response_model=list[PolicyYearOut])
def list_policy_years(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PolicyYearOut]:
    client_id = require_client_id(user)
    rows = list(
        db.execute(
            select(PolicyYear)
            .where(PolicyYear.client_id == client_id)
            .order_by(PolicyYear.year.desc())
        )
        .scalars()
        .all()
    )
    envelopes = envelopes_for(db, rows)
    return [
        _policy_year_out(py, envelopes[py.id]) for py in rows
    ]


@router.post("", response_model=PolicyYearOut, status_code=status.HTTP_201_CREATED)
def create_policy_year(
    payload: PolicyYearCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearOut:
    client_id = require_client_id(user)
    # Two policies overlap iff start_a <= end_b AND start_b <= end_a.
    overlapping = db.execute(
        select(PolicyYear).where(
            PolicyYear.client_id == client_id,
            and_(
                PolicyYear.start_date <= payload.end_date,
                PolicyYear.end_date >= payload.start_date,
            ),
        )
    ).scalar_one_or_none()
    if overlapping is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Overlaps with existing policy year "
            f"{overlapping.start_date.isoformat()} to "
            f"{overlapping.end_date.isoformat()}.",
        )
    py = PolicyYear(
        client_id=client_id,
        year=payload.start_date.year,
        start_date=payload.start_date,
        end_date=payload.end_date,
        status=PolicyYearStatus.draft,
    )
    db.add(py)
    db.flush()
    # Panel clinic selections behave like a per-company setting: a new year
    # inherits the previous year's tags instead of resetting the locator.
    from app.services.panel_clinics import carry_over_panel_tags

    carried_panels = carry_over_panel_tags(db, py)
    write_audit(
        db,
        user,
        action="create_policy_year",
        entity_type="policy_year",
        entity_id=py.id,
        after={
            "year": py.year,
            "start_date": py.start_date.isoformat(),
            "end_date": py.end_date.isoformat(),
            "status": py.status.value,
            "carried_panel_tags": carried_panels,
        },
    )
    db.commit()
    db.refresh(py)
    # A brand-new year has no products yet, so the envelope equals its own span.
    return _policy_year_out(py, (py.start_date, py.end_date))


@router.get("/{policy_year_id}", response_model=PolicyYearOut)
def get_policy_year(
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> PolicyYearOut:
    return _policy_year_out(py, envelope_for(db, py))


@router.get("/{policy_year_id}/activation-readiness", response_model=dict)
def activation_readiness(
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """How close is this policy year to being activatable?

    Returns counts the UI uses to enable/disable the Activate button.
    """
    confirmed_case = case(
        (Category.status == CategoryStatus.confirmed.value, 1),
        else_=0,
    )
    row = db.execute(
        select(func.count(Category.id), func.coalesce(func.sum(confirmed_case), 0))
        .where(Category.policy_year_id == py.id)
    ).one()
    total, confirmed = int(row[0] or 0), int(row[1] or 0)
    return {
        "total_categories": total,
        "confirmed_categories": confirmed,
        "unconfirmed_categories": total - confirmed,
        "ready": total > 0 and confirmed == total and py.status == PolicyYearStatus.draft,
        "status": py.status.value if isinstance(py.status, PolicyYearStatus) else py.status,
    }


@router.post("/{policy_year_id}/activate", response_model=ActivationResult)
def activate_policy_year(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivationResult:
    if py.status != PolicyYearStatus.draft:
        current = py.status.value if isinstance(py.status, PolicyYearStatus) else py.status
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Policy year is already {current}",
        )

    total_categories = db.scalar(
        select(func.count(Category.id)).where(Category.policy_year_id == py.id)
    ) or 0
    if total_categories == 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Policy year has no categories. Configure at least one category before activating.",
        )

    unconfirmed = db.scalar(
        select(func.count(Category.id)).where(
            Category.policy_year_id == py.id,
            Category.status != CategoryStatus.confirmed.value,
        )
    ) or 0
    if unconfirmed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{unconfirmed} categories are not yet confirmed. "
            "Confirm all categories before activating.",
        )

    snapshot = build_snapshot(db, py.id, generated_by=user.user_id)
    now = datetime.now(tz=UTC)
    py.snapshot_json = snapshot
    py.status = PolicyYearStatus.active
    py.activated_at = now
    py.activated_by = user.user_id

    write_audit(
        db,
        user,
        action="activate_policy_year",
        entity_type="policy_year",
        entity_id=py.id,
        after={
            "year": py.year,
            "snapshot_counts": snapshot["counts"],
            "snapshot_version": snapshot["version"],
        },
    )
    db.commit()
    db.refresh(py)
    return ActivationResult(
        policy_year_id=py.id,
        status=py.status.value if isinstance(py.status, PolicyYearStatus) else py.status,
        activated_at=now,
        snapshot_counts=snapshot["counts"],
    )


@router.get("/{policy_year_id}/snapshot", response_model=SnapshotOut)
def get_snapshot(py: PolicyYear = Depends(load_policy_year)) -> SnapshotOut:
    if not py.snapshot_json:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No snapshot — policy year has not been activated.",
        )
    return SnapshotOut(
        policy_year_id=py.id,
        year=py.year,
        activated_at=py.activated_at,
        snapshot=py.snapshot_json,
    )


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{policy_year_id}/fact-find-form")
def download_fact_find_form(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Auto-fill and download the Group Insurance Fact-Find Form as ``.docx``.

    Fills every field the platform can resolve accurately (company, products,
    eligibility, basis of cover, member composition) and leaves the rest blank
    for the broker. Best-effort tables (age/gender) fill for whatever subset of
    the roster carries the data; what was skipped is returned in the
    ``X-FactFind-Notes`` header.
    """
    from app.services.fact_find_render import generate

    docx_bytes, notes = generate(db, py)
    write_audit(
        db,
        user,
        action="export",
        entity_type="policy_year",
        entity_id=py.id,
        after={"artifact": "fact_find_form", "notes": notes},
    )
    db.commit()

    filename = f"fact-find-{py.year}.docx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        # Completeness report (what was partial/skipped), URL-encoded for ASCII
        # header safety; the frontend surfaces it after download. Exposed to the
        # browser via CORSMiddleware's expose_headers (app/main.py), not here —
        # a per-response Access-Control-Expose-Headers would be overwritten.
        "X-FactFind-Notes": quote(" | ".join(notes)),
    }
    return Response(content=docx_bytes, media_type=_DOCX_MIME, headers=headers)
