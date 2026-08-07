"""Single create-or-update path for EmployeePlanOverride rows.

Four callers write overrides — the manual-admin router, enrollment projection,
deemed-decline finalization, and bulk updates. Centralizing the upsert here keeps
the field set, the declined→plan_code=None rule, and the sparse semantics in one
place so they can't drift.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmployeePlanOverride

# Sentinel: "leave this column unchanged on update / default on create".
_KEEP: Any = object()

# `BulkPlanUpdate.product_code` for a per-member coverage revert. A revert spans
# every overridden product, so no single code names it — and this value is what
# marks the row as NOT a re-runnable selection: replaying it would look up a
# product called "(coverage revert)" and 404. An UNDO of a revert copies the
# source batch's product_code, so the same test catches both rows.
REVERT_BATCH_PRODUCT_CODE = "(coverage revert)"


def is_revert_batch(product_code: str | None) -> bool:
    """Is this batch a per-member coverage revert (not a re-runnable selection)?"""
    return product_code == REVERT_BATCH_PRODUCT_CODE


def override_snapshot(row: EmployeePlanOverride | None) -> dict[str, Any] | None:
    """Audit-friendly projection of an override row (or None)."""
    if row is None:
        return None
    return {
        "product_code": row.product_code,
        "plan_code": row.plan_code,
        "tier_category_id": row.tier_category_id,
        "declined": row.declined,
        "covered_dependant_ids": row.covered_dependant_ids,
        "dependant_option_ids": row.dependant_option_ids,
        "flex_price_tag": row.flex_price_tag,
        "source": row.source,
    }


def restore_snapshot(row: EmployeePlanOverride | None) -> dict[str, Any] | None:
    """The override's full RESTORABLE state — what an undo needs to put back.

    ``override_snapshot`` plus ``source_ref``: an undo has to restore WHERE the
    coverage came from, not just what it was. Restoring an enrollment-projected
    override as a bulk one would lose the link to the election that produced it.

    Lives here, beside the writer, because two undo producers depend on the
    shape being IDENTICAL: bulk apply and the per-member coverage revert both
    feed ``bulk_plan_update.undo_batch``, which detects a superseded pair by
    comparing ``restore_snapshot(current) != entry["after"]``. Two spellings of
    this projection would make every revert look superseded and silently refuse
    to undo.
    """
    snap = override_snapshot(row)
    if snap is None or row is None:
        return None
    return {**snap, "source_ref": row.source_ref}


def restore_entry(
    employee: Any,
    *,
    product_id: str,
    product_code: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    """One entry of an undo's ``restore`` list, in the shape ``undo_batch`` reads."""
    return {
        "employee_id": employee.id,
        "product_id": product_id,
        "product_code": product_code,
        "staff_id": employee.staff_id,
        "employee_name": employee.employee_name,
        "before": before,
        "after": after,
    }


def upsert_override(
    db: Session,
    *,
    employee_id: str,
    policy_year_id: str,
    client_id: str,
    product_id: str,
    product_code: str,
    declined: bool,
    plan_code: str | None,
    source: str,
    source_ref: str | None = None,
    modified_by: str | None = None,
    covered_dependant_ids: Any = _KEEP,
    effective_from: Any = _KEEP,
    tier_category_id: Any = _KEEP,
    flex_price_tag: Any = _KEEP,
    dependant_option_ids: Any = _KEEP,
) -> tuple[EmployeePlanOverride, dict[str, Any] | None]:
    """Create or update the (employee, product) override. Caller commits.

    Returns ``(row, before)`` where ``before`` is the pre-mutation snapshot (None
    when the row is newly created) so the caller can write an audit diff without a
    second query. ``covered_dependant_ids`` / ``effective_from`` left at the
    sentinel are not touched (preserving any existing value on update).
    """
    row = db.execute(
        select(EmployeePlanOverride).where(
            EmployeePlanOverride.employee_id == employee_id,
            EmployeePlanOverride.product_id == product_id,
        )
    ).scalar_one_or_none()
    before = override_snapshot(row)
    if row is None:
        row = EmployeePlanOverride(
            employee_id=employee_id,
            policy_year_id=policy_year_id,
            client_id=client_id,
            product_id=product_id,
            product_code=product_code,
        )
        db.add(row)
    row.plan_code = None if declined else plan_code
    row.declined = declined
    row.source = source
    row.source_ref = source_ref
    row.modified_by = modified_by
    if covered_dependant_ids is not _KEEP:
        row.covered_dependant_ids = covered_dependant_ids
    if effective_from is not _KEEP:
        row.effective_from = effective_from
    if tier_category_id is not _KEEP:
        row.tier_category_id = None if declined else tier_category_id
    if flex_price_tag is not _KEEP:
        row.flex_price_tag = None if declined else flex_price_tag
    if dependant_option_ids is not _KEEP:
        row.dependant_option_ids = None if declined else dependant_option_ids
    return row, before
