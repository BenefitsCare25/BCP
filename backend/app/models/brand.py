"""White-label brand: what a broker firm's sites, emails and documents look like.

A control table (``db/tenancy.CONTROL_TABLES``): the brand is served before
anyone signs in, from the host's firm, before a firm schema is selected.

One firm row (``scope_key = "firm"``) plus optional company rows
(``scope_key = <client id>``) that override it on that company's HR and
employee portals. Every content column is nullable: the resolver
(``services/brand.py``) layers built-in default ← firm ← company, and a NULL
means "inherit".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

BRAND_SCOPE_FIRM = "firm"
# Image slots a brand may fill. Each value in `assets` is
# {"id": "<sha256>.<ext>", "content_type", "width", "height", "bytes"}.
BRAND_ASSET_SLOTS = frozenset({"logo", "mark", "favicon"})


class BrandProfile(Base, TimestampMixin):
    __tablename__ = "brand_profiles"
    __table_args__ = (
        UniqueConstraint("broker_firm_id", "scope_key", name="uq_brand_profiles_firm_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    broker_firm_id: Mapped[str] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # "firm", or the company id for a company override (portable uniqueness).
    scope_key: Mapped[str] = mapped_column(String(40), nullable=False)

    # Product name shown in titles, headers, emails and the authenticator app.
    product_name: Mapped[str | None] = mapped_column(String(80))
    # Web-app manifest short name (home-screen label).
    short_name: Mapped[str | None] = mapped_column(String(30))
    # "#rrggbb". Accessible foregrounds are derived, never stored.
    primary_color: Mapped[str | None] = mapped_column(String(7))
    accent_color: Mapped[str | None] = mapped_column(String(7))
    assets: Mapped[dict[str, Any] | None] = mapped_column(JSON())

    support_email: Mapped[str | None] = mapped_column(String(320))
    support_phone: Mapped[str | None] = mapped_column(String(40))

    # System email identity. The From address is used only once the master
    # admin records it as verified (SPF/DKIM at the broker's domain); until
    # then mail goes from the platform sender with this display name.
    email_sender_name: Mapped[str | None] = mapped_column(String(120))
    email_reply_to: Mapped[str | None] = mapped_column(String(320))
    email_from_address: Mapped[str | None] = mapped_column(String(320))
    email_from_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Prefix for new e-card numbers; existing cards keep theirs.
    card_prefix: Mapped[str | None] = mapped_column(String(8))
    # Firm row only. Hiding needs the firm's `allow_hide_attribution`
    # entitlement; the resolver shows attribution whenever it is missing.
    show_platform_attribution: Mapped[bool | None] = mapped_column(Boolean)

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_by: Mapped[str | None] = mapped_column(String(36))
