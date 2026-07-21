"""Stage 3 — claim-form ↔ extracted-fields comparison via the AI gateway.

Sends the member's ``form_fields`` snapshot, every document's extracted
fields, the in-code field maps, AI-judged business rules, and the required
document families for the claim type. Folds the required-documents check into
rule results (a missing required document is a failed rule for the verdict).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Claim, Dependant, Employee
from app.services import ai_gateway
from app.services.claim_intake import claim_profile_for, required_doc_slots
from app.services.claims_review.field_maps import (
    AI_RULES,
    FIELD_MAPS,
    required_documents_for,
)
from app.services.roster_attributes import NAME_KEYS, REL_KEYS, first_value


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
    if claim.sub_type:
        claim_fields.setdefault("sub_type", claim.sub_type)
    if claim.visit_type:  # SP claims: first vs follow-up visit
        claim_fields.setdefault("visit_type", claim.visit_type)
    if claim.benefit_key:  # legacy claims created before the Benefit field was removed
        claim_fields.setdefault("benefit_key", claim.benefit_key)
    if claim.flex_category_name:
        claim_fields.setdefault("flex_category_name", claim.flex_category_name)

    # Who the patient should be — without this the "patient named on the
    # documents must be the claimant or declared dependant" rule has nothing
    # to compare against.
    employee = db.get(Employee, claim.employee_id)
    if employee is not None and employee.employee_name:
        claim_fields["policyholder_name"] = employee.employee_name
    if claim.dependant_id:
        dep = db.get(Dependant, claim.dependant_id)
        av = (dep.attribute_values or {}) if dep is not None else {}
        claim_fields["claimant_name"] = first_value(av, NAME_KEYS)
        claim_fields["claimant_relationship"] = first_value(av, REL_KEYS)
        claim_fields["claimant_is_dependant"] = True
    elif employee is not None and employee.employee_name:
        claim_fields["claimant_name"] = employee.employee_name

    # The referral requirement is a per-product profile fact, not something to
    # infer from the free-text claim_type / display name — and the document
    # families come from the claim's resolved slots (hospital-sector aware).
    required_docs = required_documents_for(
        claim.claim_type,
        claim.sub_type,
        requires_referral=claim_profile_for(claim.product_code).requires_referral,
        slot_keys=required_doc_slots(
            claim.product_code,
            claim.sub_type,
            claim.provider_name,
            claim_kind=claim.claim_kind,
        ),
    )
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
