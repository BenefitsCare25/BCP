"""Pipeline orchestrator — runs as a FastAPI BackgroundTask after submit.

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
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.tenancy import set_search_path
from app.models import Claim, ClaimAIReview, Employee, StoredDocument
from app.models.claim import (
    CLAIM_STATUS_AI_FLAGGED,
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_SUBMITTED,
)
from app.models.claim_ai_review import (
    REVIEW_STATUS_COMPLETE,
    REVIEW_STATUS_ERROR,
    REVIEW_VERDICT_CLEAN,
    REVIEW_VERDICT_FLAGGED,
)
from app.services import ai_gateway
from app.services.ai_extractor import AINotConfiguredError
from app.services.claim_doc_types import resolve_doc_types
from app.services.claim_review_configs import resolve_review_config
from app.services.claims import claim_documents
from app.services.claims_review import (
    comparison,
    doc_completeness,
    extraction,
    rules,
    vision_verify,
)
from app.services.claims_review.verdict import compute_verdict
from app.services.member_statement import build_member_statement

logger = logging.getLogger(__name__)


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
    """
    db.refresh(claim)
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
    """Execute one AI review run. Designed for BackgroundTasks — never raises."""
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
        except AINotConfiguredError as exc:
            review.status = REVIEW_STATUS_ERROR
            review.error_detail = str(exc)
            _fall_back_to_manual(db, claim)
            logger.warning("Claim %s AI review skipped: %s", claim.id, exc)
        except Exception as exc:
            review.status = REVIEW_STATUS_ERROR
            review.error_detail = f"{type(exc).__name__}: {exc}"
            _fall_back_to_manual(db, claim)
            logger.exception("Claim %s AI review failed", claim.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Claim %s AI review could not be persisted", claim_id)
        # The rollback above just discarded _fall_back_to_manual's status
        # write too — without this, the claim is stranded at
        # `ai_review_pending` forever. Re-apply the safety net in its own
        # minimal transaction so the "never block the member" guarantee
        # survives a failed review persist.
        try:
            set_search_path(db, broker_firm_id)
            claim = db.get(Claim, claim_id)
            if claim is not None and claim.status == CLAIM_STATUS_AI_REVIEW_PENDING:
                claim.status = CLAIM_STATUS_SUBMITTED
                review = db.get(ClaimAIReview, review_id)
                if review is not None and review.status not in (
                    REVIEW_STATUS_COMPLETE, REVIEW_STATUS_ERROR
                ):
                    review.status = REVIEW_STATUS_ERROR
                    review.error_detail = "Review results could not be persisted."
                db.commit()
        except Exception:
            db.rollback()
            logger.critical(
                "Claim %s is stranded in ai_review_pending — the manual-review "
                "fallback could not be persisted either. Broker must re-run "
                "the review.", claim_id,
            )
    finally:
        db.close()


def _run_stages(
    db, claim: Claim, review: ClaimAIReview, broker_firm_id: str | None = None
) -> None:
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
        _finalize_claim_status(db, claim, REVIEW_VERDICT_FLAGGED)
        return

    docs = claim_documents(db, claim)
    # The referral letter is a member-level document (entity_type="referral"),
    # not a claim attachment — include it so extraction sees it and the
    # specialist required-documents check can pass.
    if claim.referral_document_id:
        referral = db.get(StoredDocument, claim.referral_document_id)
        if referral is not None:
            docs.append(referral)
    all_calls: list[dict[str, Any]] = []

    # Stage 2 — extraction (cached per document hash).
    extractions, doc_warnings, calls = extraction.extract_documents(
        db, claim, docs, broker_firm_id
    )
    all_calls.extend(calls)
    # Deterministic key-field completeness per recognised document type
    # (broker-side warnings only — never blocks or auto-flags the member).
    # Definitions come from the client's configured registry (defaults when
    # none stored).
    doc_warnings = doc_warnings + doc_completeness.doc_completeness_results(
        claim, extractions, resolve_doc_types(db, claim.client_id)
    )

    # Stage 3 — comparison + AI-judged rules + required-documents check.
    ai_review, call_meta = comparison.compare_claim(db, claim, extractions, cfg)
    all_calls.append(call_meta)

    # Stage 4 — selective vision verification (cap in vision_verify).
    comparisons, vision_checks, calls = vision_verify.run_vision_checks(
        db, claim, docs, ai_review["field_comparisons"],
        vision_fields=cfg.vision_fields,
    )
    all_calls.extend(calls)

    # Stage 5 — verdict.
    rule_results = det_results + doc_warnings + ai_review["rule_results"]
    verdict, reasons = compute_verdict(
        rule_results, comparisons, vision_checks, ai_review["confidence"],
        evidence_fields=cfg.evidence_fields,
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
    _apply_call_metadata(review, all_calls)
    _finalize_claim_status(db, claim, verdict)
