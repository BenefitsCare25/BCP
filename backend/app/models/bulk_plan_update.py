"""BulkPlanUpdate — record of a batch coverage change over a population.

A broker composes a *rule* over the roster (``query``) and a *set* of coverage
changes (``changes`` — one per product: move to a plan, decline, or revert to the
cohort default) and applies it in one audited operation. The batch writes
``EmployeePlanOverride`` rows for each valid member; this table records the
request + structured outcome (applied / skipped / errored) for traceability. A
``previewed`` row is never persisted — preview is read-only — so every stored row
represents a real apply.

The record is also the UNDO source: ``result_summary["restore"]`` holds each
written pair's before/after override snapshot, so an undo can put back exactly
what this batch replaced and skip any pair somebody has moved since.

The pre-change-set columns (``product_code`` / ``target_plan_code`` / ``action``
/ ``selector`` / ``dependant_action``) are kept and still written — populated
from the FIRST change — so rows written before the change set, and any reader
that only knows the flat shape, keep working.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class BulkUpdateStatus:
    applied = "applied"  # all targets succeeded
    partially_failed = "partially_failed"  # some rows skipped/errored


class BulkUpdateAction:
    set_plan = "set_plan"  # assign target_plan_code
    decline = "decline"  # opt selected members out of the product
    revert_to_default = "revert_to_default"  # drop the override, back to cohort


class BulkPlanUpdate(Base, TimestampMixin):
    __tablename__ = "bulk_plan_updates"
    # Both UNIQUE — each backs a check-then-act that is otherwise a race (a
    # replayed apply, a double undo). NULLs are distinct in a unique index on
    # both dialects, so rows carrying neither value don't collide.
    __table_args__ = (
        Index("ix_bulk_plan_updates_request", "client_id", "request_id", unique=True),
    )

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
    # {"applied": n, "skipped": n, "errors": n, "rows": [{...}], "restore": [{...}]}.
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    # The selection RULE (a serialized ``MemberQuery``) — what makes a past batch
    # re-runnable and auditable. ``selector`` holds the same object for legacy
    # readers; this column is the one new code reads.
    query: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    # The change SET: [{"product_code", "action", "target_plan_code",
    # "dependant_action"}]. NULL on rows written before multi-product batches.
    changes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON(), nullable=True)
    # Warning codes the broker explicitly accepted, so a year later the record
    # says they were told (see services/bulk_warnings.py).
    acknowledged: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
    # Set on an UNDO batch, pointing at the batch it reverses. An undo is a new
    # batch, never a deletion — history stays append-only.
    undo_of: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True, unique=True
    )
    # Client-generated per apply attempt. A repeat returns the original record
    # instead of applying twice (a double-click, or a retry after a timeout).
    # Indexed WITH client_id (see __table_args__): the lookup is always scoped to
    # a tenant, and a bare request_id index would still scan a firm's rows.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
