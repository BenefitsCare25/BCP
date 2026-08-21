"""Policy (benefit) years — list, create, update, delete, set-current, copy.

A policy year is the config-version container for one client. There is no
activation lock: configuration is editable on every year. Exactly one year is
flagged "current" (``status == active``) — that is the year the member portal
reads and the one claims are submitted against. Setting a year current demotes
the previously-current one to ``archived``.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_policy_year, require_client_id
from app.db.session import get_db
from app.models import PolicyYear
from app.models.policy_year import PolicyYearStatus
from app.schemas.api import (
    PolicyYearCopyIn,
    PolicyYearCopyResult,
    PolicyYearCreate,
    PolicyYearOut,
    PolicyYearUpdate,
    SnapshotOut,
)
from app.services.policy_year_clone import clone_policy_year_config
from app.services.product_terms import envelope_for, envelopes_for

router = APIRouter(prefix="/policy-years", tags=["policy-years"])


def _policy_year_out(py: PolicyYear, envelope: tuple[date, date]) -> PolicyYearOut:
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
        claim_grace_period_days=py.claim_grace_period_days,
        leaver_access_days=py.leaver_access_days,
        activated_at=py.activated_at,
    )


def _assert_no_overlap(
    db: Session, client_id: str, start: date, end: date, *, exclude_id: str | None = None
) -> None:
    """409 if [start, end] overlaps another year for the client.

    Two policies overlap iff start_a <= end_b AND start_b <= end_a.
    """
    stmt = select(PolicyYear).where(
        PolicyYear.client_id == client_id,
        and_(PolicyYear.start_date <= end, PolicyYear.end_date >= start),
    )
    if exclude_id is not None:
        stmt = stmt.where(PolicyYear.id != exclude_id)
    overlapping = db.execute(stmt).scalars().first()
    if overlapping is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Overlaps with existing benefit year "
            f"{overlapping.start_date.isoformat()} to "
            f"{overlapping.end_date.isoformat()}.",
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
    return [_policy_year_out(py, envelopes[py.id]) for py in rows]


@router.post("", response_model=PolicyYearOut, status_code=status.HTTP_201_CREATED)
def create_policy_year(
    payload: PolicyYearCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearOut:
    client_id = require_client_id(user)
    _assert_no_overlap(db, client_id, payload.start_date, payload.end_date)
    py = PolicyYear(
        client_id=client_id,
        year=payload.start_date.year,
        start_date=payload.start_date,
        end_date=payload.end_date,
        status=PolicyYearStatus.draft,
        claim_grace_period_days=payload.claim_grace_period_days,
        leaver_access_days=payload.leaver_access_days,
    )
    db.add(py)
    db.flush()
    # Panel clinic selections behave like a per-company setting: a new year
    # inherits the previous year's tags instead of resetting the locator.
    from app.services.panel_cards import carry_over_card_assignments
    from app.services.panel_clinics import carry_over_panel_tags

    carried_panels = carry_over_panel_tags(db, py)
    carry_over_card_assignments(db, py)
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


@router.patch("/{policy_year_id}", response_model=PolicyYearOut)
def update_policy_year(
    payload: PolicyYearUpdate,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearOut:
    """Partial update: dates and/or claim grace period.

    Only fields present in the request body are written, so a grace-period-only
    edit can't wipe the coverage dates and a dates-only edit can't reset grace.
    """
    fields = payload.model_fields_set
    new_start = payload.start_date if "start_date" in fields else py.start_date
    new_end = payload.end_date if "end_date" in fields else py.end_date
    # Dates are nullable in the schema (absent = keep), but an EXPLICIT null
    # can't be persisted (NOT NULL columns) — reject it cleanly instead of
    # letting `date < None` raise a 500.
    if new_start is None or new_end is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "start_date and end_date cannot be null",
        )
    if new_end < new_start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "end_date must be on or after start_date",
        )
    if "start_date" in fields or "end_date" in fields:
        _assert_no_overlap(db, py.client_id, new_start, new_end, exclude_id=py.id)
        py.start_date = new_start
        py.end_date = new_end
        py.year = new_start.year
    if "claim_grace_period_days" in fields:
        py.claim_grace_period_days = payload.claim_grace_period_days
    if "leaver_access_days" in fields:
        py.leaver_access_days = payload.leaver_access_days

    write_audit(
        db,
        user,
        action="update_policy_year",
        entity_type="policy_year",
        entity_id=py.id,
        after={
            "start_date": py.start_date.isoformat(),
            "end_date": py.end_date.isoformat(),
            "claim_grace_period_days": py.claim_grace_period_days,
            "leaver_access_days": py.leaver_access_days,
        },
    )
    db.commit()
    db.refresh(py)
    return _policy_year_out(py, envelope_for(db, py))


@router.delete("/{policy_year_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_policy_year(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a benefit year and its configuration (cascade).

    The current year (``active``) can't be deleted — set another year current
    first, so the member portal never loses its year out from under it.
    """
    if py.status == PolicyYearStatus.active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is the current benefit year. Set another year as current "
            "before deleting it.",
        )
    write_audit(
        db,
        user,
        action="delete_policy_year",
        entity_type="policy_year",
        entity_id=py.id,
        before={
            "year": py.year,
            "start_date": py.start_date.isoformat(),
            "end_date": py.end_date.isoformat(),
            "status": py.status.value
            if isinstance(py.status, PolicyYearStatus)
            else py.status,
        },
    )
    db.delete(py)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{policy_year_id}/set-current", response_model=PolicyYearOut)
def set_current_policy_year(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearOut:
    """Flag this year as the current one the member portal reads.

    Exactly one year per client is current: any previously-current year is
    demoted to ``archived``. No snapshot, no readiness gate — configuration
    stays editable on every year.
    """
    now = datetime.now(tz=UTC)
    others = (
        db.execute(
            select(PolicyYear).where(
                PolicyYear.client_id == py.client_id,
                PolicyYear.status == PolicyYearStatus.active,
                PolicyYear.id != py.id,
            )
        )
        .scalars()
        .all()
    )
    for other in others:
        other.status = PolicyYearStatus.archived
    py.status = PolicyYearStatus.active
    py.activated_at = now
    py.activated_by = user.user_id

    write_audit(
        db,
        user,
        action="set_current_policy_year",
        entity_type="policy_year",
        entity_id=py.id,
        after={
            "year": py.year,
            "demoted": [o.id for o in others],
        },
    )
    db.commit()
    db.refresh(py)
    return _policy_year_out(py, envelope_for(db, py))


@router.post(
    "/{policy_year_id}/copy",
    response_model=PolicyYearCopyResult,
    status_code=status.HTTP_201_CREATED,
)
def copy_policy_year(
    payload: PolicyYearCopyIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearCopyResult:
    """Create a new benefit year and clone this year's configuration into it."""
    client_id = require_client_id(user)
    _assert_no_overlap(db, client_id, payload.start_date, payload.end_date)

    target = PolicyYear(
        client_id=client_id,
        year=payload.start_date.year,
        start_date=payload.start_date,
        end_date=payload.end_date,
        status=PolicyYearStatus.draft,
        claim_grace_period_days=(
            payload.claim_grace_period_days
            if payload.claim_grace_period_days is not None
            else py.claim_grace_period_days
        ),
        # Carried over like the grace period — a renewal keeps the company's
        # settled deadlines unless the broker states new ones.
        leaver_access_days=(
            payload.leaver_access_days
            if payload.leaver_access_days is not None
            else py.leaver_access_days
        ),
    )
    db.add(target)
    db.flush()

    from app.services.panel_cards import carry_over_card_assignments
    from app.services.panel_clinics import carry_over_panel_tags

    # Both come from the year being COPIED, not the most recent one — the rest
    # of this handler clones py.id, and mixing another year's panel setup in
    # would pair this year's products with a different year's configuration.
    carry_over_panel_tags(db, target, source_policy_year_id=py.id)
    carry_over_card_assignments(db, target, source_policy_year_id=py.id)
    counts = clone_policy_year_config(
        db, source_id=py.id, target_id=target.id, client_id=client_id
    )

    write_audit(
        db,
        user,
        action="copy_policy_year",
        entity_type="policy_year",
        entity_id=target.id,
        after={
            "source_policy_year_id": py.id,
            "start_date": target.start_date.isoformat(),
            "end_date": target.end_date.isoformat(),
            "copied": counts,
        },
    )
    db.commit()
    db.refresh(target)
    return PolicyYearCopyResult(
        policy_year=_policy_year_out(target, (target.start_date, target.end_date)),
        copied=counts,
    )


@router.get("/{policy_year_id}/snapshot", response_model=SnapshotOut)
def get_snapshot(py: PolicyYear = Depends(load_policy_year)) -> SnapshotOut:
    if not py.snapshot_json:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No snapshot for this policy year.",
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
        # header safety. It remains available to API consumers and is also
        # preserved in the export audit record above; it is not a download
        # failure and therefore is not surfaced as an application error.
        "X-FactFind-Notes": quote(" | ".join(notes)),
    }
    return Response(content=docx_bytes, media_type=_DOCX_MIME, headers=headers)
