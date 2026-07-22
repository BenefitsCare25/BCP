"""Startup recovery for claim AI reviews stranded by a mid-flight restart.

FastAPI ``BackgroundTasks`` are in-process and non-durable: a deploy or crash
in the window after ``submit_claim`` commits ``status=ai_review_pending`` but
before ``run_review`` finishes leaves the claim stuck in ``ai_review_pending``
with its ``ClaimAIReview`` row still ``pending`` and no task alive to move it.

This sweep runs once at app startup (a deploy IS a restart, so it fires exactly
when strandings happen) and reverts such claims to ``submitted`` — the same
manual-review fallback ``run_review`` applies on failure. The claim becomes
visible/actionable in the broker queue again; a broker can re-run the AI review
from there. Reverting (rather than re-dispatching) is idempotent and race-free,
so two instances starting against the same database can't double-spend or
collide on a review row.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, set_search_path
from app.models import BrokerFirm, Claim, ClaimAIReview
from app.models.claim import CLAIM_STATUS_AI_REVIEW_PENDING, CLAIM_STATUS_SUBMITTED
from app.models.claim_ai_review import REVIEW_STATUS_ERROR, REVIEW_STATUS_PENDING

logger = logging.getLogger(__name__)


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
