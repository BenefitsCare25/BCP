"""Stage 3 — claim-form ↔ extracted-fields comparison via the AI gateway.

Sends the member's ``form_fields`` snapshot, every document's extracted
fields, the claim type's resolved field maps, severity-tagged AI business
rules, and the required document families. Folds the required-documents check
into rule results (a missing required document is a failed rule for the
verdict). The configuration comes from the claim's per-claim-type review
config (``services/claim_review_configs.py``) — in-code defaults when the
company hasn't customized that claim type.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Claim, Dependant, Employee
from app.services import ai_gateway
from app.services.claim_intake import claim_profile_for, required_doc_slots
from app.services.claim_review_configs import (
    ReviewConfig,
    attribute_rule_results,
    rendered_rules,
    resolve_review_config,
)
from app.services.claims_review.field_maps import required_documents_for
from app.services.roster_attributes import NAME_KEYS, REL_KEYS, first_value


def compare_claim(
    db: Session,
    claim: Claim,
    extractions: list[dict[str, Any]],
    config: ReviewConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the comparison call. Returns ``(review, call_metadata)`` where
    review has ``field_comparisons``, ``rule_results`` (AI rules + required-doc
    checks, source="ai"), ``summary`` and ``confidence``."""
    cfg = config if config is not None else resolve_review_config(db, claim)
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

    # The derived families ALWAYS apply: the referral requirement is a
    # per-product profile fact (never inferred from the free-text claim_type /
    # display name) and the families come from the claim's resolved slots,
    # which are hospital-sector AND sub-type aware. A claim type's configured
    # list ADDS to them — the config is per claim type, so letting it replace
    # the derivation would apply one setting's document set to every sub-type
    # and could drop a guaranteed referral check.
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
    seen = {d.strip().lower() for d in required_docs}
    for extra in cfg.required_documents or ():
        if extra.strip().lower() not in seen:
            seen.add(extra.strip().lower())
            required_docs.append(extra)
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
        field_maps=list(cfg.field_maps),
        ai_rules=rendered_rules(cfg),
        required_documents=required_docs,
    )

    # Attribute severity/category back onto the AI's echoed rules FIRST —
    # a failed warning/info rule becomes a "warning" result and never
    # auto-flags; unmatched failed rules stay "fail" (fail-safe).
    rule_results = [
        {**r, "source": "ai"}
        for r in attribute_rule_results(
            cfg, list(result.review.get("rule_results", []))
        )
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
