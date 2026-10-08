"""Control-plane identity: platform users and their per-client grants.

A `User` belongs to one broker firm (except `system_admin`, whose
`broker_firm_id` is NULL — they operate across firms). Broker-role users
(`broker_admin`, `broker_viewer`) implicitly reach every client in their firm.
Client-role users (`client_admin`, `client_hr`) are pinned to specific clients
via `UserClientAccess` rows.

Identity is DB-backed (not claim-derived) so onboarding doesn't require custom
Entra claims — a signed-in Entra user is matched to a `User` row by `oid` or
email.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

USER_STATUS_ACTIVE = "active"
USER_STATUS_INVITED = "invited"
USER_STATUS_DISABLED = "disabled"


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        # Unique per broker firm, not platform-wide: when a client company moves
        # to another broker at renewal, its HR people are onboarded again by the
        # new broker while the old broker keeps its records. Firm-less platform
        # admins are unique among themselves.
        Index("uq_users_firm_email", "broker_firm_id", "email", unique=True),
        Index(
            "uq_users_platform_email",
            "email",
            unique=True,
            postgresql_where=text("broker_firm_id IS NULL"),
            sqlite_where=text("broker_firm_id IS NULL"),
        ),
        Index("uq_users_entra_identity", "external_tid", "external_id", unique=True),
        Index(
            "uq_users_legacy_external_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_tid IS NULL"),
            sqlite_where=text("external_tid IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # Entra `oid` (object id). NULL until an invited user first signs in.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Entra directory (tenant) id of `external_id`. An object id is only unique
    # within its directory, so the binding key is (`external_tid`, `external_id`).
    # NULL on rows bound before multi-directory sign-in (the platform directory).
    external_tid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # NULL only for system_admin (cross-firm operator).
    broker_firm_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=USER_STATUS_ACTIVE, index=True
    )
    broker_mfa_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class UserClientAccess(Base, TimestampMixin):
    """Per-client grant for client-scoped roles. Broker roles don't need rows
    here — their reach is the whole firm."""

    __tablename__ = "user_client_access"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_user_client_access_user_client"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
