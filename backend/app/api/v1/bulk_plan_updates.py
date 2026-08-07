"""Bulk coverage changes — apply a set of coverage changes to a population.

Preview is a read-only dry-run; apply writes the sparse overrides, records the
batch, and audits it. Apply is rate-limited like other bulk operations (10/min).

- POST /policy-years/{id}/bulk-plan-updates/preview   — dry-run, no writes
- POST /policy-years/{id}/bulk-plan-updates/apply      — apply + record
- GET  /policy-years/{id}/bulk-plan-updates            — history
- GET  /bulk-plan-updates/{id}                         — one batch, in detail
- POST /bulk-plan-updates/{id}/undo                    — put it back

Preview and apply take the SAME body, and that body carries a ``MemberQuery`` — a
rule, not a list of people. Apply re-resolves the rule server-side under the
preview's ``selection_digest``, so what is applied is provably the population that
was previewed, without shipping thousands of ids back and forth.

The pre-change-set body (flat ``product_code`` + ``selector``) still works: it is
folded into a one-change set by ``BulkCoverageRequest`` itself, so there is one
evaluation path rather than two that can drift about money.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_bulk_plan_update, load_policy_year
from app.core.pagination import MAX_LIMIT
from app.core.rate_limit import limiter
from app.db.base import new_uuid
from app.db.session import get_db
from app.models import BulkPlanUpdate, PolicyYear
from app.models.bulk_plan_update import BulkUpdateStatus
from app.schemas.enrollment import (
    BulkApplyResult,
    BulkBatchDetailOut,
    BulkBatchSummaryOut,
    BulkChangeGroup,
    BulkCoverageRequest,
    BulkImpact,
    BulkPreviewResult,
    BulkRowOutcome,
    BulkUndoResult,
    BulkWarningBucket,
)
from app.services import bulk_plan_update as svc
from app.services.bulk_plan_update import (
    ResolvedChange,
    SelectionChanged,
    SelectionTooLarge,
    UnacknowledgedWarnings,
)
from app.services.enrollment_products import available_plan_codes, resolve_product_by_code
from app.services.enrollment_validation import assert_plan_available
from app.services.override_writer import is_revert_batch
from app.services.underwriting import refresh_underwriting_cases

router = APIRouter(tags=["bulk-plan-updates"])

# Rows kept inline on the stored batch record. Everything above it is summarised
# by counts + groups; a multi-megabyte JSON blob in a tenant table is not a
# record, it is a liability.
MAX_STORED_ROWS = 5000


def _resolve_changes(
    db: Session, py: PolicyYear, body: BulkCoverageRequest
) -> list[ResolvedChange]:
    out: list[ResolvedChange] = []
    for change in body.changes:
        product = resolve_product_by_code(db, py, change.product_code)
        if product is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Product '{change.product_code}' is not configured in this policy year.",
            )
        if change.action == "set_plan":
            assert_plan_available(
                change.target_plan_code,
                available_plan_codes(db, py.id, product.id),
                change.product_code,
            )
        out.append(ResolvedChange(change=change, product=product))
    return out


def _replayable(
    db: Session, py: PolicyYear, body: BulkCoverageRequest
) -> BulkPlanUpdate | None:
    """The batch this ``request_id`` already produced, if it is the same request.

    Scoped to the BENEFIT YEAR as well as the client, and the stored change set
    has to match: a replay answers "did that go through?" with ``applied: N``,
    so returning that for a body which was never run is worse than applying
    twice — it reports work that did not happen.
    """
    existing = db.execute(
        select(BulkPlanUpdate).where(
            BulkPlanUpdate.client_id == py.client_id,
            BulkPlanUpdate.request_id == body.request_id,
        )
    ).scalars().first()
    if existing is None:
        return None
    same = existing.policy_year_id == py.id and (existing.changes or []) == [
        c.model_dump() for c in body.changes
    ]
    if not same:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "request_id_reused",
                "message": (
                    "This attempt id was already used for a different coverage "
                    "change. Re-run the preview and apply again."
                ),
                "existing_id": existing.id,
            },
        )
    return existing


def _too_large(exc: SelectionTooLarge) -> HTTPException:
    """A runaway guard, not a workflow limit — the message states how far over
    the selection is so the broker knows what to narrow."""
    return HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        {
            "code": "selection_too_large",
            "message": (
                f"{exc.selected:,} members match this selection; the limit for one "
                f"run is {exc.limit:,}. Narrow the filters and run it in parts."
            ),
            "selected": exc.selected,
            "limit": exc.limit,
        },
    )


def _page(rows: list[Any], offset: int, limit: int) -> list[Any]:
    return rows[offset : offset + limit]


def _summary(record: BulkPlanUpdate, undone_by: str | None = None) -> BulkBatchSummaryOut:
    stored = record.result_summary or {}
    changes = record.changes or []
    codes = [str(c["product_code"]) for c in changes if c.get("product_code")]
    return BulkBatchSummaryOut(
        id=record.id,
        created_at=record.created_at,
        initiated_by=record.initiated_by,
        status=record.status,
        # A pre-change-set row has no `changes`; its single product code is the
        # flat column, which is still written for exactly this reason.
        product_codes=codes or ([record.product_code] if record.product_code else []),
        counts=stored.get("counts") or {},
        acknowledged=record.acknowledged or [],
        undo_of=record.undo_of,
        undone_by=undone_by,
        is_revert=is_revert_batch(record.product_code),
        restorable=len(stored.get("restore") or []),
        # Written pairs beyond the stored cap, which an undo canNOT put back.
        not_restorable=max(
            0,
            (stored.get("restore_total") or 0) - len(stored.get("restore") or []),
        ),
    )


@router.post(
    "/policy-years/{policy_year_id}/bulk-plan-updates/preview",
    response_model=BulkPreviewResult,
)
def preview_bulk_update(
    policy_year_id: str,
    body: BulkCoverageRequest,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> BulkPreviewResult:
    changes = _resolve_changes(db, py, body)
    try:
        result = svc.evaluate(db, py, changes, body.query, apply=False)
    except SelectionTooLarge as exc:
        raise _too_large(exc) from exc
    return BulkPreviewResult(
        rows=_page(result.rows, offset, limit),
        rows_total=len(result.rows),
        rows_offset=offset,
        counts=result.counts,
        groups=result.groups,
        warnings=result.warnings,
        impact=result.impact,
        selection_digest=result.digest,
    )


@router.post(
    "/policy-years/{policy_year_id}/bulk-plan-updates/apply",
    response_model=BulkApplyResult,
)
@limiter.limit("10/minute")
def apply_bulk_update(
    request: Request,
    policy_year_id: str,
    body: BulkCoverageRequest,
    limit: int = Query(100, ge=1, le=1000),
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkApplyResult:
    changes = _resolve_changes(db, py, body)

    if body.request_id:
        # Idempotency. A retry after a timeout on a batch that actually
        # committed, or a resubmitted form, must not apply twice — so a replayed
        # request_id returns the batch it already produced rather than a second
        # one. The unique index is what makes this safe under concurrency; this
        # lookup just avoids doing the work and hitting it (see the commit).
        existing = _replayable(db, py, body)
        if existing is not None:
            return _replay(existing, limit)

    record_id = new_uuid()

    # Every gate — size, staleness, acknowledgement — is checked inside
    # `evaluate` BEFORE the first write. The population (or the coverage of
    # someone in it) may have moved since the broker approved the preview, and an
    # apply that trips a gate must leave nothing behind.
    try:
        result = svc.evaluate(
            db, py, changes, body.query, apply=True, record_id=record_id, user=user,
            expected_digest=body.selection_digest,
            acknowledged=set(body.acknowledge),
        )
    except SelectionTooLarge as exc:
        raise _too_large(exc) from exc
    except SelectionChanged as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "selection_changed",
                "message": (
                    "The roster changed since this preview — re-run the preview "
                    "and check the numbers before applying."
                ),
                "selection_digest": exc.digest,
            },
        ) from exc
    except UnacknowledgedWarnings as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "unacknowledged_warnings",
                "message": (
                    "Some members need a second look before this runs. Confirm "
                    "each warning to continue."
                ),
                "warnings": [b.model_dump() for b in exc.buckets],
            },
        ) from exc

    counts = result.counts
    applied_ids = {
        r.employee_id for r in result.rows if r.outcome == "applied" and r.employee_id
    }
    if applied_ids:
        # A plan change moves eligible sum insured, which is exactly what the
        # NEL gates key on. Scoped to the batch's members per the underwriting
        # invariant (an unscoped run hydrates the whole roster twice AND would
        # retire cases for households it never recomputed). Flush-only — the
        # commit below owns it, so a fault rolls the whole batch back.
        refresh_underwriting_cases(db, py, applied_ids)

    primary = body.primary
    record = BulkPlanUpdate(
        id=record_id,
        policy_year_id=py.id,
        client_id=py.client_id,
        initiated_by=user.user_id,
        # The flat columns carry the FIRST change so a reader that only knows the
        # pre-change-set shape still sees a coherent row.
        product_code=primary.product_code,
        target_plan_code=primary.target_plan_code,
        action=primary.action,
        selector=body.query.model_dump(),
        dependant_action=(
            primary.dependant_action.model_dump() if primary.dependant_action else None
        ),
        query=body.query.model_dump(),
        changes=[c.model_dump() for c in body.changes],
        acknowledged=list(body.acknowledge),
        request_id=body.request_id,
        status=_status_for(counts),
        result_summary=_result_summary(result),
    )
    db.add(record)
    db.flush()
    write_audit(
        db, user, action="bulk_plan_update", entity_type="bulk_plan_update",
        entity_id=record.id,
        after={
            "products": [c.product_code for c in body.changes],
            "counts": counts,
            "acknowledged": list(body.acknowledge),
        },
    )
    try:
        db.commit()
    except IntegrityError:
        # Another request with the same ``request_id`` committed while this one
        # was evaluating — the lookup above ran before it existed. The unique
        # index is what actually enforces idempotency; rolling back discards
        # THIS run's overrides (the winner already wrote them) and the replay
        # returns the batch that did land.
        db.rollback()
        winner = db.execute(
            select(BulkPlanUpdate).where(
                BulkPlanUpdate.client_id == py.client_id,
                BulkPlanUpdate.request_id == body.request_id,
            )
        ).scalars().first()
        if winner is None:
            raise
        return _replay(winner, limit)
    return BulkApplyResult(
        id=record_id,
        status=record.status,
        counts=counts,
        rows=_page(result.rows, 0, limit),
        rows_total=len(result.rows),
        groups=result.groups,
        warnings=result.warnings,
        impact=result.impact,
    )


def _status_for(counts: dict[str, int]) -> str:
    """Only an ERROR is a failure. ``skipped`` means "this member isn't enrolled
    in the product", which is the NORMAL result of a roster-wide rule — "move all
    of Sales to Plan 2" necessarily sweeps in people the product doesn't cover.
    Counting it as a partial failure (correct when the only selectors were
    explicit ids) would file every filter-driven run as partially_failed and make
    the status worthless."""
    return (
        BulkUpdateStatus.applied
        if counts.get("error", 0) == 0
        else BulkUpdateStatus.partially_failed
    )


def _result_summary(result: svc.BulkEvaluation) -> dict[str, Any]:
    stored = result.rows[:MAX_STORED_ROWS]
    return {
        "counts": result.counts,
        "groups": [g.model_dump() for g in result.groups],
        "warnings": [w.model_dump() for w in result.warnings],
        "impact": result.impact.model_dump(),
        "rows": [r.model_dump() for r in stored],
        "rows_total": len(result.rows),
        # A cap that isn't reported reads as "that's all of them".
        "rows_truncated": len(result.rows) > len(stored),
        # The undo source: what each written pair looked like before, and what
        # this batch left it as. One entry per written (member, PRODUCT) pair,
        # so a 10-product change set over 5,000 members is 50,000 of them —
        # hence the same cap as the rows, and hence the flag: undo can only put
        # back what was recorded, and an unreported cap would have the confirm
        # dialog promise the whole batch while silently leaving the tail on its
        # new coverage.
        "restore": result.restore[:MAX_STORED_ROWS],
        "restore_truncated": len(result.restore) > MAX_STORED_ROWS,
        "restore_total": len(result.restore),
    }


def _replay(record: BulkPlanUpdate, limit: int) -> BulkApplyResult:
    stored = record.result_summary or {}
    return BulkApplyResult(
        id=record.id,
        status=record.status,
        counts=stored.get("counts") or {},
        rows=[BulkRowOutcome(**r) for r in (stored.get("rows") or [])[:limit]],
        rows_total=stored.get("rows_total") or 0,
        groups=[BulkChangeGroup(**g) for g in stored.get("groups") or []],
        # The warnings come back too: a replay is the answer to "did that
        # apply?", and dropping them would make the second answer read as a
        # cleaner run than the first.
        warnings=[BulkWarningBucket(**w) for w in stored.get("warnings") or []],
        impact=BulkImpact(**(stored.get("impact") or {})),
        replayed=True,
    )


# ── History ─────────────────────────────────────────────────────────────────


@router.get(
    "/policy-years/{policy_year_id}/bulk-plan-updates",
    response_model=list[BulkBatchSummaryOut],
)
def list_bulk_updates(
    policy_year_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> list[BulkBatchSummaryOut]:
    rows = list(
        db.execute(
            select(BulkPlanUpdate)
            .where(BulkPlanUpdate.policy_year_id == py.id)
            # ``id`` breaks the tie: two batches applied inside the same second
            # (SQLite's timestamp resolution) would otherwise order arbitrarily,
            # and an unstable sort makes paging repeat or skip rows.
            .order_by(BulkPlanUpdate.created_at.desc(), BulkPlanUpdate.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    # Which batches have already been undone, resolved from the undo batches in
    # the same page-plus-year rather than a per-row query. A batch that has been
    # undone must not still offer an Undo button.
    undone = {
        row.undo_of: row.id
        for row in db.execute(
            select(BulkPlanUpdate).where(
                BulkPlanUpdate.policy_year_id == py.id,
                BulkPlanUpdate.undo_of.is_not(None),
            )
        ).scalars()
    }
    return [_summary(r, undone.get(r.id)) for r in rows]


@router.get("/bulk-plan-updates/{batch_id}", response_model=BulkBatchDetailOut)
def get_bulk_update(
    batch_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    record: BulkPlanUpdate = Depends(load_bulk_plan_update),
    db: Session = Depends(get_db),
) -> BulkBatchDetailOut:
    stored = record.result_summary or {}
    rows = stored.get("rows") or []
    undone_by = db.execute(
        select(BulkPlanUpdate.id).where(BulkPlanUpdate.undo_of == record.id)
    ).scalars().first()
    base = _summary(record, undone_by)
    return BulkBatchDetailOut(
        **base.model_dump(),
        # `query` falls back to the legacy `selector` column so a pre-change-set
        # batch is still re-runnable in the builder.
        query=record.query or record.selector,
        changes=record.changes or _legacy_change(record),
        groups=[BulkChangeGroup(**g) for g in stored.get("groups") or []],
        impact=BulkImpact(**(stored.get("impact") or {})),
        rows=[BulkRowOutcome(**r) for r in _page(rows, offset, limit)],
        rows_total=stored.get("rows_total") or len(rows),
        rows_offset=offset,
        rows_truncated=bool(stored.get("rows_truncated")),
    )


def _legacy_change(record: BulkPlanUpdate) -> list[dict[str, Any]]:
    return [
        {
            "product_code": record.product_code,
            "action": record.action,
            "target_plan_code": record.target_plan_code,
            "dependant_action": record.dependant_action,
        }
    ]


@router.post("/bulk-plan-updates/{batch_id}/undo", response_model=BulkUndoResult)
@limiter.limit("10/minute")
def undo_bulk_update(
    request: Request,
    batch_id: str,
    record: BulkPlanUpdate = Depends(load_bulk_plan_update),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkUndoResult:
    """Restore what a batch replaced, as a NEW batch.

    Undo never deletes history: it writes its own record pointing at the source,
    so the timeline reads "this was applied, then this was put back" rather than
    losing the fact that it happened.
    """
    already = db.execute(
        select(BulkPlanUpdate.id).where(BulkPlanUpdate.undo_of == record.id)
    ).scalars().first()
    if already:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "already_undone",
                "message": "This coverage change has already been undone.",
                "undo_id": already,
            },
        )
    if record.undo_of:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "cannot_undo_an_undo",
                "message": (
                    "This batch is itself an undo. Re-run the original selection "
                    "instead of undoing the undo."
                ),
            },
        )
    py = db.get(PolicyYear, record.policy_year_id)
    if py is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benefit year not found")

    stored = record.result_summary or {}
    undo_id = new_uuid()
    result = svc.undo_batch(
        db, py, stored.get("restore") or [], record_id=undo_id, user=user
    )
    if result.employee_ids:
        refresh_underwriting_cases(db, py, result.employee_ids)

    rows = result.rows + result.superseded
    stored_rows = rows[:MAX_STORED_ROWS]
    undo_record = BulkPlanUpdate(
        id=undo_id,
        policy_year_id=py.id,
        client_id=py.client_id,
        initiated_by=user.user_id,
        product_code=record.product_code,
        target_plan_code=None,
        action="revert_to_default",
        selector={},
        query=record.query or record.selector,
        changes=record.changes or _legacy_change(record),
        undo_of=record.id,
        status=_status_for(result.counts),
        result_summary={
            "counts": result.counts,
            "rows": [r.model_dump() for r in stored_rows],
            "rows_total": len(rows),
            "rows_truncated": len(rows) > len(stored_rows),
            # An undo is not itself undoable (see the guard above), so it stores
            # no restore list — an empty one here is deliberate, not a gap.
            "restore": [],
        },
    )
    db.add(undo_record)
    db.flush()
    write_audit(
        db, user, action="bulk_plan_update_undo", entity_type="bulk_plan_update",
        entity_id=undo_id, after={"undo_of": record.id, "counts": result.counts},
    )
    db.commit()
    return BulkUndoResult(
        id=undo_id, counts=result.counts, superseded=result.superseded
    )
