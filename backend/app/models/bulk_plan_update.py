"""BulkPlanUpdate — record of a batch plan-reassignment over a list of members.

A broker selects a product + target plan (or decline) and a set of employees
(explicit ids or staff ids), optionally with a dependant-coverage adjustment, and
applies it in one audited operation. The batch writes ``EmployeePlanOverride``
rows for each valid member; this table records the request + structured outcome
(applied / skipped / errored) for traceability. A ``previewed`` row is never
persisted — preview is read-only — so every stored row represents a real apply.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class BulkUpdateStatus:
    applied = "applied"  # all targets succeeded
    partially_failed = "partially_failed"  # some rows skipped/errored


class BulkUpdateAction:
    set_plan = "set_plan"  # assign target_plan_code
    decline = "decline"  # opt selected members out of the product


class BulkPlanUpdate(Base, TimestampMixin):
    __tablename__ = "bulk_plan_updates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    initiated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    target_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BulkUpdateAction.set_plan,
        server_default=BulkUpdateAction.set_plan,
    )
    # The request selector: {"employee_ids": [...]} and/or {"staff_ids": [...]}.
    selector: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    # Optional dependant-coverage change applied alongside the plan: e.g.
    # {"mode": "include_all" | "exclude_all" | "set", "dependant_ids": [...]}.
    dependant_action: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BulkUpdateStatus.applied,
        server_default=BulkUpdateStatus.applied,
    )
    # {"applied": n, "skipped": n, "errors": n, "rows": [{...}]}.
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
