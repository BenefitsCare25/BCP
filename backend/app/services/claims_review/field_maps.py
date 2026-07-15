"""Static review configuration — field maps, required documents, AI rules.

In-code for v1 (per the plan); a future iteration can move these to a
DB-driven per-client template. A field map pairs a claim-form field with the
document field family it should match, plus the comparison mode:

- ``fuzzy``   — semantic equivalence, formatting ignored (dates get the
  any-date-field fallback scan, see the review prompt).
- ``numeric`` — numbers within ``tolerance`` match.
- ``exact``   — any difference is a MISMATCH.

``verify_with_vision`` marks fields worth a selective vision re-check when
the text comparison says MISMATCH/UNCERTAIN.
"""
from __future__ import annotations

from typing import Any

FIELD_MAPS: list[dict[str, Any]] = [
    {
        "portal_field": "amount_claimed",
        "document_field": "Total Amount / Amount Paid",
        "mode": "numeric",
        "tolerance": 0.01,
        "verify_with_vision": True,
    },
    {
        "portal_field": "incurred_date",
        "document_field": "Invoice / Visit / Treatment Date",
        "mode": "fuzzy",
        "verify_with_vision": True,
    },
    {
        "portal_field": "provider_name",
        "document_field": "Clinic / Hospital / Provider Name",
        "mode": "fuzzy",
        "verify_with_vision": True,
    },
    {
        "portal_field": "invoice_number",
        "document_field": "Invoice / Receipt / Bill Number",
        "mode": "fuzzy",
        "verify_with_vision": False,
    },
    {
        "portal_field": "currency",
        "document_field": "Currency",
        "mode": "fuzzy",
        "verify_with_vision": False,
    },
]

# Claim-type keyword → required document families. Matched against the
# member-entered free-text claim_type (lowercased substring match); first
# matching entry wins, ``_DEFAULT_REQUIRED_DOCS`` otherwise. Family names are
# checked by the AI review with generous semantic matching.
_DEFAULT_REQUIRED_DOCS = ["receipt or tax invoice"]

REQUIRED_DOCUMENTS: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("inpatient", "hospitalisation", "hospitalization", "surgery", "surgical"),
        ["hospital bill or tax invoice", "discharge summary or medical report"],
    ),
    (
        ("specialist",),
        ["receipt or tax invoice", "referral letter or memo"],
    ),
]


def required_documents_for(claim_type: str) -> list[str]:
    wanted = (claim_type or "").strip().lower()
    for keywords, docs in REQUIRED_DOCUMENTS:
        if any(k in wanted for k in keywords):
            return list(docs)
    return list(_DEFAULT_REQUIRED_DOCS)


# Business rules the AI judges against all available data (claim form +
# extracted document fields). Deterministic rules live in ``rules.py``.
AI_RULES: list[str] = [
    "The submitted documents must be proof of actual treatment/payment — a "
    "quotation, pro-forma invoice, appointment reminder, or marketing "
    "material does not qualify.",
    "The patient named on the documents must plausibly be the claimant or the "
    "declared dependant (allow abbreviations and name-order differences).",
    "The documents must not indicate the amount was already paid or payable "
    "by another insurer, employer, or third party (e.g. 'company billed', "
    "'insurance portion').",
    "The document date(s) must be consistent with the stated incurred date — "
    "a bill issued long before the claimed treatment date is a concern.",
]
