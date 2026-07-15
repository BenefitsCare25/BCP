"""LeavePolicy — buy/sell-leave configuration for a policy year.

Members may buy additional annual-leave days or sell some of their entitlement
during enrollment. This phase tracks the *day counts* and enforces bounds only —
there is deliberately no pricing / funding (flex-wallet or payroll) logic yet, so
the model carries no monetary fields. Funding is a documented future extension.

One policy per policy year (upsert-by-year, like FlexScheme).
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class LeavePolicy(Base, TimestampMixin):
    __tablename__ = "leave_policies"
    __table_args__ = (
        UniqueConstraint("policy_year_id", name="uq_leave_policy_year"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    allow_buy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    allow_sell: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Day bounds. Stored as Float so half-day increments serialize cleanly to JSON
    # (consistent with the codebase's use of Float for flex wallet amounts).
    min_buy_days: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    max_buy_days: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    min_sell_days: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    max_sell_days: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    increment_days: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1"
    )
    # Per-day buy/sell leave rate keyed by an employee attribute (grade/designation),
    # NOT by age or product. Shape: ``{"attribute": "<key>", "rates": {<value>: rate}}``.
    # A member buying N days spends N*rate from their flex wallet; selling credits it.
    # Empty bag = leave priced at 0 (days-only, the prior behavior).
    leave_rates: Mapped[dict] = mapped_column(
        JSON(), nullable=False, default=dict, server_default=text("'{}'")
    )
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)
