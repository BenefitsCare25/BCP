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
from sqlalchemy import and_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_policy_year, require_client_id
from app.core.portal_auth import active_policy_year
from app.db.session import get_db
from app.models import PolicyYear
from app.models.policy_year import PolicyYearStatus
from app.schemas.api import (
    PolicyYearCopyIn,
    PolicyYearCopyResult,
    PolicyYearCreate,
    PolicyYearDeletionImpact,
    PolicyYearOut,
    PolicyYearReadinessOut,
    PolicyYearUpdate,
    SnapshotOut,
)
from app.services.policy_year_clone import clone_policy_year_config
from app.services.policy_year_safety import deletion_counts, readiness
from app.services.product_terms import envelope_for, envelopes_for

router = APIRouter(prefix="/policy-years", tags=["policy-years"])


def _lock_policy_year_scope(db: Session, client_id: str) -> None:
    """Serialize benefit-year lifecycle writes for one company on Postgres."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
            {"scope": f"policy-years:{client_id}"},
        )


def _raise_policy_year_conflict(db: Session, exc: IntegrityError) -> None:
    db.rollback()
    constraint = str(getattr(exc.orig, "diag", None) or exc.orig)
    if "active_client" in constraint:
        detail = "Another benefit year is already live for this company."
    else:
        detail = "The benefit-year dates overlap an existing period."
    raise HTTPException(status.HTTP_409_CONFLICT, detail) from exc


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
    _lock_policy_year_scope(db, client_id)
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
    try:
        db.flush()
    except IntegrityError as exc:
        _raise_policy_year_conflict(db, exc)
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
        _lock_policy_year_scope(db, py.client_id)
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
    try:
        db.commit()
    except IntegrityError as exc:
        _raise_policy_year_conflict(db, exc)
    db.refresh(py)
    return _policy_year_out(py, envelope_for(db, py))


@router.delete("/{policy_year_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_policy_year(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a configuration-only draft; retain every operational year."""
    current = active_policy_year(db, py.client_id)
    if py.status == PolicyYearStatus.active or (current is not None and current.id == py.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The live benefit year cannot be deleted.",
        )
    _, operational = deletion_counts(db, py.id)
    if py.status != PolicyYearStatus.draft or operational:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only a draft benefit year with no roster, claims, enrollment, "
            "uploads, underwriting, or report history can be deleted. Archive "
            "this year instead.",
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
            "status": py.status.value if isinstance(py.status, PolicyYearStatus) else py.status,
        },
    )
    db.delete(py)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{policy_year_id}/deletion-impact",
    response_model=PolicyYearDeletionImpact,
)
def policy_year_deletion_impact(
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> PolicyYearDeletionImpact:
    counts, operational = deletion_counts(db, py.id)
    current = active_policy_year(db, py.client_id)
    reason: str | None = None
    if py.status != PolicyYearStatus.draft:
        reason = "Live and archived benefit years are retained as company history."
    elif current is not None and current.id == py.id:
        reason = "The live benefit year cannot be deleted."
    elif operational:
        reason = "This year contains operational history and must be archived rather than deleted."
    return PolicyYearDeletionImpact(
        deletable=reason is None,
        reason=reason,
        counts=counts,
        operational_records=operational,
    )


@router.get("/{policy_year_id}/readiness", response_model=PolicyYearReadinessOut)
def policy_year_readiness(
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> PolicyYearReadinessOut:
    metrics, blockers, warnings = readiness(db, py.id)
    return PolicyYearReadinessOut(
        ready=not blockers,
        metrics=metrics,
        blockers=blockers,
        warnings=warnings,
    )


@router.post("/{policy_year_id}/set-current", response_model=PolicyYearOut)
def set_current_policy_year(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearOut:
    """Make a ready year live and archive the previously-live year.

    Exactly one year per client is current. Activation is explicit and
    readiness-gated; configuration remains editable afterwards and every
    mutation is audited.
    """
    metrics, blockers, warnings = readiness(db, py.id)
    if blockers:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "benefit_year_not_ready",
                "message": "This benefit year is not ready to go live.",
                "blockers": blockers,
                "warnings": warnings,
                "metrics": metrics,
            },
        )
    _lock_policy_year_scope(db, py.client_id)
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
    # Partial unique indexes are checked per SQL statement. Persist demotions
    # before promoting the target so the transition never briefly has two live
    # rows inside the transaction.
    if others:
        db.flush()
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
    try:
        db.commit()
    except IntegrityError as exc:
        _raise_policy_year_conflict(db, exc)
    db.refresh(py)
    return _policy_year_out(py, envelope_for(db, py))


@router.post("/{policy_year_id}/archive", response_model=PolicyYearOut)
def archive_policy_year(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearOut:
    """Retain a non-live year and its operational history."""
    if py.status == PolicyYearStatus.active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Make another ready benefit year live before archiving this one.",
        )
    before = py.status.value
    py.status = PolicyYearStatus.archived
    write_audit(
        db,
        user,
        action="archive",
        entity_type="policy_year",
        entity_id=py.id,
        before={"status": before},
        after={"status": PolicyYearStatus.archived.value},
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
    _lock_policy_year_scope(db, client_id)
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
    try:
        db.flush()
    except IntegrityError as exc:
        _raise_policy_year_conflict(db, exc)

    from app.services.panel_cards import carry_over_card_assignments
    from app.services.panel_clinics import carry_over_panel_tags

    # Both come from the year being COPIED, not the most recent one — the rest
    # of this handler clones py.id, and mixing another year's panel setup in
    # would pair this year's products with a different year's configuration.
    carry_over_panel_tags(db, target, source_policy_year_id=py.id)
    carry_over_card_assignments(db, target, source_policy_year_id=py.id)
    counts = clone_policy_year_config(db, source_id=py.id, target_id=target.id, client_id=client_id)

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
