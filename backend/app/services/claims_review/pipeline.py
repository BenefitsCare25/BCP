"""Checkpointed claim-review pipeline used by the durable worker.

Opens its OWN session (the request session is gone by the time this runs) and
re-establishes the firm ``search_path`` from the claim's client, so on
Postgres the tenant tables resolve to the right schema.

Stages: deterministic pre-checks (a fail short-circuits with ZERO AI calls) →
per-document extraction → comparison + AI rules → selective vision verify →
verdict. Any exception degrades to manual review: the review row records the
failure and the claim returns to plain ``submitted`` — the member is never
blocked by a pipeline fault.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.tenancy import set_search_path
from app.models import Claim, ClaimAIReview, ClaimReviewJob, Employee, StoredDocument
from app.models.claim import (
    CLAIM_STATUS_AI_FLAGGED,
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_SUBMITTED,
)
from app.models.claim_ai_review import (
    REVIEW_STATUS_COMPLETE,
    REVIEW_STATUS_ERROR,
    REVIEW_STATUS_RUNNING,
    REVIEW_VERDICT_CLEAN,
    REVIEW_VERDICT_FLAGGED,
)
from app.models.stored_document import STORAGE_AVAILABLE
from app.services import ai_gateway
from app.services.ai_extractor import AINotConfiguredError
from app.services.claim_doc_types import resolve_doc_types
from app.services.claim_review_configs import claim_key_for, resolve_review_config
from app.services.claims import claim_documents
from app.services.claims_review import (
    comparison,
    doc_completeness,
    extraction,
    rules,
    vision_verify,
)
from app.services.claims_review import (
    metrics as review_metrics,
)
from app.services.claims_review.verdict import compute_verdict
from app.services.member_statement import build_member_statement

logger = logging.getLogger(__name__)


class ReviewOwnershipLost(RuntimeError):
    """The job was cancelled, superseded, revised, or leased by another worker."""


class ReviewDeadlineExceeded(RuntimeError):
    """The durable review exceeded its configured end-to-end deadline."""


def _apply_call_metadata(review: ClaimAIReview, calls: list[dict[str, Any]]) -> None:
    """Sum live (non-cache-hit) token usage + cost onto the review row."""
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    model = None
    for meta in calls:
        model = meta.get("model") or model
        if meta.get("cache_hit"):
            continue
        in_t = int(meta.get("input_tokens") or 0)
        out_t = int(meta.get("output_tokens") or 0)
        input_tokens += in_t
        output_tokens += out_t
        cost += ai_gateway._estimate_cost_usd(str(meta.get("model") or ""), in_t, out_t)
    review.model = model
    review.input_tokens = input_tokens
    review.output_tokens = output_tokens
    review.cost_estimate_usd = round(cost, 6)


def _still_ours(db: Session, claim: Claim) -> bool:
    """Is the claim STILL waiting on this run — read from the DATABASE, not
    from the copy this session loaded before the AI calls?

    **The refresh is the whole point.** This session loads the claim once, then
    spends tens of seconds in the provider, and commits only at the very end
    (`ai_gateway` commits its spend counter on a SEPARATE session, so nothing
    expires ours in between). So `claim.status` in memory is the status as it
    was when the run STARTED, and guarding on it is not a guard at all.

    Two things move the claim inside that window, and both are ordinary:
    - a MEMBER AMENDMENT, which sets the status to `submitted` and supersedes
      this review;
    - a BROKER DECISION.

    Without the refresh the verdict is written over either of them. The
    amendment case is the worse one: the claim ends at `ai_verified` with its
    only review superseded, so `_latest_review` returns None and the broker is
    shown a verified verdict with no review behind it — computed against values
    the claim no longer holds. A plausible answer that is silently wrong, which
    is exactly the class this codebase spends its comments on.

    Only the CLAIM is refreshed. `review` carries this run's unflushed results
    and refreshing it would discard them; a superseded review being filled in
    is harmless, since `_latest_review` already filters it out.

    **The row LOCK is what makes the answer survive being acted on.** A bare
    refresh only narrows the window: the read and the write below it are two
    statements, so an amendment committing between them is still overwritten —
    and because the flush carries only the dirty `status` column, the result is
    exactly the state this guard exists to prevent (`ai_verified` with its sole
    review superseded, so the broker reads a verified verdict with no review
    behind it). `SELECT … FOR UPDATE` holds the row from here to `db.commit()`,
    a few statements away, so a concurrent amendment either lands before the
    read — and is seen — or waits and applies on top. SQLite's dialect renders
    no `FOR UPDATE` clause at all, so the test suite is unaffected.
    """
    db.refresh(claim, with_for_update=True)
    return claim.status == CLAIM_STATUS_AI_REVIEW_PENDING


def _finalize_claim_status(db: Session, claim: Claim, verdict: str) -> None:
    """Move the claim per the verdict — but only if it's still waiting on us."""
    if not _still_ours(db, claim):
        return
    claim.status = (
        CLAIM_STATUS_AI_VERIFIED
        if verdict == REVIEW_VERDICT_CLEAN
        else CLAIM_STATUS_AI_FLAGGED
    )


def _fall_back_to_manual(db: Session, claim: Claim) -> None:
    """Same staleness applies here: a member who amended a `needs_info` claim
    mid-run leaves it at `needs_info`, and writing `submitted` over that would
    move a claim nobody is waiting on back into the queue."""
    if _still_ours(db, claim):
        claim.status = CLAIM_STATUS_SUBMITTED


def run_review(claim_id: str, review_id: str, broker_firm_id: str | None) -> None:
    """Compatibility entry point for local callers; production uses the worker."""
    db = SessionLocal()
    try:
        set_search_path(db, broker_firm_id)
        review = db.get(ClaimAIReview, review_id)
        claim = db.get(Claim, claim_id)
        if review is None or claim is None:
            logger.warning(
                "run_review: claim %s / review %s not found — skipping", claim_id, review_id
            )
            return
        if review.superseded:
            return

        try:
            _run_stages(db, claim, review, broker_firm_id)
        except AINotConfiguredError:
            review.status = REVIEW_STATUS_ERROR
            review.error_detail = "AI review is not configured."
            _fall_back_to_manual(db, claim)
            logger.warning(
                "Claim AI review skipped",
                extra={"claim_id": claim.id, "error_code": "AINotConfiguredError"},
            )
        except Exception as exc:
            review.status = REVIEW_STATUS_ERROR
            review.error_detail = "Claim review failed. Route the claim to manual review."
            _fall_back_to_manual(db, claim)
            logger.error(
                "Claim AI review failed",
                extra={"claim_id": claim.id, "error_code": type(exc).__name__},
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "Claim AI review could not be persisted",
            extra={"claim_id": claim_id, "error_code": type(exc).__name__},
        )
        # The rollback above just discarded _fall_back_to_manual's status
        # write too — without this, the claim is stranded at
        # `ai_review_pending` forever. Re-apply the safety net in its own
        # minimal transaction so the "never block the member" guarantee
        # survives a failed review persist.
        try:
            set_search_path(db, broker_firm_id)
            # Locked for the same reason as `_still_ours`: this re-reads a
            # status another transaction may be moving, and then writes over it.
            claim = db.get(Claim, claim_id, with_for_update=True)
            if claim is not None and claim.status == CLAIM_STATUS_AI_REVIEW_PENDING:
                claim.status = CLAIM_STATUS_SUBMITTED
                review = db.get(ClaimAIReview, review_id)
                if review is not None and review.status not in (
                    REVIEW_STATUS_COMPLETE, REVIEW_STATUS_ERROR
                ):
                    review.status = REVIEW_STATUS_ERROR
                    review.error_detail = "Review results could not be persisted."
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.critical(
                "Claim %s is stranded in ai_review_pending — the manual-review "
                "fallback could not be persisted either. Broker must re-run "
                "the review.",
                claim_id,
                extra={"error_code": type(exc).__name__},
            )
    finally:
        db.close()


def execute_leased_review(job_id: str, lease_owner: str) -> None:
    """Execute one job and raise failures to the worker's retry classifier."""
    with SessionLocal() as db:
        job = db.get(ClaimReviewJob, job_id)
        if job is None:
            raise ReviewOwnershipLost(f"Job {job_id} no longer exists")
        set_search_path(db, job.broker_firm_id)
        review = db.get(ClaimAIReview, job.review_id)
        claim = db.get(Claim, job.claim_id)
        if review is None or claim is None:
            raise ReviewOwnershipLost("Claim or review no longer exists")
        if review.status == REVIEW_STATUS_COMPLETE:
            if review.superseded or claim.revision != job.claim_revision:
                raise ReviewOwnershipLost("Completed review no longer owns the claim revision")
            return
        _run_stages(
            db,
            claim,
            review,
            job.broker_firm_id,
            job_id=job.id,
            lease_owner=lease_owner,
        )


def _checkpoint(
    db: Session,
    claim: Claim,
    review: ClaimAIReview,
    *,
    stage: str,
    job_id: str | None,
    lease_owner: str | None,
) -> None:
    """Persist progress only while this exact lease and claim revision are current."""
    now = datetime.now(UTC)
    if job_id is not None:
        job = db.get(ClaimReviewJob, job_id)
        if job is not None:
            db.refresh(job, with_for_update=True)
        if (
            job is None
            or job.state != "running"
            or job.lease_owner != lease_owner
            or job.lease_expires_at is None
            or (
                job.lease_expires_at.replace(tzinfo=UTC)
                if job.lease_expires_at.tzinfo is None
                else job.lease_expires_at
            )
            <= now
            or job.claim_revision != claim.revision
        ):
            db.rollback()
            raise ReviewOwnershipLost("Review lease or claim revision is no longer current")
        deadline = int(os.environ.get("INSPRO_REVIEW_DEADLINE_SECONDS", "1200"))
        created_at = (
            job.created_at.replace(tzinfo=UTC)
            if job.created_at.tzinfo is None
            else job.created_at
        )
        if (now - created_at).total_seconds() >= deadline:
            db.rollback()
            raise ReviewDeadlineExceeded("Claim review exceeded its overall deadline")
        job.stage = stage
        job.heartbeat_at = now
    db.refresh(claim, with_for_update=True)
    superseded = db.scalar(
        select(ClaimAIReview.superseded).where(ClaimAIReview.id == review.id)
    )
    if (
        superseded is not False
        or (job_id is not None and claim.status != CLAIM_STATUS_AI_REVIEW_PENDING)
        or (job_id is not None and job.claim_revision != claim.revision)
    ):
        db.rollback()
        raise ReviewOwnershipLost("Claim or review ownership changed")
    review.stage = stage
    review.heartbeat_at = now
    db.commit()


def _compare_verify_and_finalize(
    db: Session,
    claim: Claim,
    review: ClaimAIReview,
    docs: list[StoredDocument],
    cfg: Any,
    det_results: list[dict[str, Any]],
    doc_warnings: list[dict[str, Any]],
    extractions: list[dict[str, Any]],
    all_calls: list[dict[str, Any]],
    job_id: str | None,
    lease_owner: str | None,
) -> None:
    review.rule_results = det_results + doc_warnings
    _checkpoint(
        db, claim, review, stage="comparison", job_id=job_id, lease_owner=lease_owner
    )
    stage_started = time.monotonic()
    ai_review, call_meta = comparison.compare_claim(db, claim, extractions, cfg)
    review_metrics.stage_duration("comparison", time.monotonic() - stage_started)
    all_calls.append(call_meta)
    review.field_comparisons = ai_review["field_comparisons"]
    review.rule_results = det_results + doc_warnings + ai_review["rule_results"]
    _apply_call_metadata(review, all_calls)
    _checkpoint(
        db, claim, review, stage="comparison", job_id=job_id, lease_owner=lease_owner
    )

    _checkpoint(db, claim, review, stage="vision", job_id=job_id, lease_owner=lease_owner)
    stage_started = time.monotonic()

    def vision_checkpoint(current, checks, calls) -> None:
        review.field_comparisons = list(current)
        review.vision_checks = list(checks)
        _apply_call_metadata(review, all_calls + list(calls))
        _checkpoint(
            db, claim, review, stage="vision", job_id=job_id, lease_owner=lease_owner
        )

    comparisons, vision_checks, calls = vision_verify.run_vision_checks(
        db,
        claim,
        docs,
        ai_review["field_comparisons"],
        vision_fields=cfg.vision_fields,
        checkpoint=vision_checkpoint,
    )
    review_metrics.stage_duration("vision", time.monotonic() - stage_started)
    all_calls.extend(calls)

    _checkpoint(db, claim, review, stage="verdict", job_id=job_id, lease_owner=lease_owner)
    stage_started = time.monotonic()
    rule_results = det_results + doc_warnings + ai_review["rule_results"]
    verdict, reasons = compute_verdict(
        rule_results,
        comparisons,
        vision_checks,
        ai_review["confidence"],
        evidence_fields=cfg.evidence_fields,
        model=review.model,
        claim_type=claim_key_for(claim)[1],
    )
    review.status = REVIEW_STATUS_COMPLETE
    review.verdict = verdict
    review.confidence = ai_review["confidence"]
    review.extractions = extractions
    review.field_comparisons = comparisons
    review.rule_results = rule_results
    review.vision_checks = vision_checks
    review.summary = (
        ai_review["summary"]
        if not reasons
        else (ai_review["summary"] + "\n\nFlagged: " + "; ".join(reasons)).strip()
    )
    review_metrics.stage_duration("verdict", time.monotonic() - stage_started)
    _apply_call_metadata(review, all_calls)
    _finalize_claim_status(db, claim, verdict)
    review.stage = "persist"
    review.completed_at = datetime.now(UTC)
    review.progress_current = review.progress_total
    db.commit()


def _extract_stage(
    db: Session,
    claim: Claim,
    review: ClaimAIReview,
    broker_firm_id: str | None,
    det_results: list[dict[str, Any]],
    job_id: str | None,
    lease_owner: str | None,
) -> tuple[
    list[StoredDocument],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    docs = claim_documents(db, claim)
    if claim.referral_document_id:
        referral = db.get(StoredDocument, claim.referral_document_id)
        if referral is not None and referral.storage_state == STORAGE_AVAILABLE:
            docs.append(referral)
    review.progress_total = len(docs)
    review.progress_current = len(review.extractions or [])
    _checkpoint(
        db, claim, review, stage="extraction", job_id=job_id, lease_owner=lease_owner
    )
    stage_started = time.monotonic()

    def extraction_checkpoint(current, warnings, calls) -> None:
        review.extractions = list(current)
        review.rule_results = det_results + list(warnings)
        review.progress_current = len(current) + len(warnings)
        _apply_call_metadata(review, list(calls))
        _checkpoint(
            db,
            claim,
            review,
            stage="extraction",
            job_id=job_id,
            lease_owner=lease_owner,
        )

    extractions, doc_warnings, calls = extraction.extract_documents(
        db, claim, docs, broker_firm_id, checkpoint=extraction_checkpoint
    )
    review_metrics.stage_duration("extraction", time.monotonic() - stage_started)
    doc_warnings += doc_completeness.doc_completeness_results(
        claim, extractions, resolve_doc_types(db, claim.client_id)
    )
    return docs, extractions, doc_warnings, list(calls)


def _run_stages(
    db,
    claim: Claim,
    review: ClaimAIReview,
    broker_firm_id: str | None = None,
    *,
    job_id: str | None = None,
    lease_owner: str | None = None,
) -> None:
    now = datetime.now(UTC)
    review.status = REVIEW_STATUS_RUNNING
    review.error_code = None
    review.error_detail = None
    review.started_at = review.started_at or now
    review.attempt += 1
    _checkpoint(
        db, claim, review, stage="deterministic", job_id=job_id, lease_owner=lease_owner
    )
    stage_started = time.monotonic()
    employee = db.get(Employee, claim.employee_id)
    if employee is None:
        raise RuntimeError(f"Employee {claim.employee_id} not found for claim {claim.id}")
    statement = build_member_statement(db, employee)

    # The claim type's review configuration (per-company; in-code defaults when
    # the type isn't customized) — resolved once, drives comparison, vision
    # gating and the verdict. Stamped BEFORE stage 1 so a deterministically
    # flagged claim still records which setup was in force (a NULL there means
    # "ran on the defaults", which would be a lie for a customized claim type).
    cfg = resolve_review_config(db, claim)
    review.review_config_id = cfg.config_id
    review.review_config_label = cfg.config_label

    # Stage 1 — deterministic pre-checks. A hard fail flags the claim with
    # zero AI spend.
    det_results = rules.deterministic_rule_results(db, claim, statement)
    review_metrics.stage_duration("deterministic", time.monotonic() - stage_started)
    review.rule_results = det_results
    if rules.has_failures(det_results):
        failed = [r["rule"] for r in det_results if r["status"] == "fail"]
        review.status = REVIEW_STATUS_COMPLETE
        review.verdict = REVIEW_VERDICT_FLAGGED
        review.confidence = 1.0
        review.field_comparisons = []
        review.vision_checks = []
        review.extractions = []
        review.summary = "Flagged by deterministic checks: " + "; ".join(failed)
        review.input_tokens = 0
        review.output_tokens = 0
        review.cost_estimate_usd = 0.0
        review.deterministic_short_circuit = True
        review.completed_at = datetime.now(UTC)
        _finalize_claim_status(db, claim, REVIEW_VERDICT_FLAGGED)
        review.stage = "persist"
        db.commit()
        return

    docs, extractions, doc_warnings, all_calls = _extract_stage(
        db, claim, review, broker_firm_id, det_results, job_id, lease_owner
    )
    _compare_verify_and_finalize(
        db,
        claim,
        review,
        docs,
        cfg,
        det_results,
        doc_warnings,
        extractions,
        all_calls,
        job_id,
        lease_owner,
    )
