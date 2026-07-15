"""Audit log — every mutation writes a row here."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Who acted: "user" (broker/HR platform user) or "member" (portal member).
    # NULL means "user" (rows predating the portal).
    actor_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Acting member account for portal-originated events (plain String, like
    # user_id — audit rows outlive account deletion).
    member_account_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    # Subject employee for per-member history views (coverage timeline). Plain
    # String (like user_id, not an FK) so audit rows outlive the employee row and
    # retention isn't coupled to employee deletion. NULL for non-member events.
    employee_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    cross_tenant_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
