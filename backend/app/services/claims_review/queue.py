"""Atomic enqueue and cancellation for durable claim-review jobs."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ai_config import load_ai_config
from app.models import Claim, ClaimAIReview, ClaimReviewJob, ClientAIConfig, PlatformAISetting
from app.models.claim import (
    CASE_TYPE_CLAIM,
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_SUBMITTED,
)
from app.models.claim_ai_review import (
    REVIEW_STATUS_CANCELLED,
    REVIEW_STATUS_ERROR,
    REVIEW_STATUS_QUEUED,
    REVIEW_STATUS_RETRY_WAIT,
    REVIEW_STATUS_RUNNING,
)
from app.models.claim_review_job import (
    ACTIVE_JOB_STATES,
    JOB_STATE_CANCELLED,
    JOB_STATE_QUEUED,
)
from app.models.platform_ai_settings import SINGLETON_ID


@dataclass(frozen=True)
class EnqueueResult:
    review: ClaimAIReview
    job: ClaimReviewJob | None
    existing: bool = False


def _stored_config_is_validated(db: Session, client_id: str) -> bool:
    cfg = load_ai_config(db, client_id)
    if cfg is None:
        return False
    if cfg.source == "env":
        return os.environ.get("INSPRO_AI_CONFIG_VALIDATED", "").lower() == "true"
    if cfg.source == "byok":
        row = db.execute(
            select(ClientAIConfig).where(ClientAIConfig.client_id == client_id)
        ).scalar_one_or_none()
        fingerprint = row.key_fingerprint if row else None
    else:
        row = db.get(PlatformAISetting, SINGLETON_ID)
        fingerprint = row.key_fingerprint if row else None
    return bool(
        row
        and row.validation_status == "active"
        and row.validated_fingerprint == fingerprint
        and row.validated_model == cfg.model
        and row.validated_location == cfg.gcp_location
        and row.validated_capacity_mode == cfg.capacity_mode
    )


def configuration_ready(db: Session, client_id: str) -> bool:
    """Fail closed in production; local/test may exercise the queue without Vertex."""
    env = os.environ.get("INSPRO_ENV", "dev").strip().lower()
    return env not in {"prod", "production"} or _stored_config_is_validated(db, client_id)


def active_job(db: Session, claim_id: str) -> ClaimReviewJob | None:
    return db.execute(
        select(ClaimReviewJob)
        .where(ClaimReviewJob.claim_id == claim_id, ClaimReviewJob.state.in_(ACTIVE_JOB_STATES))
        .order_by(ClaimReviewJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def enqueue_claim_review(
    db: Session,
    claim: Claim,
    broker_firm_id: str,
    *,
    supersede: bool,
    available_at: datetime | None = None,
    mark_pending: bool = True,
) -> EnqueueResult:
    """Create the review and public job in the caller's single transaction."""
    locked = db.get(Claim, claim.id, with_for_update=True)
    if locked is None:
        raise RuntimeError(f"Claim {claim.id} disappeared while enqueueing review")

    current_job = active_job(db, claim.id)
    if current_job is not None:
        review = db.get(ClaimAIReview, current_job.review_id)
        if review is not None and not review.superseded:
            return EnqueueResult(review=review, job=current_job, existing=True)

    if supersede:
        for old in db.execute(
            select(ClaimAIReview).where(
                ClaimAIReview.claim_id == claim.id,
                ClaimAIReview.superseded.is_(False),
            )
        ).scalars():
            old.superseded = True

    review = ClaimAIReview(
        client_id=claim.client_id,
        claim_id=claim.id,
        status=REVIEW_STATUS_QUEUED,
        stage="queued",
    )
    db.add(review)
    db.flush()

    if not configuration_ready(db, claim.client_id):
        review.status = REVIEW_STATUS_ERROR
        review.stage = "persist"
        review.error_code = "ai_configuration_unvalidated"
        review.error_detail = (
            "AI configuration is not validated for this model and Singapore region; "
            "the claim was routed to manual review."
        )
        review.completed_at = datetime.now(UTC)
        claim.status = CLAIM_STATUS_SUBMITTED
        return EnqueueResult(review=review, job=None)

    if mark_pending:
        claim.status = CLAIM_STATUS_AI_REVIEW_PENDING
    job = ClaimReviewJob(
        broker_firm_id=broker_firm_id,
        client_id=claim.client_id,
        claim_id=claim.id,
        review_id=review.id,
        claim_revision=claim.revision,
        idempotency_key=f"claim-review:{claim.id}:{claim.revision}:{review.id}",
        state=JOB_STATE_QUEUED,
        stage="queued",
        available_at=available_at or datetime.now(UTC),
    )
    db.add(job)
    db.flush()
    return EnqueueResult(review=review, job=job)


def enqueue_amended_claim_review(
    db: Session,
    claim: Claim,
    broker_firm_id: str | None,
) -> EnqueueResult | None:
    """Queue one quiet-period review for the claim's latest revision.

    Each later amendment cancels the active delayed job before replacing it,
    so a receipt replacement (add new, then remove old) produces one provider
    call for the final document set rather than one call per click.
    """
    if (
        not broker_firm_id
        or claim.case_type != CASE_TYPE_CLAIM
        or claim.status != CLAIM_STATUS_SUBMITTED
    ):
        return None
    # Persist cancellation of the previous active row before inserting its
    # replacement. Both dialects enforce one active job per claim with a
    # partial unique index, so relying on unit-of-work ordering can make a valid
    # replacement race its own cancelled predecessor.
    db.flush()
    try:
        seconds = int(os.environ.get("INSPRO_REVIEW_AMENDMENT_DEBOUNCE_SECONDS", "30"))
    except ValueError:
        seconds = 30
    delay = timedelta(seconds=max(5, min(seconds, 300)))
    return enqueue_claim_review(
        db,
        claim,
        broker_firm_id,
        supersede=True,
        available_at=datetime.now(UTC) + delay,
        # During the quiet period the worker does not own the claim yet. It
        # transitions to pending when the delayed job is actually leased.
        mark_pending=False,
    )


def cancel_active_review_job(db: Session, claim_id: str, reason: str) -> int:
    """Cancel active queue ownership before a member/broker moves the claim."""
    now = datetime.now(UTC)
    jobs = db.execute(
        select(ClaimReviewJob).where(
            ClaimReviewJob.claim_id == claim_id,
            ClaimReviewJob.state.in_(ACTIVE_JOB_STATES),
        )
    ).scalars()
    count = 0
    for job in jobs:
        job.state = JOB_STATE_CANCELLED
        job.finished_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error_code = "claim_state_changed"
        job.last_error_detail = reason[:500]
        review = db.get(ClaimAIReview, job.review_id)
        if review is not None and review.status in {
            REVIEW_STATUS_QUEUED,
            REVIEW_STATUS_RUNNING,
            REVIEW_STATUS_RETRY_WAIT,
        }:
            review.status = REVIEW_STATUS_CANCELLED
            review.error_code = "claim_state_changed"
            review.error_detail = reason[:500]
            review.completed_at = now
        count += 1
    return count
