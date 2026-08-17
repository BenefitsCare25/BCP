"""One-time reconciliation of reviews created before the durable queue."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, set_search_path
from app.models import BrokerFirm, Claim, ClaimAIReview, ClaimReviewJob
from app.models.claim import (
    CLAIM_STATUS_AI_FLAGGED,
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_SUBMITTED,
)
from app.models.claim_ai_review import (
    REVIEW_STATUS_COMPLETE,
    REVIEW_STATUS_ERROR,
    REVIEW_STATUS_PENDING,
)
from app.services.claims_review.queue import active_job, configuration_ready

logger = logging.getLogger(__name__)


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
