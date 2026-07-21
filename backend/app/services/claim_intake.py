"""Claim-intake profiles — what the submission form asks per product type.

Singapore has no public claims-diagnosis API (data.gov.sg only publishes
aggregate ICD-10-AM statistics such as "Top 10 Conditions of
Hospitalisation"), so the diagnosis catalog is bundled in-code
(`sg_diagnoses.py`) — same v1 convention as the AI-review field maps.

A profile keys off the registry product code and drives BOTH the frontend
form (via `/portal/coverage-options`) and the backend validation
(`assert_intake_valid`, called on create AND submit so a stale draft can't
slip through):

- ``sub_types``          — GHS-family claims must name a sub-claim type
                           (hospitalisation vs pre/post vs A&E vs dialysis).
- ``requires_referral``  — specialist claims must attach a referral letter
                           (or explicitly declare it not applicable).
- ``diagnosis_group``    — which slice of the diagnosis catalog the member
                           searches; ``diagnosis_required`` gates submit.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Employee, StoredDocument
from app.models.claim import CLAIM_KIND_INSURED
from app.models.stored_document import DOC_ENTITY_REFERRAL

# Currencies a member may incur a bill in — single source of truth, exposed
# to the frontend via /portal/coverage-options.
ALLOWED_CURRENCIES: tuple[str, ...] = (
    "SGD", "USD", "MYR", "EUR", "GBP", "AUD",
    "HKD", "CNY", "JPY", "INR", "IDR", "THB", "PHP",
)

# Inpatient sub-claim types (broker-specified wording, 2026-07-21).
GHS_SUB_TYPES: tuple[str, ...] = (
    "Follow up Pre-/Post-Hospitalisation",
    "Hospitalisation/Day Surgery/Other Inpatient Treatment",
    "Emergency Accidental Outpatient Treatment",
    "Kidney Dialysis/Cancer Treatment",
)

# Pre-rename sub-type values still stored on old claims/drafts — normalized on
# validation so a draft created before the relabel can still submit, and reruns
# of old AI reviews keep working.
LEGACY_SUB_TYPES: dict[str, str] = {
    "hospitalisation or day surgery": GHS_SUB_TYPES[1],
    "pre and post hospitalisation": GHS_SUB_TYPES[0],
    "outpatient kidney dialysis and cancer treatment": GHS_SUB_TYPES[3],
}

# Outpatient treatment types that ride on GP coverage rather than their own
# product. Offered (and valid) only when the member's GP plan schedule carries
# a matching benefit row; the claim is bucketed against that row via
# `benefit_key`, so its annual limit (when stated) drives utilization and the
# broker's over-limit approve guard.
SUB_TYPE_TCM = "TCM (Traditional Chinese Medicine)"
SUB_TYPE_PHYSIO = "Physiotherapy"
GP_SUB_TYPES: tuple[str, ...] = (SUB_TYPE_TCM, SUB_TYPE_PHYSIO)

# Keywords that identify the SOB row funding each GP-rider sub-type.
_SUB_TYPE_ROW_KEYWORDS: dict[str, tuple[str, ...]] = {
    SUB_TYPE_TCM: ("tcm", "traditional chinese", "chinese physician"),
    SUB_TYPE_PHYSIO: ("physio",),
}

CATEGORY_OUTPATIENT = "outpatient"
CATEGORY_INPATIENT = "inpatient"
CATEGORY_OTHER = "other"


def normalize_sub_type(sub_type: str | None) -> str | None:
    """Fold a legacy stored sub-type label onto its current name."""
    if not sub_type:
        return sub_type
    return LEGACY_SUB_TYPES.get(sub_type.strip().lower(), sub_type)


def benefit_row_for_sub_type(
    benefit_schedule: dict | None, sub_type: str | None
) -> str | None:
    """The schedule row name funding a GP-rider sub-type (TCM/Physio), or None
    when the plan doesn't carry one. Read defensively — `benefit_schedule` is
    untyped JSON server-side."""
    keywords = _SUB_TYPE_ROW_KEYWORDS.get(sub_type or "")
    if not keywords:
        return None
    items = (benefit_schedule or {}).get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if name and any(k in name.lower() for k in keywords):
            return name
    return None

# Free-text fallback the UI offers when no catalog entry fits. Anything the
# member types rides behind this prefix so brokers can spot unlisted
# conditions at a glance.
DIAGNOSIS_OTHER = "Other"
_OTHER_PREFIX = "other:"


def effective_diagnosis(diagnosis: str | None) -> str:
    """The diagnosis with the UI's ``Other:`` sentinel resolved — a bare
    ``Other`` / ``Other:`` (no condition after it) is not a diagnosis, so it
    normalizes to empty. Keeps the required-diagnosis rule honest server-side
    even when a request skips the frontend."""
    text = (diagnosis or "").strip()
    low = text.lower()
    if low in ("other", "other:"):
        return ""
    if low.startswith(_OTHER_PREFIX):
        return text[len(_OTHER_PREFIX):].strip()
    return text


@dataclass(frozen=True)
class ClaimIntakeProfile:
    sub_types: tuple[str, ...] = ()
    # GHS-family: the sub-type is the treatment setting and must be chosen.
    # GP-family: the sub-types are optional riders (TCM/Physio) — a plain GP
    # claim carries none.
    sub_type_required: bool = True
    requires_referral: bool = False
    diagnosis_group: str | None = None
    diagnosis_required: bool = False
    # Outpatient / Inpatient grouping for the claim-form dropdown; products
    # outside the taxonomy (life, accident, maternity…) group under "other".
    category: str = CATEGORY_OTHER
    # Dropdown label for the claim type ("GP", "SP", …); None = product name.
    claim_type_label: str | None = None


_HOSPITAL = ClaimIntakeProfile(
    sub_types=GHS_SUB_TYPES,
    diagnosis_group="hospital",
    diagnosis_required=True,
    category=CATEGORY_INPATIENT,
)
_GP = ClaimIntakeProfile(
    sub_types=GP_SUB_TYPES,
    sub_type_required=False,
    diagnosis_group="gp",
    diagnosis_required=True,
    category=CATEGORY_OUTPATIENT,
    claim_type_label="GP (General Practitioner)",
)
_SP = ClaimIntakeProfile(
    diagnosis_group="sp",
    diagnosis_required=True,
    requires_referral=True,
    category=CATEGORY_OUTPATIENT,
    claim_type_label="SP (Specialist)",
)
_DENTAL = ClaimIntakeProfile(
    diagnosis_group="dental",
    diagnosis_required=True,
    category=CATEGORY_OUTPATIENT,
    claim_type_label="Dental",
)

_PROFILES: dict[str, ClaimIntakeProfile] = {
    "GHS": _HOSPITAL,
    "GHS2": _HOSPITAL,
    "GMM": _HOSPITAL,
    "GMM2": _HOSPITAL,
    "IMP": _HOSPITAL,
    "GP": _GP,
    "GCGP": _GP,
    "GOGP": _GP,
    "SP": _SP,
    "GCSP": _SP,
    "GOSP": _SP,
    "GD": _DENTAL,
    "DENTAL": _DENTAL,
    "MATERNITY": ClaimIntakeProfile(diagnosis_group="maternity"),
}

_EMPTY = ClaimIntakeProfile()


def claim_profile_for(product_code: str | None) -> ClaimIntakeProfile:
    return _PROFILES.get((product_code or "").strip().upper(), _EMPTY)


def assert_intake_valid(
    db: Session,
    employee: Employee,
    *,
    claim_kind: str,
    product_code: str | None,
    sub_type: str | None,
    diagnosis: str | None,
    referral_document_id: str | None,
    referral_not_applicable: bool,
    currency: str,
) -> None:
    """Profile-driven form validation. 422 with a member-readable message so
    the wrong claim type / missing sub-type / missing referral can't be
    submitted; the AI review then cross-checks the documents against these
    same fields."""
    if claim_kind != CLAIM_KIND_INSURED:
        return  # flex claims: wallet currency + category checks live elsewhere

    if currency.upper() not in ALLOWED_CURRENCIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{currency}' is not a supported claim currency.",
        )

    profile = claim_profile_for(product_code)
    sub_type = normalize_sub_type(sub_type)

    if profile.sub_types:
        if not sub_type and profile.sub_type_required:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Select the claim sub-type for this hospital & surgical claim.",
            )
        if sub_type and sub_type not in profile.sub_types:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"'{sub_type}' is not a valid sub-type for {product_code} claims.",
            )
    elif sub_type:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{product_code} claims do not take a claim sub-type.",
        )

    if profile.diagnosis_required and not effective_diagnosis(diagnosis):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Select the diagnosis for this claim (choose 'Other' and describe "
            "it if it isn't listed).",
        )

    if profile.requires_referral:
        if referral_document_id is None and not referral_not_applicable:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Specialist claims need a referral letter — attach one, pick a "
                "previously uploaded letter, or mark it not applicable.",
            )
    elif referral_document_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{product_code} claims do not take a referral letter.",
        )

    if referral_document_id is not None:
        doc = db.get(StoredDocument, referral_document_id)
        if (
            doc is None
            or doc.entity_type != DOC_ENTITY_REFERRAL
            or doc.entity_id != employee.id
        ):
            # Same not-403 convention as tenant scoping: someone else's letter
            # simply doesn't exist.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Referral letter not found"
            )
