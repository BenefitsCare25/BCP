"""Durable, privacy-minimised member claim-email outbox."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

NOTIFICATION_QUEUED = "queued"
NOTIFICATION_SENDING = "sending"
NOTIFICATION_SENT = "sent"
NOTIFICATION_DEAD = "dead"


class ClaimNotification(Base, TimestampMixin):
    __tablename__ = "claim_notifications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'dead')",
            name="status_valid",
        ),
        Index("ix_claim_notifications_delivery", "status", "available_at"),
        Index(
            "uq_claim_notifications_source_message",
            "source_message_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NOTIFICATION_QUEUED
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
