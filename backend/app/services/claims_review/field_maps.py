"""Default review configuration — field maps, required documents, AI rules.

These are the IN-CODE DEFAULTS: `services/claim_review_configs.py` overlays
them with the per-company, per-claim-type rows the broker edits on the Claims
page (Review rules tab), and the required-document derivation below stays
live whenever a claim type doesn't override it. A field map pairs a
claim-form field with the document field family it should match, plus the
comparison mode:

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
        # Present only in the form snapshot for the hospital-stay subtype, so
        # the shared default remains inert for every outpatient/pre-post claim.
        "portal_field": "admission_date",
        "document_field": "Admission Date",
        "mode": "fuzzy",
        "verify_with_vision": True,
    },
    {
        "portal_field": "discharge_date",
        "document_field": "Discharge Date",
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
    {
        # Receipts don't always state one — absence should read as UNCERTAIN,
        # not MISMATCH; a medical report naming a different condition is the
        # signal this exists to catch.
        "portal_field": "diagnosis",
        "document_field": "Diagnosis / Condition / Treatment Description",
        "mode": "fuzzy",
        "verify_with_vision": False,
    },
]

# Fields worth a selective vision re-check (amount, date, provider). Single
# source of truth — both the vision pass (which statuses it re-checks) and the
# verdict (which MISSING_IN_PDF fields it flags) derive from this.
VISION_FIELDS = frozenset(
    m["portal_field"] for m in FIELD_MAPS if m.get("verify_with_vision")
)

# Claim-type keyword → required document families. The GHS sub-claim type is
# consulted FIRST (it states the treatment setting precisely — "Pre and Post
# Hospitalisation" must not demand a discharge summary just because the
# product name contains "Surgical"); the claim_type keyword match is the
# fallback. Family names are checked by the AI review with generous semantic
# matching.
_DEFAULT_REQUIRED_DOCS = ["receipt or tax invoice"]

_SUB_TYPE_REQUIRED_DOCS: dict[str, list[str]] = {
    "hospitalisation/day surgery/other inpatient treatment": [
        "hospital bill or tax invoice",
        "discharge summary or medical report",
    ],
    "follow up pre-/post-hospitalisation": ["receipt or tax invoice"],
    "emergency accidental outpatient treatment": ["receipt or tax invoice"],
    "kidney dialysis/cancer treatment": ["receipt or tax invoice"],
    # GP riders (see claim_intake.GP_SUB_TYPES).
    "tcm (traditional chinese medicine)": ["receipt or tax invoice"],
    "physiotherapy": ["receipt or tax invoice"],
    # Pre-rename labels still on old claims — reruns must keep resolving.
    "hospitalisation or day surgery": [
        "hospital bill or tax invoice",
        "discharge summary or medical report",
    ],
    "pre and post hospitalisation": ["receipt or tax invoice"],
    "outpatient kidney dialysis and cancer treatment": ["receipt or tax invoice"],
}

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


# Required-document slot key (claim_intake.required_doc_slots) → the document
# family the AI review verifies. Phrased generously — the review matches
# semantically, so "final bill" satisfies the finalised-tax-invoice family.
SLOT_DOC_FAMILIES: dict[str, str] = {
    "invoice_receipt": "receipt or tax invoice",
    "sp_invoice": "specialist clinic or hospital invoice",
    "finalised_tax_invoice": "finalised tax invoice or final hospital bill",
    "summary_tax_invoice": "summary tax invoice or hospital bill summary",
    "itemised_tax_invoice": "itemised tax invoice or detailed hospital bill",
    "discharge_summary": "discharge summary or medical report",
}


def required_documents_for(
    claim_type: str,
    sub_type: str | None = None,
    *,
    requires_referral: bool = False,
    slot_keys: list[str] | None = None,
) -> list[str]:
    """Required document families for a claim. When the caller supplies the
    claim's resolved document slots (``slot_keys``, from
    `claim_intake.required_doc_slots`) those are authoritative; otherwise the
    GHS sub-type is consulted first, then the claim_type keyword fallback.
    ``requires_referral`` is the authoritative per-product signal from the
    intake profile (never the free-text claim_type / display name): when set,
    the referral-letter family is guaranteed present so the AI doc-check
    verifies it regardless of how the product happens to be named."""
    sub = (sub_type or "").strip().lower()
    if slot_keys:
        docs = [SLOT_DOC_FAMILIES[k] for k in slot_keys if k in SLOT_DOC_FAMILIES]
    elif sub in _SUB_TYPE_REQUIRED_DOCS:
        docs = list(_SUB_TYPE_REQUIRED_DOCS[sub])
    else:
        docs = list(_DEFAULT_REQUIRED_DOCS)
        wanted = (claim_type or "").strip().lower()
        for keywords, mapped in REQUIRED_DOCUMENTS:
            if any(k in wanted for k in keywords):
                docs = list(mapped)
                break
    if requires_referral and not any("referral" in d.lower() for d in docs):
        docs.append("referral letter or memo")
    return docs


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
    "The treatment setting evidenced by the documents must match the claim "
    "type and sub-type: a GP claim should show a general-practice clinic "
    "visit (an inpatient hospital bill means the wrong claim type was "
    "chosen); a specialist claim should show a specialist consultation; a "
    "'Hospitalisation/Day Surgery/Other Inpatient Treatment' claim should "
    "show an inpatient or day surgery bill; a TCM claim should show a "
    "registered TCM practitioner or Chinese physician visit; a "
    "physiotherapy claim should show a physiotherapy session.",
    "If a diagnosis is stated on the documents, it should be consistent with "
    "the declared diagnosis and plausible for the claim type.",
]
