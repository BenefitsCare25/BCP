"""Safe startup recovery and legacy reconciliation for claim reviews."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, set_search_path
from app.models import BrokerFirm, Claim, ClaimAIReview, ClaimReviewJob
from app.models.claim import (
    CASE_TYPE_CLAIM,
    CLAIM_STATUS_AI_FLAGGED,
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_SUBMITTED,
)
from app.models.claim_ai_review import (
    REVIEW_STATUS_COMPLETE,
    REVIEW_STATUS_ERROR,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_RETRY_WAIT,
)
from app.models.claim_review_job import JOB_STATE_FAILED, JOB_STATE_RETRY_WAIT
from app.services.claims_review.queue import (
    active_job,
    configuration_ready,
    enqueue_amended_claim_review,
)

logger = logging.getLogger(__name__)


def retry_failed_parse_reviews() -> int:
    """Requeue current parse failures after a worker deployment.

    The retry is bounded by the durable job's existing attempt counter and is
    refused if a member amended the claim or a broker moved it onward. This is
    what lets a parser/model-contract fix repair affected live claims without
    replaying historical or already-decided work.
    """
    try:
        return _retry_failed_parse_reviews()
    except Exception:
        # Recovery is opportunistic. A transient DB failure here must not stop
        # the worker from booting and serving its health endpoint.
        logger.exception("Failed to recover AI parse reviews")
        return 0


def _retry_failed_parse_reviews() -> int:
    now = datetime.now(UTC)
    with SessionLocal() as control_db:
        candidates = control_db.execute(
            select(ClaimReviewJob.id, ClaimReviewJob.broker_firm_id).where(
                ClaimReviewJob.state == JOB_STATE_FAILED,
                ClaimReviewJob.last_error_code == "AIParseError",
                ClaimReviewJob.attempt < ClaimReviewJob.max_attempts,
            )
        ).all()

    recovered = 0
    for job_id, broker_firm_id in candidates:
        with SessionLocal() as db:
            set_search_path(db, broker_firm_id)
            job = db.get(ClaimReviewJob, job_id, with_for_update=True)
            if job is None or job.state != JOB_STATE_FAILED:
                continue
            claim = db.get(Claim, job.claim_id, with_for_update=True)
            review = db.get(ClaimAIReview, job.review_id)
            if (
                claim is None
                or review is None
                or review.superseded
                or claim.status != CLAIM_STATUS_SUBMITTED
                or claim.revision != job.claim_revision
                or not configuration_ready(db, claim.client_id)
            ):
                continue
            claim.status = CLAIM_STATUS_AI_REVIEW_PENDING
            review.status = REVIEW_STATUS_RETRY_WAIT
            review.stage = job.stage
            review.error_code = None
            review.error_detail = None
            review.completed_at = None
            job.state = JOB_STATE_RETRY_WAIT
            job.available_at = now
            job.finished_at = None
            db.commit()
            recovered += 1
    if recovered:
        logger.warning("Requeued %s current AI parse failure(s)", recovered)
    return recovered


def recover_unreviewed_amendments() -> int:
    """Backfill submitted amendments created before automatic requeue existed."""
    try:
        with SessionLocal() as control_db:
            firm_ids = control_db.execute(select(BrokerFirm.id)).scalars().all()
        targets = firm_ids if is_postgres(engine) else [None]
        recovered = 0
        for firm_id in targets:
            with SessionLocal() as db:
                set_search_path(db, firm_id)
                claims = db.execute(
                    select(Claim).where(
                        Claim.status == CLAIM_STATUS_SUBMITTED,
                        Claim.case_type == CASE_TYPE_CLAIM,
                        Claim.amended_at.is_not(None),
                    )
                ).scalars().all()
                for claim in claims:
                    current_review = db.execute(
                        select(ClaimAIReview.id)
                        .where(
                            ClaimAIReview.claim_id == claim.id,
                            ClaimAIReview.superseded.is_(False),
                        )
                        .limit(1)
                    ).scalar_one_or_none()
                    if (
                        current_review is not None
                        or active_job(db, claim.id) is not None
                        or not configuration_ready(db, claim.client_id)
                    ):
                        continue
                    result = enqueue_amended_claim_review(db, claim, firm_id)
                    recovered += int(result is not None and result.job is not None)
                    db.commit()
                db.commit()
        if recovered:
            logger.warning("Queued %s previously unreviewed amendment(s)", recovered)
        return recovered
    except Exception:
        logger.exception("Failed to recover unreviewed claim amendments")
        return 0


def reconcile_legacy_reviews(broker_firm_id: str) -> int:
    """Convert pre-queue pending rows after tenant schema synchronization."""
    reconciled = 0
    with SessionLocal() as db:
        set_search_path(db, broker_firm_id)
        claims = db.execute(
            select(Claim).where(Claim.status == CLAIM_STATUS_AI_REVIEW_PENDING)
        ).scalars().all()
        for claim in claims:
            if active_job(db, claim.id) is not None:
                continue
            review = db.execute(
                select(ClaimAIReview)
                .where(
                    ClaimAIReview.claim_id == claim.id,
                    ClaimAIReview.superseded.is_(False),
                )
                .order_by(ClaimAIReview.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if review is None:
                review = ClaimAIReview(client_id=claim.client_id, claim_id=claim.id)
                db.add(review)
                db.flush()
            if review.status == REVIEW_STATUS_COMPLETE:
                claim.status = (
                    CLAIM_STATUS_AI_VERIFIED
                    if review.verdict == "clean"
                    else CLAIM_STATUS_AI_FLAGGED
                )
            elif review.status == REVIEW_STATUS_ERROR or not configuration_ready(
                db, claim.client_id
            ):
                review.status = REVIEW_STATUS_ERROR
                review.error_code = review.error_code or "legacy_review_unrecoverable"
                review.error_detail = review.error_detail or (
                    "Legacy review could not be resumed with a validated AI configuration."
                )
                review.completed_at = datetime.now(UTC)
                claim.status = CLAIM_STATUS_SUBMITTED
            else:
                review.status = REVIEW_STATUS_PENDING
                review.stage = "queued"
                db.add(
                    ClaimReviewJob(
                        broker_firm_id=broker_firm_id,
                        client_id=claim.client_id,
                        claim_id=claim.id,
                        review_id=review.id,
                        claim_revision=claim.revision,
                        idempotency_key=f"claim-review:{claim.id}:{claim.revision}:{review.id}",
                        available_at=datetime.now(UTC),
                    )
                )
            reconciled += 1
        db.commit()
    return reconciled


def _recover_current_schema(db: Session) -> int:
    """Revert stranded ``ai_review_pending`` claims in the current search_path.

    Only touches claims whose latest non-superseded review is still ``pending``
    (a genuinely interrupted run). A claim in ``ai_review_pending`` whose latest
    review already ``complete``/``error`` is a different inconsistency this sweep
    deliberately leaves alone. Does not commit — the caller owns the transaction.
    """
    claims = (
        db.execute(
            select(Claim).where(Claim.status == CLAIM_STATUS_AI_REVIEW_PENDING)
        )
        .scalars()
        .all()
    )
    recovered = 0
    for claim in claims:
        review = (
            db.execute(
                select(ClaimAIReview)
                .where(
                    ClaimAIReview.claim_id == claim.id,
                    ClaimAIReview.superseded.is_(False),
                )
                .order_by(ClaimAIReview.created_at.desc())
            )
            .scalars()
            .first()
        )
        if review is not None and review.status != REVIEW_STATUS_PENDING:
            continue
        claim.status = CLAIM_STATUS_SUBMITTED
        if review is not None:
            review.status = REVIEW_STATUS_ERROR
            review.error_detail = (
                "Review interrupted by a restart; reverted to manual review."
            )
        recovered += 1
    return recovered


def recover_stranded_reviews() -> int:
    """Sweep every firm schema (Postgres) / the single schema (SQLite).

    Returns the number of claims recovered. NEVER raises — a recovery failure
    must not block app startup.
    """
    total = 0
    try:
        if is_postgres(engine):
            with SessionLocal() as db:
                firm_ids = list(db.execute(select(BrokerFirm.id)).scalars().all())
            for firm_id in firm_ids:
                with SessionLocal() as db:
                    set_search_path(db, firm_id)
                    n = _recover_current_schema(db)
                    if n:
                        db.commit()
                        total += n
                        logger.warning(
                            "Recovered %s stranded claim review(s) in firm %s", n, firm_id
                        )
        else:
            with SessionLocal() as db:
                total = _recover_current_schema(db)
                if total:
                    db.commit()
                    logger.warning("Recovered %s stranded claim review(s)", total)
    except Exception:
        logger.exception("Stranded-review recovery sweep failed")
    return total
