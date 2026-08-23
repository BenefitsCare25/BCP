"""Panel clinic e-cards — broker settings surface.

A `PanelCard` is card ARTWORK plus the fractional placements of the fields
printed on it. Like panel listings it is a shared LIBRARY entry (client_id
NULL): the artwork is uploaded once per insurer/TPA and reused by every
company on that panel.

Assigning a card to a policy year + product (`POST /policy-years/{id}/cards`)
is what makes it visible to that year's members (`GET /portal/cards`), and the
assignment carries the year-specific printed data: which identifier is shown
as the member ID, the covered-service badges, and the per-setting remarks.

Cards are reference/operational data — NOT part of the priced configuration —
so uploads and assignment stay open on the current year, mirroring panel
listings.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    _deny_cross_tenant,
    assert_policy_year_for_user,
    load_panel_card,
    load_policy_year,
    require_client_id,
    tenant_or_global,
)
from app.core.rate_limit import limiter
from app.core.storage import document_path, get_storage
from app.core.uploads import saved_upload
from app.db.base import new_uuid
from app.db.session import get_db
from app.models import PanelCard, PolicyYear, PolicyYearCard, Product
from app.models.panel_card import (
    CARD_FACES,
    CARD_IMAGE_SUFFIXES,
    CARD_REMARK_KEYS,
    CARD_REMARK_LABELS,
    CARD_SERVICE_LABELS,
    CARD_SERVICES,
    MAX_CARD_ARTWORK_BYTES,
    MEMBER_ID_SOURCES,
)
from app.schemas.panel_card import (
    PLACEMENT_FIELD_KEYS,
    CardFieldOption,
    CardOptionsOut,
    CardPlacements,
    PanelCardIn,
    PanelCardOut,
    PanelCardUpdate,
    PolicyYearCardIn,
    PolicyYearCardOut,
)
from app.services.panel_cards import load_year_cards

router = APIRouter(prefix="/panel-cards", tags=["panel-cards"])

# Assignment rides the policy-year path, registered alongside.
year_router = APIRouter(prefix="/policy-years", tags=["panel-cards"])

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# Human labels for the placement editor's field palette.
_PLACEMENT_LABELS: dict[str, str] = {
    "member_name": "Member name",
    "member_id": "Member ID",
    "staff_id": "Staff ID",
    "email": "Email",
    "nric_masked": "NRIC (masked)",
    "company_name": "Company name",
    "policy_number": "Policy number",
    "product_name": "Product name",
    "plan_name": "Plan",
    "effective_date": "Effective date",
    "expiry_date": "Expiry date",
    "insurer": "Insurer",
    "panel_provider": "Panel provider",
    "card_name": "Card name",
    "special_conditions": "Special conditions",
    "dependant_name": "Dependant name",
    "relationship": "Relationship",
    **{f"remark_{k}": f"Remarks — {v}" for k, v in CARD_REMARK_LABELS.items()},
}

_MEMBER_ID_SOURCE_LABELS: dict[str, str] = {
    "insurer_member_id": "Insurer member ID (from roster)",
    "staff_id": "Staff ID",
    "email": "Email address",
    "national_id_masked": "NRIC / FIN (masked)",
    "platform_id": "Platform-generated ID",
}


def _placements_of(card: PanelCard) -> CardPlacements:
    raw = card.placements if isinstance(card.placements, dict) else {}
    return CardPlacements.model_validate(raw)


def _assigned_year_ids(db: Session, card_id: str) -> list[str]:
    return list(
        db.scalars(
            select(PolicyYearCard.policy_year_id).where(
                PolicyYearCard.panel_card_id == card_id
            )
        )
    )


def _card_out(card: PanelCard, assigned_year_ids: list[str]) -> PanelCardOut:
    return PanelCardOut(
        id=card.id,
        insurer=card.insurer,
        panel_provider=card.panel_provider,
        name=card.name,
        display_label=card.display_label(),
        has_front=bool(card.artwork_front_path),
        has_back=bool(card.artwork_back_path),
        aspect_ratio=card.aspect_ratio,
        placements=_placements_of(card),
        assigned_policy_year_ids=assigned_year_ids,
        uploaded_at=card.uploaded_at,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def _assert_unique_combo(
    db: Session, payload: PanelCardIn, exclude_id: str | None = None
) -> None:
    # Uniqueness is enforced within the shared library (client_id NULL); the
    # DB unique constraint can't cover NULLs, so the check lives here.
    stmt = select(PanelCard.id).where(
        PanelCard.client_id.is_(None),
        PanelCard.insurer == payload.insurer,
        PanelCard.panel_provider == payload.panel_provider,
        PanelCard.name == payload.name,
    )
    if exclude_id:
        stmt = stmt.where(PanelCard.id != exclude_id)
    if db.execute(stmt).first() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_panel_card",
                "message": (
                    "A card with this insurer, panel provider and name already "
                    "exists — upload the new artwork there."
                ),
            },
        )


def _validate_face(face: str) -> str:
    if face not in CARD_FACES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"face must be one of {list(CARD_FACES)}",
        )
    return face


@router.get("/options", response_model=CardOptionsOut)
def card_options() -> CardOptionsOut:
    """Vocabulary for the placement editor + assignment form, so the frontend
    never hardcodes keys this API validates."""
    return CardOptionsOut(
        placement_keys=[
            CardFieldOption(key=key, label=_PLACEMENT_LABELS.get(key, key))
            for key in PLACEMENT_FIELD_KEYS
        ],
        member_id_sources=[
            CardFieldOption(key=key, label=_MEMBER_ID_SOURCE_LABELS.get(key, key))
            for key in MEMBER_ID_SOURCES
        ],
        services=[
            CardFieldOption(key=key, label=CARD_SERVICE_LABELS[key])
            for key in CARD_SERVICES
        ],
        remark_keys=[
            CardFieldOption(key=key, label=CARD_REMARK_LABELS[key])
            for key in CARD_REMARK_KEYS
        ],
    )


@router.get("", response_model=list[PanelCardOut])
def list_panel_cards(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PanelCardOut]:
    client_id = require_client_id(user)
    cards = db.scalars(
        select(PanelCard)
        .where(tenant_or_global(PanelCard.client_id, client_id))
        .order_by(PanelCard.insurer, PanelCard.panel_provider, PanelCard.name)
    ).all()
    if not cards:
        return []
    assignments: dict[str, list[str]] = {}
    for card_id, year_id in db.execute(
        select(PolicyYearCard.panel_card_id, PolicyYearCard.policy_year_id).where(
            PolicyYearCard.panel_card_id.in_([c.id for c in cards])
        )
    ).all():
        assignments.setdefault(card_id, []).append(year_id)
    return [_card_out(card, assignments.get(card.id, [])) for card in cards]


@router.post("", response_model=PanelCardOut, status_code=status.HTTP_201_CREATED)
def create_panel_card(
    payload: PanelCardIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelCardOut:
    """Create a shared library card — artwork is uploaded separately."""
    _assert_unique_combo(db, payload)
    card = PanelCard(
        client_id=None,
        insurer=payload.insurer,
        panel_provider=payload.panel_provider,
        name=payload.name,
    )
    db.add(card)
    db.flush()
    write_audit(
        db,
        user,
        action="panel_card.create",
        entity_type="panel_card",
        entity_id=card.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(card)
    return _card_out(card, [])


@router.patch("/{card_id}", response_model=PanelCardOut)
def update_panel_card(
    payload: PanelCardUpdate,
    card: PanelCard = Depends(load_panel_card),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelCardOut:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _card_out(card, _assigned_year_ids(db, card.id))
    merged = PanelCardIn(
        insurer=changes.get("insurer", card.insurer),
        panel_provider=changes.get("panel_provider", card.panel_provider),
        name=changes.get("name", card.name),
    )
    _assert_unique_combo(db, merged, exclude_id=card.id)
    before = {
        "insurer": card.insurer,
        "panel_provider": card.panel_provider,
        "name": card.name,
    }
    card.insurer = merged.insurer
    card.panel_provider = merged.panel_provider
    card.name = merged.name
    write_audit(
        db,
        user,
        action="panel_card.update",
        entity_type="panel_card",
        entity_id=card.id,
        before=before,
        after=changes,
    )
    db.commit()
    db.refresh(card)
    return _card_out(card, _assigned_year_ids(db, card.id))


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_panel_card(
    card: PanelCard = Depends(load_panel_card),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    blobs = [p for p in (card.artwork_front_path, card.artwork_back_path) if p]
    write_audit(
        db,
        user,
        action="panel_card.delete",
        entity_type="panel_card",
        entity_id=card.id,
        before={
            "insurer": card.insurer,
            "panel_provider": card.panel_provider,
            "name": card.name,
            "assigned_policy_year_ids": _assigned_year_ids(db, card.id),
        },
    )
    db.delete(card)  # assignments cascade
    db.commit()
    # Bytes go only AFTER the row is gone (same ordering as the artwork
    # upload/delete paths): a failed commit would otherwise leave a live card
    # pointing at deleted blobs, 502-ing for every member holding it.
    storage = get_storage()
    for path in blobs:
        storage.delete(path)


@router.put("/{card_id}/placements", response_model=PanelCardOut)
def set_card_placements(
    payload: CardPlacements,
    card: PanelCard = Depends(load_panel_card),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelCardOut:
    """Replace the card's field placements (the drag-editor's save)."""
    before = _placements_of(card).model_dump()
    card.placements = payload.model_dump()
    write_audit(
        db,
        user,
        action="panel_card.placements_update",
        entity_type="panel_card",
        entity_id=card.id,
        before=before,
        after={"field_count": len(payload.fields)},
    )
    db.commit()
    db.refresh(card)
    return _card_out(card, _assigned_year_ids(db, card.id))


# ── Artwork ──────────────────────────────────────────────────────────────────


def _artwork_fields(face: str) -> tuple[str, str]:
    return (f"artwork_{face}_path", f"artwork_{face}_mime")


@router.post("/{card_id}/artwork/{face}", response_model=PanelCardOut)
@limiter.limit("20/minute")
async def upload_card_artwork(
    request: Request,
    face: str,
    file: Annotated[UploadFile, File(description="Card artwork image")],
    card: PanelCard = Depends(load_panel_card),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelCardOut:
    """Upload (replacing) one face of the card artwork.

    The image's aspect ratio is stamped on the card so the portal can reserve
    the right box before the artwork loads — positioned fields would otherwise
    jump on first paint.
    """
    _validate_face(face)
    path_field, mime_field = _artwork_fields(face)
    previous = getattr(card, path_field)

    async with saved_upload(
        file, set(CARD_IMAGE_SUFFIXES), max_bytes=MAX_CARD_ARTWORK_BYTES
    ) as tmp_path:
        try:
            from PIL import Image

            with Image.open(tmp_path) as image:
                width, height = image.size
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Artwork could not be read as an image.",
            ) from exc
        if not width or not height:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Artwork has no dimensions."
            )
        suffix = tmp_path.suffix.lower()
        storage_path = document_path(
            user.broker_firm_id,
            # Library cards belong to the firm, not a company.
            card.client_id or "library",
            "panel_card",
            card.id,
            f"{face}-{new_uuid()}",
            suffix,
        )
        with tmp_path.open("rb") as stream:
            get_storage().save(stream, storage_path)

    setattr(card, path_field, storage_path)
    setattr(card, mime_field, _MIME_BY_SUFFIX.get(suffix, "application/octet-stream"))
    # The front face defines the geometry the placements are relative to.
    if face == "front":
        card.aspect_ratio = round(width / height, 6)
    card.uploaded_at = datetime.now(UTC)
    card.uploaded_by = user.user_id

    write_audit(
        db,
        user,
        action="panel_card.artwork_upload",
        entity_type="panel_card",
        entity_id=card.id,
        after={
            "face": face,
            "filename": (file.filename or "")[:255],
            "width": width,
            "height": height,
        },
    )
    db.commit()
    db.refresh(card)
    # Replaced bytes are removed only after the new path is committed, so a
    # failed commit can never leave the card pointing at a deleted blob.
    if previous and previous != storage_path:
        get_storage().delete(previous)
    return _card_out(card, _assigned_year_ids(db, card.id))


@router.get("/{card_id}/artwork/{face}")
def get_card_artwork(
    face: str,
    card: PanelCard = Depends(load_panel_card),
) -> Response:
    """Serve card artwork to the broker UI (config editor + employee view)."""
    _validate_face(face)
    path_field, mime_field = _artwork_fields(face)
    path = getattr(card, path_field)
    if not path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No artwork uploaded")
    try:
        content = get_storage().read(path)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Artwork could not be retrieved"
        ) from exc
    return Response(
        content=content,
        media_type=getattr(card, mime_field) or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.delete("/{card_id}/artwork/{face}", response_model=PanelCardOut)
def delete_card_artwork(
    face: str,
    card: PanelCard = Depends(load_panel_card),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelCardOut:
    _validate_face(face)
    path_field, mime_field = _artwork_fields(face)
    path = getattr(card, path_field)
    if not path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No artwork uploaded")
    setattr(card, path_field, None)
    setattr(card, mime_field, None)
    if face == "front":
        card.aspect_ratio = None
    write_audit(
        db,
        user,
        action="panel_card.artwork_delete",
        entity_type="panel_card",
        entity_id=card.id,
        before={"face": face},
    )
    db.commit()
    get_storage().delete(path)
    db.refresh(card)
    return _card_out(card, _assigned_year_ids(db, card.id))


# ── Policy-year assignment ───────────────────────────────────────────────────


def _assignment_out(
    assignment: PolicyYearCard, card: PanelCard, product: Product
) -> PolicyYearCardOut:
    return PolicyYearCardOut(
        id=assignment.id,
        policy_year_id=assignment.policy_year_id,
        panel_card_id=assignment.panel_card_id,
        card_name=card.name,
        product_id=product.id,
        product_code=product.code,
        product_name=product.display_name,
        employee_member_id_source=assignment.employee_member_id_source,
        dependant_member_id_source=assignment.dependant_member_id_source,
        services={k: bool(v) for k, v in (assignment.services or {}).items()},
        remarks={k: v for k, v in (assignment.remarks or {}).items() if v},
        special_conditions=assignment.special_conditions,
        show_future_cards=assignment.show_future_cards,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _hydrate(db: Session, assignments: list[PolicyYearCard]) -> list[PolicyYearCardOut]:
    if not assignments:
        return []
    cards = {
        c.id: c
        for c in db.scalars(
            select(PanelCard).where(
                PanelCard.id.in_({a.panel_card_id for a in assignments})
            )
        )
    }
    products = {
        p.id: p
        for p in db.scalars(
            select(Product).where(Product.id.in_({a.product_id for a in assignments}))
        )
    }
    out = []
    for assignment in assignments:
        card = cards.get(assignment.panel_card_id)
        product = products.get(assignment.product_id)
        if card is None or product is None:
            continue
        out.append(_assignment_out(assignment, card, product))
    return out


def _resolve_assignment_refs(
    db: Session,
    payload: PolicyYearCardIn,
    policy_year: PolicyYear,
    user: CurrentUser,
) -> tuple[PanelCard, Product]:
    card = db.get(PanelCard, payload.panel_card_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel card not found")
    if card.client_id is not None and card.client_id != policy_year.client_id:
        # Existing-but-foreign id: 404 like the deps, and security-log the
        # blocked cross-tenant probe the same way they do.
        raise _deny_cross_tenant(user, "Panel card", card.id)
    if not card.artwork_front_path:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "card_has_no_artwork",
                "message": "Upload the front artwork before assigning this card.",
            },
        )
    product = db.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    if product.client_id is not None and product.client_id != policy_year.client_id:
        raise _deny_cross_tenant(user, "Product", product.id)
    return card, product


@year_router.get("/{policy_year_id}/cards", response_model=list[PolicyYearCardOut])
def list_policy_year_cards(
    policy_year: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> list[PolicyYearCardOut]:
    return _hydrate(db, load_year_cards(db, policy_year.id))


@year_router.post(
    "/{policy_year_id}/cards",
    response_model=PolicyYearCardOut,
    status_code=status.HTTP_201_CREATED,
)
def create_policy_year_card(
    payload: PolicyYearCardIn,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearCardOut:
    """Assign a card to this benefit year for one product — the switch that
    makes it visible to the year's members.

    Deliberately NOT behind `assert_policy_year_editable`: cards are
    operational reference data (artwork and remarks change mid-year), like
    panel listings.
    """
    policy_year = assert_policy_year_for_user(policy_year_id, user, db)
    card, product = _resolve_assignment_refs(db, payload, policy_year, user)
    existing = db.execute(
        select(PolicyYearCard.id).where(
            PolicyYearCard.policy_year_id == policy_year.id,
            PolicyYearCard.product_id == product.id,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_product_card",
                "message": (
                    f"{product.display_name} already has a card for this benefit "
                    "year — edit or remove it first."
                ),
            },
        )
    assignment = PolicyYearCard(
        policy_year_id=policy_year.id,
        panel_card_id=card.id,
        product_id=product.id,
        employee_member_id_source=payload.employee_member_id_source,
        dependant_member_id_source=payload.dependant_member_id_source,
        services=payload.services,
        remarks=payload.remarks,
        special_conditions=(payload.special_conditions or "").strip() or None,
        show_future_cards=payload.show_future_cards,
    )
    db.add(assignment)
    db.flush()
    write_audit(
        db,
        user,
        action="policy_year.card_assign",
        entity_type="policy_year",
        entity_id=policy_year.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(assignment)
    return _assignment_out(assignment, card, product)


def _load_assignment(
    db: Session,
    policy_year: PolicyYear,
    assignment_id: str,
    user: CurrentUser,
) -> PolicyYearCard:
    assignment = db.get(PolicyYearCard, assignment_id)
    if assignment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card assignment not found")
    if assignment.policy_year_id != policy_year.id:
        # Existing-but-foreign id: 404 like the deps, and security-log the
        # blocked probe the same way they do — this is the one path where an
        # attacker can enumerate ids using a policy year they legitimately own.
        raise _deny_cross_tenant(user, "Card assignment", assignment_id)
    return assignment


@year_router.put(
    "/{policy_year_id}/cards/{assignment_id}", response_model=PolicyYearCardOut
)
def update_policy_year_card(
    payload: PolicyYearCardIn,
    policy_year_id: str,
    assignment_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYearCardOut:
    policy_year = assert_policy_year_for_user(policy_year_id, user, db)
    assignment = _load_assignment(db, policy_year, assignment_id, user)
    card, product = _resolve_assignment_refs(db, payload, policy_year, user)
    if product.id != assignment.product_id:
        clash = db.execute(
            select(PolicyYearCard.id).where(
                PolicyYearCard.policy_year_id == policy_year.id,
                PolicyYearCard.product_id == product.id,
                PolicyYearCard.id != assignment.id,
            )
        ).first()
        if clash is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "duplicate_product_card",
                    "message": (
                        f"{product.display_name} already has a card for this "
                        "benefit year."
                    ),
                },
            )
    before = {
        "panel_card_id": assignment.panel_card_id,
        "product_id": assignment.product_id,
        "employee_member_id_source": assignment.employee_member_id_source,
        "dependant_member_id_source": assignment.dependant_member_id_source,
        "services": assignment.services,
        "remarks": assignment.remarks,
        "show_future_cards": assignment.show_future_cards,
    }
    assignment.panel_card_id = card.id
    assignment.product_id = product.id
    assignment.employee_member_id_source = payload.employee_member_id_source
    assignment.dependant_member_id_source = payload.dependant_member_id_source
    assignment.services = payload.services
    assignment.remarks = payload.remarks
    assignment.special_conditions = (payload.special_conditions or "").strip() or None
    assignment.show_future_cards = payload.show_future_cards
    write_audit(
        db,
        user,
        action="policy_year.card_update",
        entity_type="policy_year",
        entity_id=policy_year.id,
        before=before,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(assignment)
    return _assignment_out(assignment, card, product)


@year_router.delete(
    "/{policy_year_id}/cards/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_policy_year_card(
    policy_year_id: str,
    assignment_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    policy_year = assert_policy_year_for_user(policy_year_id, user, db)
    assignment = _load_assignment(db, policy_year, assignment_id, user)
    write_audit(
        db,
        user,
        action="policy_year.card_unassign",
        entity_type="policy_year",
        entity_id=policy_year.id,
        before={
            "panel_card_id": assignment.panel_card_id,
            "product_id": assignment.product_id,
        },
    )
    db.delete(assignment)
    db.commit()
