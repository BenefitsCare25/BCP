"""Stage 3 — claim-form ↔ extracted-fields comparison via the AI gateway.

Sends the member's ``form_fields`` snapshot, every document's extracted
fields, the in-code field maps, AI-judged business rules, and the required
document families for the claim type. Folds the required-documents check into
rule results (a missing required document is a failed rule for the verdict).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Claim
from app.services import ai_gateway
from app.services.claims_review.field_maps import (
    AI_RULES,
    FIELD_MAPS,
    required_documents_for,
)


def compare_claim(
    db: Session,
    claim: Claim,
    extractions: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the comparison call. Returns ``(review, call_metadata)`` where
    review has ``field_comparisons``, ``rule_results`` (AI rules + required-doc
    checks, source="ai"), ``summary`` and ``confidence``."""
    claim_fields = dict(claim.form_fields or {})
    # benefit context helps the model judge the rules.
    claim_fields.setdefault("claim_kind", claim.claim_kind)
    if claim.product_code:
        claim_fields.setdefault("product_code", claim.product_code)
    if claim.benefit_key:
        claim_fields.setdefault("benefit_key", claim.benefit_key)
    if claim.flex_category_name:
        claim_fields.setdefault("flex_category_name", claim.flex_category_name)

    required_docs = required_documents_for(claim.claim_type)
    result = ai_gateway.review_claim(
        db,
        client_id=claim.client_id,
        policy_year_id=claim.policy_year_id,
        claim_fields=claim_fields,
        documents=[
            {
                "file_name": e["file_name"],
                "document_type": e["document_type"],
                "fields": e["fields"],
            }
            for e in extractions
        ],
        field_maps=FIELD_MAPS,
        ai_rules=AI_RULES,
        required_documents=required_docs,
    )

    rule_results = [
        {**r, "source": "ai"} for r in result.review.get("rule_results", [])
    ]
    for check in result.review.get("required_documents_check", []):
        found = bool(check.get("found"))
        rule_results.append(
            {
                "rule": f"Required document present: {check.get('document_type_name')}",
                "status": "pass" if found else "fail",
                "source": "ai",
                "evidence": str(check.get("notes") or ("Found." if found else "Not found.")),
            }
        )

    review = {
        "field_comparisons": result.review.get("field_comparisons", []),
        "rule_results": rule_results,
        "summary": result.review.get("summary", ""),
        "confidence": float(result.review.get("confidence") or 0.0),
    }
    return review, result.metadata
