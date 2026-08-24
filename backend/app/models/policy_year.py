"""Policy year — versions per client. Activation snapshots live here."""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import JSON, Base, TimestampMixin, new_uuid

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.client import Client


class PolicyYearStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class PolicyYear(Base, TimestampMixin):
    __tablename__ = "policy_years"
    # Uniqueness on (client_id, start_date) instead of (client_id, year):
    # off-cycle policies (e.g. Sep 2026-Aug 2027 vs Jan 2026-Dec 2026) share
    # a calendar year but cannot share a start date.
    __table_args__ = (
        UniqueConstraint("client_id", "start_date", name="uq_policy_year_start"),
        CheckConstraint(
            "claim_grace_period_days IS NULL OR "
            "claim_grace_period_days BETWEEN 0 AND 3650",
            name="claim_grace_period_days_valid",
        ),
        CheckConstraint(
            "leaver_access_days IS NULL OR leaver_access_days BETWEEN 0 AND 3650",
            name="leaver_access_days_valid",
        ),
        Index(
            "uq_policy_year_active_client",
            "client_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # `year` is the label year — equal to `start_date.year`. Kept for snapshot
    # / display compatibility; primary identity is `start_date`.
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PolicyYearStatus] = mapped_column(
        Enum(PolicyYearStatus), nullable=False, default=PolicyYearStatus.draft
    )
    # Days after the coverage period ends during which members may still submit
    # claims for this year. None = no submission deadline (system default).
    claim_grace_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Days after a member's LAST DAY OF SERVICE during which they keep portal
    # access (read + file claims incurred on or before it). Bounds the member,
    # where `claim_grace_period_days` bounds the year — a submit must satisfy
    # both. None = `member_access.DEFAULT_LEAVER_ACCESS_DAYS`; 0 = access ends
    # on the last day. There is deliberately no "unlimited" value: unlimited is
    # the defect this column exists to close (`docs/LEAVER_ACCESS_PLAN.md`).
    leaver_access_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)

    client: Mapped[Client] = relationship(back_populates="policy_years")
    # passive_deletes defers child removal to the DB's ON DELETE CASCADE — without
    # it, deleting a policy year makes the ORM try to NULL categories.policy_year_id
    # (NOT NULL → IntegrityError) instead of cascade-deleting the categories.
    categories: Mapped[list[Category]] = relationship(
        back_populates="policy_year",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
