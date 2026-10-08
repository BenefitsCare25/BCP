"""Brand settings: how a broker firm's sites, emails and documents look.

Firm console (`/firm/brand`): the people who own the firm's settings —
`firm_admin` in its own firm, and the master admin on the selected company's
firm within its access model (standing access or a grant; a `read` grant is
read-only) — edit the firm's brand and optional per-company overrides. A NULL
field inherits: built-in Inspro ← firm ← company (`services/brand.py`).

Two fields are firm-only. The email From address is used once the master admin
records it as verified (SPF/DKIM at the broker's domain), and setting or
changing it clears that verification. Platform attribution can be hidden only
with the firm's `allow_hide_attribution` entitlement.

Each row carries a `revision` (0 before the row exists): a write naming an
older one is refused with 409 `brand_revision_stale`, so two tabs cannot
silently overwrite each other. Every change is written to the firm's
`audit_log` (entity `brand_profile`), and to `platform_audit_log` when the
master admin makes it. Images are validated and stored by
`services/brand_assets.py`.

Platform console: the master admin records or clears the verification of a
firm's From address (`/platform/firms/{id}/brand/sender-verification`).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.firm import own_firm
from app.api.v1.public import BrandOut, brand_out
from app.core.audit import write_audit, write_platform_audit
from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser
from app.core.deps import assert_platform_firm_access, require_firm_owner, require_system_admin
from app.core.rate_limit import limiter
from app.core.tenant_resolution import require_platform_host
from app.db.session import get_db
from app.db.tenancy import set_search_path
from app.models import BrokerFirm, Client
from app.models.brand import BRAND_SCOPE_FIRM, BrandProfile
from app.services.brand import resolve_brand
from app.services.brand_assets import (
    MAX_ASSET_BYTES,
    BrandAssetError,
    BrandAssetTooLarge,
    asset_url,
    clean_asset,
    store_asset,
)
from app.services.email_template_content import valid_email

router = APIRouter(prefix="/firm/brand", tags=["brand"])
platform_router = APIRouter(
    prefix="/platform/firms/{firm_id}/brand",
    tags=["platform"],
    # As the rest of the platform console: off a platform host it is a 404.
    dependencies=[Depends(require_platform_host), Depends(require_system_admin)],
)

Slot = Literal["logo", "mark", "favicon"]

_COLOR = re.compile(r"^#[0-9a-f]{6}$")
_CARD_PREFIX = re.compile(r"^[A-Z0-9]{2,8}$")
_PHONE = re.compile(r"^\+?[0-9 ().\-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TEXT_LIMITS = {"product_name": 80, "short_name": 30, "email_sender_name": 120}
_LABELS = {
    "product_name": "Product name",
    "short_name": "Short name",
    "email_sender_name": "Email sender name",
    "support_email": "Support email",
    "email_reply_to": "Reply-to address",
    "email_from_address": "From address",
}
# Firm-governed: a company row never sets them.
_FIRM_ONLY = ("email_from_address", "show_platform_attribution", "card_prefix")
_CONTENT_FIELDS = (
    "product_name",
    "short_name",
    "primary_color",
    "accent_color",
    "support_email",
    "support_phone",
    "email_sender_name",
    "email_reply_to",
    "email_from_address",
    "card_prefix",
    "show_platform_attribution",
)
_STALE = "The brand was changed by someone else. Reload it and try again."


# ── Shapes ────────────────────────────────────────────────────────────────────
def _clean_text(value: str | None, label: str, max_length: int) -> str | None:
    """Trimmed text; blank means "inherit" (None)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if _CONTROL.search(value):
        raise ValueError(f"{label} cannot contain control characters.")
    if len(value) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters.")
    return value


class BrandIn(BaseModel):
    """A brand change. Only the fields sent are applied; null (or blank text)
    clears a field so it inherits. `revision` is the one last read."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    product_name: str | None = None
    short_name: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    support_email: str | None = None
    support_phone: str | None = None
    email_sender_name: str | None = None
    email_reply_to: str | None = None
    email_from_address: str | None = None
    card_prefix: str | None = None
    show_platform_attribution: bool | None = None

    @field_validator("product_name", "short_name", "email_sender_name")
    @classmethod
    def _name(cls, value: str | None, info: ValidationInfo) -> str | None:
        name = str(info.field_name)
        return _clean_text(value, _LABELS[name], _TEXT_LIMITS[name])

    @field_validator("primary_color", "accent_color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        value = _clean_text(value, "Colour", 7)
        if value is None:
            return None
        value = value.lower()
        if not _COLOR.fullmatch(value):
            raise ValueError("Colours are six-digit hex values such as #c11a2b.")
        return value

    @field_validator("support_email", "email_reply_to", "email_from_address")
    @classmethod
    def _email(cls, value: str | None, info: ValidationInfo) -> str | None:
        label = _LABELS[str(info.field_name)]
        value = _clean_text(value, label, 254)
        if value is None:
            return None
        value = value.lower()
        if not valid_email(value):
            raise ValueError(f"{label} is not a valid email address.")
        return value

    @field_validator("card_prefix")
    @classmethod
    def _card_prefix(cls, value: str | None) -> str | None:
        value = _clean_text(value, "Card prefix", 8)
        if value is None:
            return None
        value = value.upper()
        if not _CARD_PREFIX.fullmatch(value):
            raise ValueError("The card prefix is 2 to 8 letters or digits.")
        return value

    @field_validator("support_phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        value = _clean_text(value, "Support phone", 40)
        if value is None:
            return None
        if not _PHONE.fullmatch(value) or sum(ch.isdigit() for ch in value) < 3:
            raise ValueError(
                "The support phone may contain digits, spaces and + ( ) - . only."
            )
        return value


class AssetOut(BaseModel):
    id: str
    content_type: str
    width: int
    height: int
    bytes: int
    url: str


class BrandSettingsOut(BaseModel):
    """A brand row as saved (null: inherited). `revision` 0: no row yet."""

    product_name: str | None = None
    short_name: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    support_email: str | None = None
    support_phone: str | None = None
    email_sender_name: str | None = None
    email_reply_to: str | None = None
    email_from_address: str | None = None
    card_prefix: str | None = None
    show_platform_attribution: bool | None = None
    assets: dict[str, AssetOut] = Field(default_factory=dict)
    revision: int = 0
    updated_at: datetime | None = None
    updated_by: str | None = None


class EntitlementsOut(BaseModel):
    allow_hide_attribution: bool


class EmailFromOut(BaseModel):
    address: str | None
    verified_at: datetime | None


class FirmBrandOut(BaseModel):
    settings: BrandSettingsOut
    effective: BrandOut
    entitlements: EntitlementsOut
    email_from: EmailFromOut


class CompanyBrandOut(BaseModel):
    client_id: str
    settings: BrandSettingsOut
    # The company's brand, and the firm brand it inherits from.
    effective: BrandOut
    inherited: BrandOut


class SenderVerificationIn(BaseModel):
    address: str = Field(min_length=3, max_length=320)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _utc(value: datetime | None) -> datetime | None:
    """SQLite (development) hands timestamps back naive; they are UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _firm(db: Session, user: CurrentUser, *, write: bool) -> BrokerFirm:
    """The firm whose brand the caller manages (firm console rules)."""
    firm = own_firm(db, user)
    assert_platform_firm_access(db, user, firm.id, write=write, not_found="Broker firm not found")
    return firm


def _company(db: Session, firm: BrokerFirm, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.broker_firm_id != firm.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return client


def _row(db: Session, firm_id: str, scope_key: str, *, lock: bool = False) -> BrandProfile | None:
    stmt = select(BrandProfile).where(
        BrandProfile.broker_firm_id == firm_id, BrandProfile.scope_key == scope_key
    )
    return db.execute(stmt.with_for_update() if lock else stmt).scalar_one_or_none()


def _settings_out(row: BrandProfile | None) -> BrandSettingsOut:
    if row is None:
        return BrandSettingsOut()
    assets = {
        slot: AssetOut(**meta, url=url)
        for slot, meta in (row.assets or {}).items()
        if meta and (url := asset_url(meta)) is not None
    }
    return BrandSettingsOut(
        **{name: getattr(row, name) for name in _CONTENT_FIELDS},
        assets=assets,
        revision=row.revision,
        updated_at=_utc(row.updated_at),
        updated_by=row.updated_by,
    )


def _firm_out(db: Session, firm: BrokerFirm) -> FirmBrandOut:
    row = _row(db, firm.id, BRAND_SCOPE_FIRM)
    return FirmBrandOut(
        settings=_settings_out(row),
        effective=brand_out(resolve_brand(db, firm.id)),
        entitlements=EntitlementsOut(allow_hide_attribution=firm.allow_hide_attribution),
        email_from=_email_from(row),
    )


def _company_out(db: Session, firm: BrokerFirm, client: Client) -> CompanyBrandOut:
    return CompanyBrandOut(
        client_id=client.id,
        settings=_settings_out(_row(db, firm.id, client.id)),
        effective=brand_out(resolve_brand(db, firm.id, client.id)),
        inherited=brand_out(resolve_brand(db, firm.id)),
    )


def _email_from(row: BrandProfile | None) -> EmailFromOut:
    if row is None:
        return EmailFromOut(address=None, verified_at=None)
    return EmailFromOut(
        address=row.email_from_address, verified_at=_utc(row.email_from_verified_at)
    )


def _check_revision(row: BrandProfile | None, revision: int | None) -> None:
    current = row.revision if row is not None else 0
    if revision is not None and revision != current:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "brand_revision_stale", "message": _STALE, "revision": current,
        })


def _state(row: BrandProfile | None) -> dict[str, Any]:
    """Audit snapshot of a row (asset ids, not their metadata)."""
    if row is None:
        return {}
    state: dict[str, Any] = {name: getattr(row, name) for name in _CONTENT_FIELDS}
    verified = _utc(row.email_from_verified_at)
    state["email_from_verified_at"] = verified.isoformat() if verified else None
    state["assets"] = {slot: (meta or {}).get("id") for slot, meta in (row.assets or {}).items()}
    return state


def _new_row(db: Session, firm: BrokerFirm, client: Client | None) -> BrandProfile:
    row = BrandProfile(
        broker_firm_id=firm.id,
        client_id=client.id if client is not None else None,
        scope_key=client.id if client is not None else BRAND_SCOPE_FIRM,
        revision=0,
    )
    db.add(row)
    return row


def _audit(
    db: Session, user: CurrentUser, firm: BrokerFirm, *, entity_id: str, action: str,
    before: dict[str, Any], after: dict[str, Any], client_id: str | None,
) -> None:
    """Record a brand change in the firm's trail (and the platform's, for the
    master admin). Caller commits."""
    if user.role == ROLE_SYSTEM_ADMIN:
        write_platform_audit(
            db, user, action=f"firm.brand.{action}", entity_type="brand_profile",
            entity_id=entity_id, broker_firm_id=firm.id, client_id=client_id,
            detail={"before": before, "after": after},
        )
    set_search_path(db, firm.id)
    write_audit(
        db, user, action=action, entity_type="brand_profile", entity_id=entity_id,
        before=before, after=after, client_id=client_id,
    )


def _commit(db: Session) -> None:
    """Commit; a concurrent first save of the same row is a stale revision."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "brand_revision_stale", "message": _STALE,
        }) from None


def _touch(row: BrandProfile, user: CurrentUser) -> None:
    row.revision = (row.revision or 0) + 1
    row.updated_by = user.user_id


def _refuse_firm_only(body: BrandIn) -> None:
    sent = [name for name in _FIRM_ONLY if getattr(body, name) is not None]
    if sent:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "brand_firm_only_field",
            "message": "The From address, e-card prefix and platform attribution are "
                       "set for the whole firm, not per company.",
            "fields": sent,
        })


def _refuse_unentitled_hide(firm: BrokerFirm, row: BrandProfile | None, body: BrandIn) -> None:
    """Hiding attribution needs the entitlement; a row already hiding it may
    still be saved (the resolver shows attribution while it is missing)."""
    hiding = "show_platform_attribution" in body.model_fields_set and (
        body.show_platform_attribution is False
    )
    already = row is not None and row.show_platform_attribution is False
    if hiding and not already and not firm.allow_hide_attribution:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "attribution_entitlement_required",
            "message": "Hiding the platform attribution needs the platform's permission "
                       "for this firm. Ask the platform administrator to enable it.",
        })


def _refuse_borrowed_helpdesk(
    db: Session, firm: BrokerFirm, client: Client | None, target: BrandProfile
) -> None:
    """A brand with its own product name needs its own support email, so a
    rebranded site never sends members to the platform's helpdesk."""
    firm_row = target if client is None else _row(db, firm.id, BRAND_SCOPE_FIRM)
    rows = [r for r in (firm_row, None if client is None else target) if r is not None]
    product = next((r.product_name for r in reversed(rows) if r.product_name), None)
    support = next((r.support_email for r in reversed(rows) if r.support_email), None)
    if product and not support:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "brand_support_email_required",
            "message": "Add a support email: members of a renamed site are shown it "
                       "instead of the platform helpdesk.",
        })


def _save(
    db: Session, user: CurrentUser, firm: BrokerFirm, client: Client | None, body: BrandIn,
) -> None:
    """Apply a brand change to the firm row (client None) or a company row,
    checking its revision, and audit it. Commits; no change, no write."""
    scope_key = client.id if client is not None else BRAND_SCOPE_FIRM
    row = _row(db, firm.id, scope_key, lock=True)
    _check_revision(row, body.revision)
    if client is None:
        _refuse_unentitled_hide(firm, row, body)
    before = _state(row)
    target = row if row is not None else _new_row(db, firm, client)
    old_sender = target.email_from_address
    for name in body.model_fields_set - {"revision"}:
        setattr(target, name, getattr(body, name))
    if target.email_from_address != old_sender:
        target.email_from_verified_at = None
    _refuse_borrowed_helpdesk(db, firm, client, target)
    after = _state(target)
    empty = all(after[name] is None for name in _CONTENT_FIELDS)
    if after == before or (row is None and empty):
        db.rollback()
        return
    _touch(target, user)
    db.flush()
    _audit(
        db, user, firm, entity_id=target.id, action="update",
        before=before, after=after, client_id=client.id if client is not None else None,
    )
    _commit(db)


def _scope(db: Session, firm: BrokerFirm, scope: str) -> Client | None:
    return None if scope == BRAND_SCOPE_FIRM else _company(db, firm, scope)


def _scope_out(
    db: Session, firm: BrokerFirm, client: Client | None
) -> FirmBrandOut | CompanyBrandOut:
    return _firm_out(db, firm) if client is None else _company_out(db, firm, client)


# ── Firm brand ────────────────────────────────────────────────────────────────
@router.get("", response_model=FirmBrandOut)
def get_firm_brand(
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> FirmBrandOut:
    """The firm's brand row as saved, the brand it resolves to, whether it may
    hide platform attribution, and its From address with its verification."""
    return _firm_out(db, _firm(db, user, write=False))


@router.put("", response_model=FirmBrandOut)
def put_firm_brand(
    body: BrandIn,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> FirmBrandOut:
    firm = _firm(db, user, write=True)
    _save(db, user, firm, None, body)
    return _firm_out(db, firm)


# ── Company overrides ─────────────────────────────────────────────────────────
@router.get("/companies/{client_id}", response_model=CompanyBrandOut)
def get_company_brand(
    client_id: str,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> CompanyBrandOut:
    firm = _firm(db, user, write=False)
    return _company_out(db, firm, _company(db, firm, client_id))


@router.put("/companies/{client_id}", response_model=CompanyBrandOut)
def put_company_brand(
    client_id: str,
    body: BrandIn,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> CompanyBrandOut:
    """Override the firm brand on this company's HR and employee portals."""
    firm = _firm(db, user, write=True)
    client = _company(db, firm, client_id)
    _refuse_firm_only(body)
    _save(db, user, firm, client, body)
    return _company_out(db, firm, client)


@router.delete("/companies/{client_id}", response_model=CompanyBrandOut)
def delete_company_brand(
    client_id: str,
    revision: int | None = Query(None, ge=0),
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> CompanyBrandOut:
    """Remove the company's overrides; it then shows the firm brand."""
    firm = _firm(db, user, write=True)
    client = _company(db, firm, client_id)
    row = _row(db, firm.id, client.id, lock=True)
    _check_revision(row, revision)
    if row is not None:
        before = _state(row)
        _audit(
            db, user, firm, entity_id=row.id, action="delete",
            before=before, after={}, client_id=client.id,
        )
        db.delete(row)
        _commit(db)
    return _company_out(db, firm, client)


# ── Images ────────────────────────────────────────────────────────────────────
def _set_asset(
    db: Session, user: CurrentUser, firm: BrokerFirm, client: Client | None,
    slot: str, meta: dict[str, Any] | None,
) -> None:
    """Point a row's slot at an image (or clear it with None), audited. Commits."""
    scope_key = client.id if client is not None else BRAND_SCOPE_FIRM
    row = _row(db, firm.id, scope_key, lock=True)
    current = (row.assets or {}).get(slot) if row is not None else None
    if (current or {}).get("id") == (meta or {}).get("id"):
        db.rollback()
        return
    before = _state(row)
    target = row if row is not None else _new_row(db, firm, client)
    assets = {k: v for k, v in (target.assets or {}).items() if k != slot}
    if meta is not None:
        assets[slot] = meta
    target.assets = assets  # a new dict: JSON columns do not track mutation
    _touch(target, user)
    db.flush()
    _audit(
        db, user, firm, entity_id=target.id, action="update",
        before=before, after=_state(target), client_id=client.id if client else None,
    )
    _commit(db)


@router.post("/assets/{slot}", response_model=FirmBrandOut | CompanyBrandOut)
@limiter.limit("20/minute")
def upload_brand_asset(
    request: Request,
    slot: Slot,
    file: UploadFile = File(...),
    scope: str = Query(BRAND_SCOPE_FIRM, min_length=1, max_length=40),
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> FirmBrandOut | CompanyBrandOut:
    """Upload the firm's (`scope=firm`) or a company's (`scope=<client id>`)
    logo, mark or favicon. PNG or WebP (ICO for the favicon), at most 512 KB,
    within the slot's dimensions; stored re-encoded without metadata."""
    firm = _firm(db, user, write=True)
    client = _scope(db, firm, scope)
    try:
        asset = clean_asset(slot, file.file.read(MAX_ASSET_BYTES + 1))
    except BrandAssetTooLarge as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from None
    except BrandAssetError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "brand_asset_invalid", "message": str(exc),
        }) from None
    store_asset(firm.id, asset)
    _set_asset(db, user, firm, client, slot, asset.meta())
    return _scope_out(db, firm, client)


@router.delete("/assets/{slot}", response_model=FirmBrandOut | CompanyBrandOut)
def delete_brand_asset(
    slot: Slot,
    scope: str = Query(BRAND_SCOPE_FIRM, min_length=1, max_length=40),
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> FirmBrandOut | CompanyBrandOut:
    """Stop using an image; the slot then inherits."""
    firm = _firm(db, user, write=True)
    client = _scope(db, firm, scope)
    _set_asset(db, user, firm, client, slot, None)
    return _scope_out(db, firm, client)


# ── Platform: sender verification ─────────────────────────────────────────────
def _platform_firm_row(db: Session, firm_id: str) -> tuple[BrokerFirm, BrandProfile | None]:
    firm = db.execute(
        select(BrokerFirm).where(BrokerFirm.id == firm_id).with_for_update()
    ).scalar_one_or_none()
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    return firm, _row(db, firm.id, BRAND_SCOPE_FIRM, lock=True)


def _record_verification(
    db: Session, user: CurrentUser, firm: BrokerFirm, row: BrandProfile, action: str,
) -> None:
    detail = {
        "address": row.email_from_address,
        "verified_at": (v.isoformat() if (v := _utc(row.email_from_verified_at)) else None),
    }
    write_platform_audit(
        db, user, action=f"firm.brand.sender_{action}", entity_type="brand_profile",
        entity_id=row.id, broker_firm_id=firm.id, detail=detail,
    )
    set_search_path(db, firm.id)
    write_audit(
        db, user, action=f"brand.sender_{action}", entity_type="brand_profile",
        entity_id=row.id, after=detail, client_id=None,
    )
    db.commit()


@platform_router.get("/sender-verification", response_model=EmailFromOut)
def get_sender_verification(
    firm_id: str,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> EmailFromOut:
    """The firm's From address and whether it is verified."""
    if db.get(BrokerFirm, firm_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    return _email_from(_row(db, firm_id, BRAND_SCOPE_FIRM))


@platform_router.post("/sender-verification", response_model=EmailFromOut)
def verify_sender(
    firm_id: str,
    body: SenderVerificationIn,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> EmailFromOut:
    """Record that the firm's From address is verified (SPF/DKIM confirmed at
    its domain). `address` must be the firm's current From address, so a
    verification can never apply to an address changed in the meantime."""
    firm, row = _platform_firm_row(db, firm_id)
    address = body.address.strip().lower()
    if row is None or not row.email_from_address or row.email_from_address != address:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "sender_address_mismatch",
            "message": "This is not the firm's current From address.",
        })
    row.email_from_verified_at = datetime.now(UTC)
    _record_verification(db, user, firm, row, "verify")
    return _email_from(row)


@platform_router.delete("/sender-verification", response_model=EmailFromOut)
def clear_sender_verification(
    firm_id: str,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> EmailFromOut:
    """Withdraw the verification; mail goes from the platform sender again."""
    firm, row = _platform_firm_row(db, firm_id)
    if row is None or row.email_from_verified_at is None:
        db.rollback()
        return _email_from(row)
    row.email_from_verified_at = None
    _record_verification(db, user, firm, row, "unverify")
    return _email_from(row)
