"""Platform control plane: customer domains, master-admin access, platform audit.

All three live in ``public`` (``db/tenancy.CONTROL_TABLES``): a request's host is
resolved to a broker firm before any firm schema is known, and platform actions
span firms.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

# Which part of the product a hostname serves.
DOMAIN_SURFACE_ALL = "all"
DOMAIN_SURFACE_STAFF = "staff"  # broker staff app
DOMAIN_SURFACE_CLIENT = "client"  # HR portal + employee portal
DOMAIN_SURFACES = frozenset({DOMAIN_SURFACE_ALL, DOMAIN_SURFACE_STAFF, DOMAIN_SURFACE_CLIENT})

DOMAIN_STATUS_PENDING = "pending"
DOMAIN_STATUS_ACTIVE = "active"
DOMAIN_STATUS_DISABLED = "disabled"
DOMAIN_STATUSES = frozenset({DOMAIN_STATUS_PENDING, DOMAIN_STATUS_ACTIVE, DOMAIN_STATUS_DISABLED})

GRANT_SCOPE_READ = "read"
GRANT_SCOPE_WRITE = "write"
GRANT_SCOPES = frozenset({GRANT_SCOPE_READ, GRANT_SCOPE_WRITE})
# Longest a single master-admin access grant may last.
MAX_GRANT_HOURS = 8

# How a broker firm's staff sign in.
IDP_ENTRA = "entra"  # the broker's own Microsoft 365 directory
IDP_LOCAL = "local"  # email + password, two-factor mandatory
IDP_KINDS = frozenset({IDP_ENTRA, IDP_LOCAL})


class TenantDomain(Base, TimestampMixin):
    """A hostname that serves one broker firm (its own white-label domain).

    Only ``active`` rows route traffic. A row starts ``pending`` until the
    domain's ownership and certificate are confirmed at the edge.
    """

    __tablename__ = "tenant_domains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    broker_firm_id: Mapped[str] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Lowercase, no port, no trailing dot.
    hostname: Mapped[str] = mapped_column(String(253), nullable=False, unique=True, index=True)
    surface: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DOMAIN_SURFACE_ALL, server_default=DOMAIN_SURFACE_ALL
    )
    # The firm's canonical host for links in emails, per surface.
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DOMAIN_STATUS_PENDING,
        server_default=DOMAIN_STATUS_PENDING,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PlatformAccessGrant(Base, TimestampMixin):
    """Time-limited, reason-stated access by the master admin to another firm.

    The master admin (``system_admin``) has standing access to the platform
    owner's firm only. Every other firm's data needs an unexpired, unrevoked
    grant; creation and revocation are written to ``platform_audit_log`` and to
    that firm's own ``audit_log`` so the firm can see who entered and why.
    """

    __tablename__ = "platform_access_grants"
    __table_args__ = (Index("ix_platform_access_grants_user_firm", "user_id", "broker_firm_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_firm_id: Mapped[str] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(8), nullable=False, default=GRANT_SCOPE_READ)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PlatformAuditLog(Base):
    """Append-only record of platform actions (firms, domains, access grants).

    Plain-string references, like ``audit_log``: rows outlive the firms and
    users they describe. Postgres rejects UPDATE and DELETE with a trigger.
    """

    __tablename__ = "platform_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    broker_firm_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class IdentityProvider(Base, TimestampMixin):
    """A sign-in method a broker firm offers its staff.

    A firm may enable Microsoft 365 (its own Entra directory, by tenant id),
    email + password, or both. HR users and employees are unaffected: they use
    their own credential surfaces.
    """

    __tablename__ = "identity_providers"
    __table_args__ = (
        UniqueConstraint("broker_firm_id", "kind", name="uq_identity_providers_firm_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    broker_firm_id: Mapped[str] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Entra only: the broker's directory (tenant) id. Tokens are accepted only
    # when their `tid` equals it and their issuer is that directory's.
    entra_tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Entra only: also require the platform authenticator after Microsoft.
    require_platform_mfa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    display_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
