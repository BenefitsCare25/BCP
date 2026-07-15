"""Pending user invitations.

A broker_admin (or system_admin) invites an email into a firm with a role.
The invite carries a single-use token; on accept (first Entra sign-in matching
the email, or explicit token redemption) a `User` row is provisioned and the
Entra `oid` is linked. `client_ids` scopes client-role invites to specific
clients.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

INVITE_STATUS_PENDING = "pending"
INVITE_STATUS_ACCEPTED = "accepted"
INVITE_STATUS_REVOKED = "revoked"


class Invitation(Base, TimestampMixin):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    broker_firm_id: Mapped[str] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=INVITE_STATUS_PENDING, index=True
    )
    invited_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # For client-scoped roles: which clients to grant on accept.
    client_ids: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
