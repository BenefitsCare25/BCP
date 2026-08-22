"""Panel clinic locations — broker settings surface.

A listing is one clinic network (insurer + panel provider + country + clinic
type) whose clinics arrive as an uploaded workbook and are replaced wholesale
per upload. Listings form a shared LIBRARY (client_id NULL — uploaded once,
visible to every company; legacy client-pinned rows stay tenant-scoped).
Each company selects which entries apply by tagging them to its policy year
(`PUT /policy-years/{id}/panels`) — tagging is what exposes clinics to that
year's members (see `GET /portal/clinics`).

Listings are reference data — NOT part of the activation snapshot — so
uploads and tagging stay open on active years (panel networks change
mid-year), mirroring the other operational writes.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.clock import today as business_today
from app.core.deps import (
    _deny_cross_tenant,
    assert_policy_year_for_user,
    load_panel_listing,
    load_policy_year,
    require_client_id,
    tenant_or_global,
)
from app.core.identity import accessible_clients
from app.core.pagination import MAX_LIMIT
from app.core.rate_limit import limiter
from app.core.uploads import WORKBOOK_SUFFIXES, saved_upload
from app.db.session import get_db
from app.models import PanelClinic, PanelListing, PolicyYear, PolicyYearPanel
from app.models.panel_clinic import clinic_type_label
from app.models.policy_year import PolicyYearStatus
from app.schemas.panel import (
    ClinicOut,
    ListingCompaniesIn,
    ListingCompanyOut,
    PanelListingIn,
    PanelListingOut,
    PanelListingUpdate,
    PanelUploadResult,
    PolicyYearPanelsIn,
    PolicyYearPanelsOut,
)
from app.services.panel_clinics import (
    PanelParseError,
    clinic_out,
    export_listing_workbook,
    parse_panel_workbook,
    replace_listing_clinics,
)

router = APIRouter(prefix="/panel-listings", tags=["panel-listings"])

# Tagging rides the policy-year path (query/PUT), registered alongside.
year_router = APIRouter(prefix="/policy-years", tags=["panel-listings"])


def _listing_out(
    listing: PanelListing,
    clinic_count: int,
    tagged_policy_year_ids: list[str],
) -> PanelListingOut:
    return PanelListingOut(
        id=listing.id,
        insurer=listing.insurer,
        panel_provider=listing.panel_provider,
        country=listing.country,
        clinic_type=listing.clinic_type,
        label=listing.label,
        display_label=listing.display_label(),
        type_label=clinic_type_label(listing.country, listing.clinic_type),
        clinic_count=clinic_count,
        source_filename=listing.source_filename,
        uploaded_at=listing.uploaded_at,
        tagged_policy_year_ids=tagged_policy_year_ids,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
    )


def _clinic_count(db: Session, listing_id: str) -> int:
    return (
        db.scalar(
            select(func.count(PanelClinic.id)).where(
                PanelClinic.panel_listing_id == listing_id
            )
        )
        or 0
    )


def _tagged_year_ids(db: Session, listing_id: str) -> list[str]:
    return list(
        db.scalars(
            select(PolicyYearPanel.policy_year_id).where(
                PolicyYearPanel.panel_listing_id == listing_id
            )
        )
    )


def _assert_unique_combo(
    db: Session, payload: PanelListingIn, exclude_id: str | None = None
) -> None:
    # Uniqueness is enforced within the shared library (client_id NULL); the
    # DB unique constraint can't cover NULLs, so the check lives here.
    stmt = select(PanelListing.id).where(
        PanelListing.client_id.is_(None),
        PanelListing.insurer == payload.insurer,
        PanelListing.panel_provider == payload.panel_provider,
        PanelListing.country == payload.country,
        PanelListing.clinic_type == payload.clinic_type,
    )
    if exclude_id:
        stmt = stmt.where(PanelListing.id != exclude_id)
    if db.execute(stmt).first() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_panel_listing",
                "message": (
                    "A panel listing for this insurer, provider, country and "
                    "clinic type already exists — upload the new list there."
                ),
            },
        )


@router.get("", response_model=list[PanelListingOut])
def list_panel_listings(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PanelListingOut]:
    client_id = require_client_id(user)
    # Shared library entries (client_id NULL) + any legacy rows pinned to the
    # active company.
    listings = db.scalars(
        select(PanelListing)
        .where(tenant_or_global(PanelListing.client_id, client_id))
        .order_by(PanelListing.insurer, PanelListing.panel_provider, PanelListing.country)
    ).all()
    if not listings:
        return []
    ids = [listing.id for listing in listings]
    counts: dict[str, int] = dict(
        db.execute(
            select(PanelClinic.panel_listing_id, func.count(PanelClinic.id))
            .where(PanelClinic.panel_listing_id.in_(ids))
            .group_by(PanelClinic.panel_listing_id)
        ).all()
    )
    tags: dict[str, list[str]] = {}
    for listing_id, year_id in db.execute(
        select(PolicyYearPanel.panel_listing_id, PolicyYearPanel.policy_year_id).where(
            PolicyYearPanel.panel_listing_id.in_(ids)
        )
    ).all():
        tags.setdefault(listing_id, []).append(year_id)
    return [
        _listing_out(listing, counts.get(listing.id, 0), tags.get(listing.id, []))
        for listing in listings
    ]


@router.post("", response_model=PanelListingOut, status_code=status.HTTP_201_CREATED)
def create_panel_listing(
    payload: PanelListingIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelListingOut:
    """Create a shared library entry — uploaded once, selectable by every
    company via its policy-year panel tags."""
    _assert_unique_combo(db, payload)
    listing = PanelListing(
        client_id=None,
        insurer=payload.insurer,
        panel_provider=payload.panel_provider,
        country=payload.country,
        clinic_type=payload.clinic_type,
        label=(payload.label or "").strip() or None,
    )
    db.add(listing)
    db.flush()
    write_audit(
        db,
        user,
        action="panel_listing.create",
        entity_type="panel_listing",
        entity_id=listing.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(listing)
    return _listing_out(listing, 0, [])


@router.patch("/{listing_id}", response_model=PanelListingOut)
def update_panel_listing(
    payload: PanelListingUpdate,
    listing: PanelListing = Depends(load_panel_listing),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelListingOut:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _listing_out(
            listing, _clinic_count(db, listing.id), _tagged_year_ids(db, listing.id)
        )
    merged = PanelListingIn(
        insurer=changes.get("insurer", listing.insurer),
        panel_provider=changes.get("panel_provider", listing.panel_provider),
        country=changes.get("country", listing.country),
        clinic_type=changes.get("clinic_type", listing.clinic_type),
        label=changes.get("label", listing.label),
    )
    _assert_unique_combo(db, merged, exclude_id=listing.id)
    before = {
        "insurer": listing.insurer,
        "panel_provider": listing.panel_provider,
        "country": listing.country,
        "clinic_type": listing.clinic_type,
        "label": listing.label,
    }
    listing.insurer = merged.insurer
    listing.panel_provider = merged.panel_provider
    listing.country = merged.country
    listing.clinic_type = merged.clinic_type
    listing.label = (merged.label or "").strip() or None
    write_audit(
        db,
        user,
        action="panel_listing.update",
        entity_type="panel_listing",
        entity_id=listing.id,
        before=before,
        after=changes,
    )
    db.commit()
    db.refresh(listing)
    return _listing_out(
        listing, _clinic_count(db, listing.id), _tagged_year_ids(db, listing.id)
    )


@router.delete("/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_panel_listing(
    listing: PanelListing = Depends(load_panel_listing),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    write_audit(
        db,
        user,
        action="panel_listing.delete",
        entity_type="panel_listing",
        entity_id=listing.id,
        before={
            "insurer": listing.insurer,
            "panel_provider": listing.panel_provider,
            "country": listing.country,
            "clinic_type": listing.clinic_type,
            "clinic_count": _clinic_count(db, listing.id),
        },
    )
    db.delete(listing)  # clinics + policy-year tags cascade
    db.commit()


@router.post("/{listing_id}/upload", response_model=PanelUploadResult)
@limiter.limit("20/minute")
async def upload_panel_clinics(
    request: Request,
    file: Annotated[UploadFile, File(description="Panel clinic listing workbook")],
    listing: PanelListing = Depends(load_panel_listing),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelUploadResult:
    """Replace the listing's clinics with the uploaded workbook (atomic —
    a parse failure leaves the previous list untouched)."""
    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        try:
            parsed = parse_panel_workbook(tmp_path)
        except PanelParseError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
            ) from exc

    replace_listing_clinics(db, listing, parsed.clinics)
    listing.source_filename = (file.filename or "untitled")[:255]
    listing.uploaded_at = datetime.now(UTC)
    listing.uploaded_by = user.user_id
    write_audit(
        db,
        user,
        action="panel_listing.upload",
        entity_type="panel_listing",
        entity_id=listing.id,
        after={
            "filename": listing.source_filename,
            "imported": len(parsed.clinics),
            "rows_total": parsed.rows_total,
            "skipped_no_name": parsed.skipped_no_name,
            "missing_coordinates": parsed.missing_coordinates,
        },
    )
    db.commit()
    db.refresh(listing)
    return PanelUploadResult(
        listing=_listing_out(
            listing, len(parsed.clinics), _tagged_year_ids(db, listing.id)
        ),
        rows_total=parsed.rows_total,
        imported=len(parsed.clinics),
        skipped_no_name=parsed.skipped_no_name,
        missing_coordinates=parsed.missing_coordinates,
    )


@router.get("/{listing_id}/download")
def download_panel_clinics(
    listing: PanelListing = Depends(load_panel_listing),
    db: Session = Depends(get_db),
) -> Response:
    clinics = db.scalars(
        select(PanelClinic)
        .where(PanelClinic.panel_listing_id == listing.id)
        .order_by(PanelClinic.name)
    ).all()
    filename = f"{listing.display_label().replace(' ', '_')}_clinics.xlsx"
    return Response(
        content=export_listing_workbook(list(clinics)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.get("/{listing_id}/clinics", response_model=list[ClinicOut])
def list_panel_clinics(
    q: str | None = Query(default=None, max_length=128),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    listing: PanelListing = Depends(load_panel_listing),
    db: Session = Depends(get_db),
) -> list[ClinicOut]:
    """Broker-side preview of a listing's clinics (no distance context).
    Searches the same fields as the member locator (incl. doctor)."""
    stmt = (
        select(PanelClinic)
        .where(PanelClinic.panel_listing_id == listing.id)
        .order_by(PanelClinic.name)
    )
    if q:
        # Escape LIKE metacharacters so "100%" or "a_c" match literally.
        escaped = (
            q.lower().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        )
        needle = f"%{escaped}%"
        stmt = stmt.where(
            func.lower(PanelClinic.name).like(needle, escape="\\")
            | func.lower(func.coalesce(PanelClinic.area, "")).like(needle, escape="\\")
            | func.lower(func.coalesce(PanelClinic.address, "")).like(needle, escape="\\")
            | func.lower(func.coalesce(PanelClinic.postal_code, "")).like(needle, escape="\\")
            | func.lower(func.coalesce(PanelClinic.doctor, "")).like(needle, escape="\\")
        )
    clinics = db.scalars(stmt.offset(offset).limit(limit)).all()
    type_label = clinic_type_label(listing.country, listing.clinic_type)
    panel_label = listing.display_label()
    return [
        clinic_out(c, listing, type_label=type_label, panel_label=panel_label)
        for c in clinics
    ]


# ── Company enablement (checkbox list across all accessible companies) ───────


def _target_years_by_client(db: Session, client_ids: list[str]) -> dict[str, PolicyYear]:
    """Each company's enable target: today's period, then legacy active, then
    the latest non-archived year. Companies with no usable year are absent."""
    years = db.scalars(
        select(PolicyYear)
        .where(PolicyYear.client_id.in_(client_ids))
        .order_by(PolicyYear.start_date)
    ).all()
    today = business_today()
    target: dict[str, PolicyYear] = {}
    for year in years:  # ordered by start ASC
        current = target.get(year.client_id)
        year_rank = (
            3
            if year.status == PolicyYearStatus.active and year.start_date > today
            else 2
            if year.start_date <= today <= year.end_date
            else 1
            if year.status == PolicyYearStatus.active
            else 0
            if year.status != PolicyYearStatus.archived
            else -1
        )
        current_rank = (
            -1
            if current is None
            else 3
            if current.status == PolicyYearStatus.active and current.start_date > today
            else 2
            if current.start_date <= today <= current.end_date
            else 1
            if current.status == PolicyYearStatus.active
            else 0
            if current.status != PolicyYearStatus.archived
            else -1
        )
        if year_rank >= 0 and year_rank >= current_rank:
            target[year.client_id] = year
    return target


def _year_label(year: PolicyYear) -> str:
    return f"{year.year} · {year.status.value}"


def _listing_companies(
    db: Session, listing: PanelListing, user: CurrentUser
) -> list[ListingCompanyOut]:
    clients = accessible_clients(
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        user_id=user.user_id,
        db=db,
    )
    if listing.client_id is not None:
        # Legacy pinned listing — only its own company may use it.
        clients = [c for c in clients if c.id == listing.client_id]
    targets = _target_years_by_client(db, [c.id for c in clients])
    year_ids = [y.id for y in targets.values()]
    enabled_years: set[str] = set(
        db.scalars(
            select(PolicyYearPanel.policy_year_id).where(
                PolicyYearPanel.panel_listing_id == listing.id,
                PolicyYearPanel.policy_year_id.in_(year_ids),
            )
        )
    ) if year_ids else set()
    out = []
    for client in clients:
        year = targets.get(client.id)
        out.append(
            ListingCompanyOut(
                client_id=client.id,
                client_name=client.name,
                policy_year_id=year.id if year else None,
                policy_year_label=_year_label(year) if year else None,
                enabled=year is not None and year.id in enabled_years,
            )
        )
    return out


@router.get("/{listing_id}/companies", response_model=list[ListingCompanyOut])
def get_listing_companies(
    listing: PanelListing = Depends(load_panel_listing),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ListingCompanyOut]:
    """Enablement state per company — which companies' current policy years
    have this listing tagged."""
    return _listing_companies(db, listing, user)


@router.put("/{listing_id}/companies", response_model=list[ListingCompanyOut])
def set_listing_companies(
    payload: ListingCompaniesIn,
    listing: PanelListing = Depends(load_panel_listing),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ListingCompanyOut]:
    """Enable this listing for exactly the given companies (on each company's
    target policy year). Companies not listed are disabled on their target
    year only — historical years keep their tags."""
    companies = _listing_companies(db, listing, user)
    by_client = {c.client_id: c for c in companies}
    wanted = set(payload.client_ids)

    unknown = wanted - set(by_client)
    if unknown:
        # Inaccessible or nonexistent company — same posture as the deps.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    no_year = [cid for cid in wanted if by_client[cid].policy_year_id is None]
    if no_year:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "client_has_no_policy_year",
                "message": "Create a policy year for the company before enabling panel lists.",
                "client_ids": no_year,
            },
        )

    changed_on, changed_off = [], []
    for company in companies:
        if company.policy_year_id is None:
            continue
        desired = company.client_id in wanted
        if desired == company.enabled:
            continue
        if desired:
            db.add(
                PolicyYearPanel(
                    policy_year_id=company.policy_year_id,
                    panel_listing_id=listing.id,
                )
            )
            changed_on.append(company.client_id)
        else:
            db.execute(
                delete(PolicyYearPanel).where(
                    PolicyYearPanel.policy_year_id == company.policy_year_id,
                    PolicyYearPanel.panel_listing_id == listing.id,
                )
            )
            changed_off.append(company.client_id)

    if changed_on or changed_off:
        write_audit(
            db,
            user,
            action="panel_listing.companies_update",
            entity_type="panel_listing",
            entity_id=listing.id,
            before={"enabled_client_ids": [c.client_id for c in companies if c.enabled]},
            after={
                "enabled_client_ids": sorted(wanted),
                "enabled": changed_on,
                "disabled": changed_off,
            },
        )
    db.commit()
    return _listing_companies(db, listing, user)


# ── Policy-year tagging ───────────────────────────────────────────────────────


@year_router.get("/{policy_year_id}/panels", response_model=PolicyYearPanelsOut)
def get_policy_year_panels(
    policy_year: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> PolicyYearPanelsOut:
    ids = list(
        db.scalars(
            select(PolicyYearPanel.panel_listing_id).where(
                PolicyYearPanel.policy_year_id == policy_year.id
            )
        )
    )
    return PolicyYearPanelsOut(policy_year_id=policy_year.id, panel_listing_ids=ids)


@year_router.put("/{policy_year_id}/panels", response_model=PolicyYearPanelsOut)
def set_policy_year_panels(
    payload: PolicyYearPanelsIn,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearPanelsOut:
    """Replace the set of panel listings tagged to a policy year — this is
    how a company selects which library entries apply to it.

    Deliberately NOT behind `assert_policy_year_editable` — panels are
    reference data, not priced configuration, and networks change mid-year.
    """
    policy_year = assert_policy_year_for_user(policy_year_id, user, db)
    wanted = list(dict.fromkeys(payload.panel_listing_ids))  # dedupe, keep order
    if wanted:
        # A listing is taggable when it's a shared library entry (client_id
        # NULL) or pinned to this policy year's own client.
        owner_by_id: dict[str, str | None] = dict(
            db.execute(
                select(PanelListing.id, PanelListing.client_id).where(
                    PanelListing.id.in_(wanted)
                )
            ).all()
        )
        for listing_id in wanted:
            if listing_id not in owner_by_id:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "Panel listing not found"
                )
            owner = owner_by_id[listing_id]
            if owner is not None and owner != policy_year.client_id:
                # Existing-but-foreign id: 404 like the deps, and security-log
                # the blocked cross-tenant probe the same way they do.
                raise _deny_cross_tenant(user, "Panel listing", listing_id)

    before = set(
        db.scalars(
            select(PolicyYearPanel.panel_listing_id).where(
                PolicyYearPanel.policy_year_id == policy_year.id
            )
        )
    )
    to_remove = before - set(wanted)
    to_add = [i for i in wanted if i not in before]
    if to_remove:
        db.execute(
            delete(PolicyYearPanel).where(
                PolicyYearPanel.policy_year_id == policy_year.id,
                PolicyYearPanel.panel_listing_id.in_(to_remove),
            )
        )
    for listing_id in to_add:
        db.add(
            PolicyYearPanel(
                policy_year_id=policy_year.id, panel_listing_id=listing_id
            )
        )
    if to_remove or to_add:
        write_audit(
            db,
            user,
            action="policy_year.panels_update",
            entity_type="policy_year",
            entity_id=policy_year.id,
            before={"panel_listing_ids": sorted(before)},
            after={"panel_listing_ids": wanted},
        )
    db.commit()
    return PolicyYearPanelsOut(
        policy_year_id=policy_year.id, panel_listing_ids=wanted
    )
