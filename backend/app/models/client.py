"""Broker firm + Client (tenant) models."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid

DEFAULT_AI_MONTHLY_TOKEN_BUDGET = 100_000

if TYPE_CHECKING:
    from app.models.client_ai_config import ClientAIConfig
    from app.models.policy_year import PolicyYear


class BrokerFirm(Base, TimestampMixin):
    __tablename__ = "broker_firms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    clients: Mapped[list[Client]] = relationship(back_populates="broker_firm")


class Client(Base, TimestampMixin):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # The broker's INTERNAL short name ("CDL"). It is what every broker-facing
    # list, switcher and heading prints, and it is deliberately not the
    # company's registered name — see `legal_name`.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The registered company name ("City Developments Limited"), NULL until a
    # broker fills it in.
    #
    # Kept separate from `name` rather than replacing it, because the two are
    # read by different audiences and neither substitutes for the other: `name`
    # is a handle a broker scans a list by, and shortening the legal name to
    # make it scannable is exactly what produced "CDL" in the first place. The
    # member portal is the surface that needs the real one — a member has no
    # idea what their employer's entry in a broker's tool is called.
    #
    # NOT wired into the placement slip. Its `Policyholder` line resolves
    # through `ProductSetup.answers["header"]`, which is a per-year PLACEMENT
    # fact captured off the insurer's own slip; a company-level field silently
    # overriding it would rewrite a legal document as a side effect of an admin
    # edit. If that fallback is ever wanted it belongs in `slip_export/header.py`
    # as a deliberate change with its own test.
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    broker_firm_id: Mapped[str] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="RESTRICT"), nullable=False
    )
    ai_monthly_token_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_AI_MONTHLY_TOKEN_BUDGET
    )
    # Subdomain segment for tenant-per-subdomain routing (`{slug}.portal.<base>`,
    # `{slug}.hr.<base>`). NULL until the tenant's subdomains go live; a single
    # DNS label (see `core.tenancy_host.validate_slug`). The per-surface flags
    # are kill-switches independent of `slug`.
    slug: Mapped[str | None] = mapped_column(
        String(63), unique=True, nullable=True, index=True
    )
    portal_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    hr_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )

    broker_firm: Mapped[BrokerFirm] = relationship(back_populates="clients")
    policy_years: Mapped[list[PolicyYear]] = relationship(back_populates="client")
    ai_config: Mapped[ClientAIConfig | None] = relationship(
        back_populates="client",
        uselist=False,
        cascade="all, delete-orphan",
    )
