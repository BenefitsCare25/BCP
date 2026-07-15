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

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

USER_STATUS_ACTIVE = "active"
USER_STATUS_INVITED = "invited"
USER_STATUS_DISABLED = "disabled"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # Entra `oid` (object id). NULL until an invited user first signs in.
    external_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # NULL only for system_admin (cross-firm operator).
    broker_firm_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=USER_STATUS_ACTIVE, index=True
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
