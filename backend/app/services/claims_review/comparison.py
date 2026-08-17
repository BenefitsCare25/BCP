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

import math
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


def _key(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").split()).casefold()


def _field_aliases(field_map: dict[str, Any]) -> set[str]:
    portal = str(field_map.get("portal_field") or "")
    document = str(field_map.get("document_field") or "")
    aliases = {_key(portal), _key(document)}
    if portal.endswith("_name"):
        aliases.add(_key(portal.removesuffix("_name")))
    for part in document.replace("/", "|").split("|"):
        if part.strip():
            aliases.add(_key(part))
    return {alias for alias in aliases if alias}


def _incomplete_result(kind: str, name: str) -> dict[str, Any]:
    return {
        "rule": f"AI returned every configured {kind} result.",
        "status": "fail",
        "source": "platform",
        "severity": "critical",
        "error_code": "ai_output_incomplete",
        "evidence": f"The AI response omitted or duplicated {kind}: {name}.",
    }


def _reconcile_comparisons(
    config: ReviewConfig,
    claim_fields: dict[str, Any],
    returned: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for item in config.field_maps:
        portal_field = str(item.get("portal_field") or "")
        if not portal_field or claim_fields.get(portal_field) in (None, ""):
            continue
        canonical = _key(portal_field)
        expected[canonical] = portal_field
        for alias in _field_aliases(item):
            aliases.setdefault(alias, canonical)
    by_field: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for item in returned:
        field = aliases.get(_key(item.get("field_name")))
        if field not in expected:
            continue
        if not field or field in by_field:
            failures.append(
                _incomplete_result(
                    "field comparison", str(item.get("field_name") or "blank")
                )
            )
            continue
        status = item.get("status")
        confidence = item.get("confidence")
        valid_confidence = isinstance(confidence, (int, float)) and math.isfinite(
            float(confidence)
        ) and 0.0 <= float(confidence) <= 1.0
        if status not in {
            "MATCH",
            "MISMATCH",
            "MISSING_IN_PDF",
            "MISSING_ON_PAGE",
            "UNCERTAIN",
        } or not valid_confidence:
            failures.append(_incomplete_result("field comparison", expected[field]))
            by_field[field] = {
                **item,
                "field_name": expected[field],
                "status": "UNCERTAIN",
                "confidence": 0.0,
                "notes": "The AI returned an invalid comparison result.",
            }
            continue
        by_field[field] = {**item, "field_name": expected[field]}
    for field, display in expected.items():
        if field in by_field:
            continue
        failures.append(_incomplete_result("field comparison", display))
        by_field[field] = {
            "field_name": display,
            "claim_value": None,
            "document_value": None,
            "status": "UNCERTAIN",
            "confidence": 0.0,
            "notes": "The AI response omitted this configured comparison.",
        }
    return list(by_field.values()), failures


def _reconcile_rules(
    config: ReviewConfig, attributed: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = {rule.id: rule for rule in config.ai_rules}
    seen: set[str] = set()
    failures: list[dict[str, Any]] = []
    for item in attributed:
        rule_id = str(item.get("rule_id") or "")
        if rule_id in expected and rule_id not in seen:
            seen.add(rule_id)
            if item.get("status") not in {
                "pass",
                "fail",
                "warning",
                "not_applicable",
            }:
                failures.append(
                    _incomplete_result("business rule", expected[rule_id].rule)
                )
        elif rule_id in expected:
            failures.append(_incomplete_result("business rule", expected[rule_id].rule))
    for rule_id, rule in expected.items():
        if rule_id not in seen:
            failures.append(_incomplete_result("business rule", rule.rule))
    return attributed + failures


def _reconcile_required_documents(
    expected: list[str], returned: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected_names = {_key(name): name for name in expected}
    by_name: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for item in returned:
        name = _key(item.get("document_type_name"))
        if name not in expected_names:
            continue
        if not name or name in by_name:
            failures.append(
                _incomplete_result(
                    "required-document check",
                    str(item.get("document_type_name") or "blank"),
                )
            )
            continue
        if not isinstance(item.get("found"), bool):
            failures.append(
                _incomplete_result("required-document check", expected_names[name])
            )
            by_name[name] = {**item, "found": False}
            continue
        by_name[name] = item
    for name, display in expected_names.items():
        if name not in by_name:
            failures.append(_incomplete_result("required-document check", display))
    return list(by_name.values()), failures


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
    comparisons, comparison_failures = _reconcile_comparisons(
        cfg, claim_fields, list(result.review.get("field_comparisons", []))
    )
    rule_results = [
        {**r, "source": "ai"}
        for r in attribute_rule_results(
            cfg, list(result.review.get("rule_results", []))
        )
    ]
    rule_results = _reconcile_rules(cfg, rule_results)
    doc_checks, doc_failures = _reconcile_required_documents(
        required_docs, list(result.review.get("required_documents_check", []))
    )
    rule_results.extend(comparison_failures + doc_failures)
    confidence = result.review.get("confidence")
    if not isinstance(confidence, (int, float)) or not math.isfinite(
        float(confidence)
    ) or not 0.0 <= float(confidence) <= 1.0:
        confidence = 0.0
        rule_results.append(_incomplete_result("overall confidence", "confidence"))
    for check in doc_checks:
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
        "field_comparisons": comparisons,
        "rule_results": rule_results,
        "summary": result.review.get("summary", ""),
        "confidence": float(confidence),
    }
    return review, result.metadata
