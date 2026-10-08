"""Resolve the brand a broker firm (and optionally one of its companies) shows.

Layers, later wins per field: the built-in Inspro brand, the firm's row, then
the company's row. A NULL column inherits. Three fields are firm-governed and
never taken from a company row: the verified email From address, the e-card
prefix (frozen onto each new company as `clients.card_id_prefix`) and platform
attribution. Attribution is shown unless the firm row hides it AND the firm
holds the `allow_hide_attribution` entitlement, so revoking the entitlement
brings attribution back without touching the brand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.brand import BRAND_SCOPE_FIRM, BrandProfile
from app.models.client import BrokerFirm, Client

PLATFORM_NAME = "Inspro"

# Today's look, used wherever a firm has not set its own.
DEFAULT_PRODUCT_NAME = PLATFORM_NAME
DEFAULT_SHORT_NAME = PLATFORM_NAME
DEFAULT_PRIMARY_COLOR = "#c11a2b"
DEFAULT_ACCENT_COLOR = "#c11a2b"
DEFAULT_SUPPORT_EMAIL = "helpdesk@inspro.com.sg"
DEFAULT_EMAIL_SENDER_NAME = "Inspro Benefits Portal"
DEFAULT_CARD_PREFIX = "INS"

# Plain-value fields a company row may override, in model attribute names.
_LAYERED = (
    "product_name",
    "short_name",
    "primary_color",
    "accent_color",
    "support_email",
    "support_phone",
    "email_sender_name",
    "email_reply_to",
)


@dataclass(frozen=True)
class Brand:
    product_name: str = DEFAULT_PRODUCT_NAME
    short_name: str = DEFAULT_SHORT_NAME
    primary_color: str = DEFAULT_PRIMARY_COLOR
    accent_color: str = DEFAULT_ACCENT_COLOR
    # slot -> asset metadata ({"id", "content_type", "width", "height", "bytes"}).
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    support_email: str = DEFAULT_SUPPORT_EMAIL
    support_phone: str | None = None
    email_sender_name: str = DEFAULT_EMAIL_SENDER_NAME
    email_reply_to: str | None = None
    # Set only when verified; otherwise mail uses the platform sender address.
    email_from_address: str | None = None
    card_prefix: str = DEFAULT_CARD_PREFIX
    show_platform_attribution: bool = True
    # Which rows contributed (None: built-in only).
    firm_id: str | None = None
    client_id: str | None = None

    @property
    def is_default(self) -> bool:
        return self.firm_id is None and self.client_id is None


DEFAULT_BRAND = Brand()


def _rows(db: Session, firm_id: str, client_id: str | None) -> dict[str, BrandProfile]:
    scopes = [BRAND_SCOPE_FIRM] + ([client_id] if client_id else [])
    rows = db.scalars(
        select(BrandProfile).where(
            BrandProfile.broker_firm_id == firm_id, BrandProfile.scope_key.in_(scopes)
        )
    ).all()
    return {row.scope_key: row for row in rows}


def _layer(values: dict[str, Any], row: BrandProfile) -> None:
    for name in _LAYERED:
        value = getattr(row, name)
        if value not in (None, ""):
            values[name] = value
    for slot, meta in (row.assets or {}).items():
        if meta:
            values["assets"][slot] = meta


def resolve_brand(db: Session, firm_id: str | None, client_id: str | None = None) -> Brand:
    """The brand for `firm_id`, with `client_id`'s overrides when given.

    A company is only layered when it belongs to `firm_id`'s rows (the query is
    keyed by firm), so a company id from another firm adds nothing.
    """
    if not firm_id:
        return DEFAULT_BRAND
    rows = _rows(db, firm_id, client_id)
    firm_row = rows.get(BRAND_SCOPE_FIRM)
    company_row = rows.get(client_id) if client_id else None
    values: dict[str, Any] = {"assets": {}}
    for row in (firm_row, company_row):
        if row is not None:
            _layer(values, row)

    attribution = True
    sender = None
    if firm_row is not None:
        if firm_row.card_prefix:
            values["card_prefix"] = firm_row.card_prefix
        if firm_row.show_platform_attribution is False:
            firm = db.get(BrokerFirm, firm_id)
            attribution = not (firm is not None and firm.allow_hide_attribution)
        if firm_row.email_from_address and firm_row.email_from_verified_at is not None:
            sender = firm_row.email_from_address
    if firm_row is None and company_row is None:
        return DEFAULT_BRAND
    # A rebranded firm never mails as "Inspro Benefits Portal". (Its support
    # email is required by the settings API once it sets a product name.)
    if "email_sender_name" not in values and "product_name" in values:
        values["email_sender_name"] = values["product_name"]
    return Brand(
        **values,
        email_from_address=sender,
        show_platform_attribution=attribution,
        firm_id=firm_id,
        client_id=client_id if company_row is not None else None,
    )


def resolve_client_brand(db: Session, client_id: str | None, *, company: bool = True) -> Brand:
    """The brand of a company's firm, with the company's overrides unless
    `company` is False (broker-facing outputs about the company)."""
    client = db.get(Client, client_id) if client_id else None
    if client is None:
        return DEFAULT_BRAND
    return resolve_brand(db, client.broker_firm_id, client.id if company else None)


def resolve_staff_brand(db: Session, firm_id: str | None) -> Brand:
    """Broker staff see their firm's brand; a master admin (no firm) sees the
    platform owner firm's."""
    if not firm_id:
        from app.core.tenant_resolution import platform_owner_firm

        owner = platform_owner_firm(db)
        firm_id = owner.id if owner is not None else None
    return resolve_brand(db, firm_id)


def firm_card_prefix(db: Session, firm_id: str) -> str | None:
    """The e-card prefix to freeze onto a company created now: the firm's own,
    or None (the original "INS") when the firm has not set one."""
    return db.scalar(
        select(BrandProfile.card_prefix).where(
            BrandProfile.broker_firm_id == firm_id,
            BrandProfile.scope_key == BRAND_SCOPE_FIRM,
        )
    ) or None


def _relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a `#rrggbb` colour."""
    channels = []
    for start in (1, 3, 5):
        value = int(color[start:start + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def readable_foreground(color: str) -> str:
    """Black or white, whichever has the higher WCAG contrast on `color`."""
    luminance = _relative_luminance(color)
    return "#ffffff" if 1.05 / (luminance + 0.05) >= (luminance + 0.05) / 0.05 else "#000000"
