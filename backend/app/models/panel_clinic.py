"""Panel clinic locator — tenant tables.

A `PanelListing` is one uploaded clinic network list, keyed by
(insurer, panel provider, country, clinic type) — e.g. "AIA-SG / Alliance /
SG / GP". `PanelClinic` rows are its clinics (replaced wholesale on each
upload). `PolicyYearPanel` tags listings to a policy year; the member portal
resolves clinics ONLY through the tags on the member's active policy year.

Listings form a shared LIBRARY: `client_id` is NULL for library entries
(uploaded once, selectable by every company — the `tenant_or_global` pattern,
like `Product`). A non-NULL `client_id` pins a listing to one company. Each
company chooses which entries apply via `PolicyYearPanel` — tagging is the
member-visibility switch, so sharing the library never leaks clinics to a
company that hasn't enabled them.

Listings are reference data, not part of the priced configuration snapshot —
tagging stays open on active years (panel networks change mid-year) like
other operational writes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import JSON, Base, TimestampMixin, new_uuid

CLINIC_TYPE_GP = "gp"
CLINIC_TYPE_TCM = "tcm"
CLINIC_TYPE_DENTAL = "dental"
CLINIC_TYPE_SPECIALIST = "sp"

CLINIC_TYPES = frozenset(
    {CLINIC_TYPE_GP, CLINIC_TYPE_TCM, CLINIC_TYPE_DENTAL, CLINIC_TYPE_SPECIALIST}
)

PANEL_COUNTRIES = frozenset({"SG", "MY"})

# Display names for a (country, clinic_type) pair. GP splits by country —
# brokers and members know these lists as "SG GP" vs "JB GP".
CLINIC_TYPE_LABELS: dict[tuple[str, str], str] = {
    ("SG", CLINIC_TYPE_GP): "SG GP",
    ("MY", CLINIC_TYPE_GP): "JB GP",
    ("SG", CLINIC_TYPE_TCM): "TCM",
    ("MY", CLINIC_TYPE_TCM): "TCM (MY)",
    ("SG", CLINIC_TYPE_DENTAL): "Dental",
    ("MY", CLINIC_TYPE_DENTAL): "Dental (MY)",
    ("SG", CLINIC_TYPE_SPECIALIST): "Specialist",
    ("MY", CLINIC_TYPE_SPECIALIST): "Specialist (MY)",
}


def clinic_type_label(country: str, clinic_type: str) -> str:
    return CLINIC_TYPE_LABELS.get(
        (country, clinic_type), f"{clinic_type.upper()} ({country})"
    )


class PanelListing(Base, TimestampMixin):
    __tablename__ = "panel_listings"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "insurer",
            "panel_provider",
            "country",
            "clinic_type",
            name="uq_panel_listing_combo",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # NULL = shared library entry (visible to every company); non-NULL pins
    # the listing to one company. Filter with `tenant_or_global`.
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    insurer: Mapped[str] = mapped_column(String(64), nullable=False)
    panel_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    clinic_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Optional display label; defaults to "<insurer> <provider> <type>" when blank.
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    clinics: Mapped[list[PanelClinic]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", passive_deletes=True
    )

    def display_label(self) -> str:
        return self.label or (
            f"{self.insurer} {self.panel_provider} "
            f"{clinic_type_label(self.country, self.clinic_type)}"
        )


class PanelClinic(Base, TimestampMixin):
    __tablename__ = "panel_clinics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    panel_listing_id: Mapped[str] = mapped_column(
        ForeignKey("panel_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    specialty: Mapped[str | None] = mapped_column(String(128), nullable=True)
    doctor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # {"mon_fri": ..., "sat": ..., "sun": ..., "public_holiday": ...}
    hours: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_map_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    listing: Mapped[PanelListing] = relationship(back_populates="clinics")


class PolicyYearPanel(Base, TimestampMixin):
    """Tags a panel listing to a policy year — the member-visibility switch."""

    __tablename__ = "policy_year_panels"
    __table_args__ = (
        UniqueConstraint(
            "policy_year_id", "panel_listing_id", name="uq_policy_year_panel"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    panel_listing_id: Mapped[str] = mapped_column(
        ForeignKey("panel_listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
