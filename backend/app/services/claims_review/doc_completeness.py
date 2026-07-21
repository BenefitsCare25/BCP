"""Deterministic document-completeness check — runs after extraction.

Classifies each extracted document against the broker's document-type
registry (`claim_doc_types`) and checks its KEY FIELDS are present — a
discharge summary must show a diagnosis and surgery/procedure, a government
finalised tax invoice must show admission/discharge dates + schemes + HRN,
a private final tax invoice must show case number/dates/final bill/HRN.

BROKER-SIDE ONLY by design (2026-07-21): results surface as review rule
results, never a member-facing block — an OCR miss or an unusual hospital
layout must not stop a legitimate claim. ``warning`` status, so verdict
computation (fails only) is untouched; the broker sees it in the queue.
"""
from __future__ import annotations

from typing import Any

from app.models import Claim
from app.services.claim_doc_types import classify_document, missing_key_fields
from app.services.sg_hospitals import hospital_sector


def _result(rule: str, status: str, evidence: str) -> dict[str, Any]:
    return {
        "rule": rule,
        "status": status,
        "source": "deterministic",
        "evidence": evidence,
    }


def doc_completeness_results(
    claim: Claim, extractions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    sector = hospital_sector(claim.provider_name)
    results: list[dict[str, Any]] = []
    for ext in extractions:
        fields = [f for f in (ext.get("fields") or []) if isinstance(f, dict)]
        defn = classify_document(
            ext.get("document_type"), fields, sector_hint=sector
        )
        if defn is None:
            continue
        name = str(ext.get("file_name") or "document")
        rule = f"{defn.display} shows its expected key fields."
        missing = missing_key_fields(defn, fields)
        if missing:
            results.append(
                _result(
                    rule,
                    "warning",
                    f'"{name}" was identified as a {defn.display} but is '
                    f"missing: {', '.join(missing)}. Confirm the right "
                    "document was submitted and is complete.",
                )
            )
        else:
            results.append(
                _result(
                    rule,
                    "pass",
                    f'"{name}" shows all expected {defn.display} fields.',
                )
            )
        # Sector cross-check: a govt-format bill on a private-hospital claim
        # (or vice versa) means the hospital or the document is wrong.
        if defn.sector is not None and sector is not None and defn.sector != sector:
            results.append(
                _result(
                    "Invoice format matches the hospital's sector.",
                    "warning",
                    f'"{name}" looks like a '
                    f"{'government' if defn.sector == 'govt' else 'private'}"
                    f"-hospital bill, but the claim names a "
                    f"{'government' if sector == 'govt' else 'private'} "
                    f"hospital ({claim.provider_name}).",
                )
            )
    return results
