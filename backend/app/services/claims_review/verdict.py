"""Stage 5 — the verdict.

``clean`` iff: no failed rule (deterministic or AI), no remaining MISMATCH
after vision verification, no REFUTED vision check, and the review confidence
clears the threshold. Anything else → ``flagged`` (the broker decides either
way — the verdict only orders the queue).
"""
from __future__ import annotations

from typing import Any

from app.models.claim_ai_review import REVIEW_VERDICT_CLEAN, REVIEW_VERDICT_FLAGGED

CONFIDENCE_THRESHOLD = 0.5


def compute_verdict(
    rule_results: list[dict[str, Any]],
    field_comparisons: list[dict[str, Any]],
    vision_checks: list[dict[str, Any]],
    confidence: float,
) -> tuple[str, list[str]]:
    """Returns ``(verdict, reasons)`` — reasons explain a flagged verdict."""
    reasons: list[str] = []
    for r in rule_results:
        if r.get("status") == "fail":
            reasons.append(f"Rule failed: {r.get('rule')}")
    for c in field_comparisons:
        if c.get("status") == "MISMATCH":
            reasons.append(f"Field mismatch: {c.get('field_name')}")
    for v in vision_checks:
        if v.get("verdict") == "REFUTED":
            reasons.append(f"Vision check refuted: {v.get('field_name')}")
    if not reasons and confidence < CONFIDENCE_THRESHOLD:
        reasons.append(
            f"Review confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD}."
        )
    verdict = REVIEW_VERDICT_FLAGGED if reasons else REVIEW_VERDICT_CLEAN
    return verdict, reasons
