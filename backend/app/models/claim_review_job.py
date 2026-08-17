"""Durable control-plane jobs for tenant claim reviews."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

JOB_STATE_QUEUED = "queued"
JOB_STATE_RUNNING = "running"
JOB_STATE_RETRY_WAIT = "retry_wait"
JOB_STATE_SUCCEEDED = "succeeded"
JOB_STATE_FAILED = "failed"
JOB_STATE_CANCELLED = "cancelled"

ACTIVE_JOB_STATES = (JOB_STATE_QUEUED, JOB_STATE_RUNNING, JOB_STATE_RETRY_WAIT)

STAGE_QUEUED = "queued"
STAGE_DETERMINISTIC = "deterministic"
STAGE_EXTRACTION = "extraction"
STAGE_COMPARISON = "comparison"
STAGE_VISION = "vision"
STAGE_VERDICT = "verdict"
STAGE_PERSIST = "persist"


class ClaimReviewJob(Base, TimestampMixin):
    """One durable execution command, globally visible before tenant routing."""

    __tablename__ = "claim_review_jobs"
    __table_args__ = (
        Index("ix_claim_review_jobs_state_available", "state", "available_at"),
        Index("ix_claim_review_jobs_state_lease", "state", "lease_expires_at"),
        Index("uq_claim_review_jobs_review", "review_id", unique=True),
        Index(
            "uq_claim_review_jobs_active_claim",
            "claim_id",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running', 'retry_wait')"),
            sqlite_where=text("state IN ('queued', 'running', 'retry_wait')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    broker_firm_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    review_id: Mapped[str] = mapped_column(String(36), nullable=False)
    claim_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=JOB_STATE_QUEUED)
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default=STAGE_QUEUED)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
