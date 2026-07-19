"""Panel clinic e-cards — tenant tables.

A `PanelCard` is one uploaded card ARTWORK plus the placement coordinates of
the fields printed on it, keyed by (insurer, panel provider, name) — e.g.
"AIA Singapore / Parkway Shenton / AIA Parkway Shenton". Like `PanelListing`
it is a shared LIBRARY entry (`client_id` NULL): the artwork is uploaded once
by the broker firm and reused by every company on that TPA's panel.

A `PolicyYearCard` assigns a card to one company's policy year and carries the
YEAR-SPECIFIC data the card prints: which insurance product it represents,
which identifier is shown as the member ID, the covered-service badges and the
per-clinic-type remarks. That split is deliberate — artwork changes rarely,
the printed data changes every renewal.

Rendering is a CSS overlay in the portal (`components/portal/MemberCard.tsx`):
placements are stored as FRACTIONS of the artwork's width/height, so one
record drives both the responsive web card and (later) a server-side raster
export at any resolution.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

CARD_FACE_FRONT = "front"
CARD_FACE_BACK = "back"
CARD_FACES = (CARD_FACE_FRONT, CARD_FACE_BACK)

# Artwork the CSS overlay can render as an <img>. Deliberately excludes PDF —
# a PDF page can't be positioned against fractional coordinates in the browser.
CARD_IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MAX_CARD_ARTWORK_BYTES = 8 * 1024 * 1024

# Where a member identifier is read from. `insurer_member_id` is the insurer's
# own number off the roster; `platform_id` is derived by us (see
# `services/panel_cards.platform_member_id`) for panels that don't issue one.
MEMBER_ID_SOURCES: tuple[str, ...] = (
    "insurer_member_id",
    "staff_id",
    "email",
    "national_id_masked",
    "platform_id",
)

# Covered-service badges printed on the card. These are ENTITLEMENTS asserted
# by the broker, NOT derived from the tagged clinic listings: a plan can cover
# X-ray & lab or health screening with no clinic network loaded, and tagging a
# dental network does not mean the plan pays dental.
CARD_SERVICES: tuple[str, ...] = (
    "gp",
    "xray_lab",
    "tcm",
    "dental",
    "specialist",
    "health_screening",
)

CARD_SERVICE_LABELS: dict[str, str] = {
    "gp": "GP",
    "xray_lab": "Xray & Lab",
    "tcm": "TCM",
    "dental": "Dental",
    "specialist": "Specialist",
    "health_screening": "Executive Health Screening",
}

# Free-text notes printed per clinic setting (the legacy "Default Remarks"
# rows). Rendered through the `remark_<key>` placement fields.
CARD_REMARK_KEYS: tuple[str, ...] = (
    "gp",
    "ae",
    "restructured_sp",
    "private_sp",
    "general",
)

CARD_REMARK_LABELS: dict[str, str] = {
    "gp": "GP",
    "ae": "A&E",
    "restructured_sp": "Restructured SP",
    "private_sp": "Private SP",
    "general": "General",
}


class PanelCard(Base, TimestampMixin):
    """Card artwork + field placements — a shared library entry."""

    __tablename__ = "panel_cards"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "insurer", "panel_provider", "name", name="uq_panel_card_combo"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # NULL = shared library entry (every company may assign it); non-NULL pins
    # the card to one company. Filter with `tenant_or_global`.
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    insurer: Mapped[str] = mapped_column(String(64), nullable=False)
    panel_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # Broker-facing card name, e.g. "AIA Parkway Shenton".
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Storage paths (app/core/storage.py). NULL until artwork is uploaded; a
    # card with no front artwork is not renderable and can't be assigned.
    artwork_front_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artwork_front_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artwork_back_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artwork_back_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Artwork aspect ratio (width / height) — the portal reserves the right box
    # before the image loads so positioned fields never jump.
    aspect_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # {"fields": [{"key", "face", "x", "y", "size", "weight", "align", "color",
    #              "uppercase", "max_width"}]} — see schemas/panel_card.py.
    placements: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)

    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    def display_label(self) -> str:
        return self.name or f"{self.insurer} {self.panel_provider}"


class PolicyYearCard(Base, TimestampMixin):
    """Assigns a card to a policy year + product — the member-visibility switch.

    One row per (policy_year, product): a member holds at most one card per
    insurance product, which is what makes "which card do I show at the GP"
    unambiguous.
    """

    __tablename__ = "policy_year_cards"
    __table_args__ = (
        UniqueConstraint(
            "policy_year_id", "product_id", name="uq_policy_year_card_product"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    panel_card_id: Mapped[str] = mapped_column(
        ForeignKey("panel_cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )

    employee_member_id_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="insurer_member_id"
    )
    dependant_member_id_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="insurer_member_id"
    )

    # {"gp": true, "dental": false, ...} — keys from CARD_SERVICES.
    services: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    # {"gp": "...", "ae": "...", ...} — keys from CARD_REMARK_KEYS.
    remarks: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    special_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase 2 seam: let members preview the NEXT benefit year's card before it
    # starts. Stored now so the setting survives; the portal renders the
    # current year only until the future-card lookup ships.
    show_future_cards: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
