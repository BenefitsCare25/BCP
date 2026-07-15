"""Dependant (Layer 3) — linked to employee via multi-key strategy."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

DEPENDANT_STATUS_ACTIVE = "active"
# Portal self-added dependants wait for broker approval before they count
# toward family status / flex wallet sizing.
DEPENDANT_STATUS_PENDING = "pending_approval"
DEPENDANT_STATUS_REJECTED = "rejected"
# Soft-terminated via an ADC movement file (dependant left the plan). Excluded
# from coverage/flex like the employee terminated state; history preserved.
DEPENDANT_STATUS_TERMINATED = "terminated"


class Dependant(Base, TimestampMixin):
    __tablename__ = "dependants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attribute_values: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    link_method: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="staff_id | id_no | name | unlinked"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    # Canonicalized dependant NRIC/FIN — dedup identity key (falls back to
    # employee + name + DOB when blank). Indexed, nullable, app-enforced-unique.
    national_id_normalized: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # Effective date of a soft-termination (ADC deletion). NULL while active.
    terminated_effective: Mapped[date | None] = mapped_column(Date, nullable=True)
