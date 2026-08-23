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

import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import Claim, Employee, StoredDocument
from app.models.claim import CLAIM_KIND_INSURED
from app.models.stored_document import DOC_ENTITY_REFERRAL, STORAGE_AVAILABLE
from app.services.sg_hospitals import SECTOR_GOVT, SECTOR_PRIVATE, hospital_sector

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

# Stable review/intake scope codes. Display labels are deliberately kept out of
# persisted configuration identities: brokers may relabel a claim choice, and
# old drafts may still carry legacy wording, without orphaning its rules or
# document-type routing.
SCOPE_STANDARD = "standard"
SCOPE_GHS_PRE_POST = "ghs_pre_post"
SCOPE_GHS_PRE_POST_GOVT = "ghs_pre_post_govt"
SCOPE_GHS_PRE_POST_PRIVATE = "ghs_pre_post_private"
SCOPE_GHS_HOSPITALISATION = "ghs_hospitalisation"
SCOPE_GHS_HOSPITALISATION_GOVT = "ghs_hospitalisation_govt"
SCOPE_GHS_HOSPITALISATION_PRIVATE = "ghs_hospitalisation_private"
SCOPE_GHS_EMERGENCY = "ghs_emergency_outpatient"
SCOPE_GHS_EMERGENCY_GOVT = "ghs_emergency_outpatient_govt"
SCOPE_GHS_EMERGENCY_PRIVATE = "ghs_emergency_outpatient_private"
SCOPE_GHS_DIALYSIS_CANCER = "ghs_dialysis_cancer"
SCOPE_GHS_DIALYSIS_CANCER_GOVT = "ghs_dialysis_cancer_govt"
SCOPE_GHS_DIALYSIS_CANCER_PRIVATE = "ghs_dialysis_cancer_private"
SCOPE_GP_TCM = "gp_tcm"
SCOPE_GP_PHYSIOTHERAPY = "gp_physiotherapy"

_SUB_TYPE_SCOPE_CODES: dict[str, str] = {
    GHS_SUB_TYPES[0]: SCOPE_GHS_PRE_POST,
    GHS_SUB_TYPES[1]: SCOPE_GHS_HOSPITALISATION,
    GHS_SUB_TYPES[2]: SCOPE_GHS_EMERGENCY,
    GHS_SUB_TYPES[3]: SCOPE_GHS_DIALYSIS_CANCER,
    SUB_TYPE_TCM: SCOPE_GP_TCM,
    SUB_TYPE_PHYSIO: SCOPE_GP_PHYSIOTHERAPY,
}

_GHS_SECTOR_SCOPE_CODES: dict[tuple[str, str], str] = {
    (SCOPE_GHS_PRE_POST, SECTOR_GOVT): SCOPE_GHS_PRE_POST_GOVT,
    (SCOPE_GHS_PRE_POST, SECTOR_PRIVATE): SCOPE_GHS_PRE_POST_PRIVATE,
    (SCOPE_GHS_HOSPITALISATION, SECTOR_GOVT): SCOPE_GHS_HOSPITALISATION_GOVT,
    (SCOPE_GHS_HOSPITALISATION, SECTOR_PRIVATE): SCOPE_GHS_HOSPITALISATION_PRIVATE,
    (SCOPE_GHS_EMERGENCY, SECTOR_GOVT): SCOPE_GHS_EMERGENCY_GOVT,
    (SCOPE_GHS_EMERGENCY, SECTOR_PRIVATE): SCOPE_GHS_EMERGENCY_PRIVATE,
    (SCOPE_GHS_DIALYSIS_CANCER, SECTOR_GOVT): SCOPE_GHS_DIALYSIS_CANCER_GOVT,
    (SCOPE_GHS_DIALYSIS_CANCER, SECTOR_PRIVATE): SCOPE_GHS_DIALYSIS_CANCER_PRIVATE,
}
_GHS_GENERIC_SCOPE_BY_SECTOR_SCOPE: dict[str, str] = {
    sector_scope: generic_scope
    for (generic_scope, _sector), sector_scope in _GHS_SECTOR_SCOPE_CODES.items()
}
GHS_GENERIC_SCOPE_CODES: frozenset[str] = frozenset(
    generic for generic, _sector in _GHS_SECTOR_SCOPE_CODES
)
GHS_SECTOR_SCOPE_CODES: frozenset[str] = frozenset(
    _GHS_GENERIC_SCOPE_BY_SECTOR_SCOPE
)

CLAIM_SCOPE_CODES: frozenset[str] = frozenset(
    {
        SCOPE_STANDARD,
        *_SUB_TYPE_SCOPE_CODES.values(),
        *GHS_SECTOR_SCOPE_CODES,
    }
)

# Keywords that identify the SOB row funding each GP-rider sub-type.
_SUB_TYPE_ROW_KEYWORDS: dict[str, tuple[str, ...]] = {
    SUB_TYPE_TCM: ("tcm", "traditional chinese", "chinese physician"),
    SUB_TYPE_PHYSIO: ("physio",),
}

CATEGORY_OUTPATIENT = "outpatient"
CATEGORY_INPATIENT = "inpatient"
CATEGORY_OTHER = "other"

# Specialist visit types — a first visit must attach a referral letter; a
# follow-up reuses the member's latest letter on file (auto-linked), and only
# prompts for one when nothing is on file.
VISIT_FIRST = "first"
VISIT_FOLLOW_UP = "follow_up"
VISIT_TYPES: tuple[str, ...] = (VISIT_FIRST, VISIT_FOLLOW_UP)

# Required-document slot keys. Uploads are tagged with the slot they fill
# (`stored_documents.doc_type`); submit blocks until every required slot for
# the claim is filled. The generic invoice/receipt slot is satisfied by ANY
# attached document (tagged or not) so legacy drafts and API clients keep
# working; the specific slots require a matching tag.
DOC_INVOICE_RECEIPT = "invoice_receipt"
DOC_SP_INVOICE = "sp_invoice"
DOC_FINALISED_TAX_INVOICE = "finalised_tax_invoice"
DOC_SUMMARY_TAX_INVOICE = "summary_tax_invoice"
DOC_ITEMISED_TAX_INVOICE = "itemised_tax_invoice"
DOC_DISCHARGE_SUMMARY = "discharge_summary"

DOC_SLOT_LABELS: dict[str, str] = {
    DOC_INVOICE_RECEIPT: "Invoice or receipt",
    DOC_SP_INVOICE: "SP invoice or GRH/private hospital invoice",
    DOC_FINALISED_TAX_INVOICE: "Finalised tax invoice",
    DOC_SUMMARY_TAX_INVOICE: "Summary tax invoice",
    DOC_ITEMISED_TAX_INVOICE: "Itemised tax invoice",
    DOC_DISCHARGE_SUMMARY: "Discharge summary",
}


def normalize_sub_type(sub_type: str | None) -> str | None:
    """Fold a legacy stored sub-type label onto its current name."""
    if not sub_type:
        return sub_type
    return LEGACY_SUB_TYPES.get(sub_type.strip().lower(), sub_type)


# Everything that isn't a letter or a digit. An invoice number is transcribed
# by hand as often as it is autofilled, so "INV-00123", "inv 00123" and
# "INV/00123" are the same bill.
_INVOICE_NOISE_RE = re.compile(r"[^0-9A-Za-z]+")


def normalize_invoice_number(value: str | None) -> str:
    """The comparison key for an invoice/receipt number — the ONE definition of
    when two claims name the same bill.

    Used by the submit-time duplicate check (`services/claims.py`), the review's
    deterministic duplicate rule (`claims_review/rules.py`) and the multi-invoice
    intake split (`claim_intake_suggest._billing_identity`), so those three can
    never disagree about which readings are the same invoice. Empty (no
    alphanumerics at all) means "no number stated" — never a match.
    """
    return _INVOICE_NOISE_RE.sub("", value or "").upper()


def benefit_row_for_sub_type(
    benefit_schedule: dict[str, Any] | None, sub_type: str | None
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
    # Whether members file this benefit as a receipt-reimbursement claim through
    # the portal. False for products settled outside the claim form — Major
    # Medical (top-up that pays after GHS, not filed separately) and the
    # event/lump-sum lines (term life, personal accident, critical illness).
    # These are hidden from the claim-type picker AND rejected at submit.
    member_claimable: bool = True


_HOSPITAL = ClaimIntakeProfile(
    sub_types=GHS_SUB_TYPES,
    diagnosis_group="hospital",
    diagnosis_required=True,
    category=CATEGORY_INPATIENT,
)
# Group Major Medical — same shape as GHS but not member-filed (excluded from
# the claim form; a member claims hospitalisation under GHS only).
_MAJOR_MEDICAL = ClaimIntakeProfile(
    sub_types=GHS_SUB_TYPES,
    diagnosis_group="hospital",
    diagnosis_required=True,
    category=CATEGORY_INPATIENT,
    member_claimable=False,
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
    "GMM": _MAJOR_MEDICAL,
    "GMM2": _MAJOR_MEDICAL,
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

# Unknown / unmapped products (term life, personal accident, critical illness,
# and any product without a claim profile) are not member-filed — hidden from
# the claim form and rejected at submit.
_EMPTY = ClaimIntakeProfile(member_claimable=False)


@dataclass(frozen=True)
class ClaimScopeDefinition:
    """One selectable claim-form scope with a stable machine identity."""

    code: str
    label: str
    sub_type: str | None


def claim_profile_for(product_code: str | None) -> ClaimIntakeProfile:
    return _PROFILES.get((product_code or "").strip().upper(), _EMPTY)


def scope_code_for_sub_type(sub_type: str | None) -> str:
    """Stable scope code for a stored/display sub-type (legacy labels included)."""
    normalized = normalize_sub_type(sub_type)
    if not normalized:
        return SCOPE_STANDARD
    return _SUB_TYPE_SCOPE_CODES.get(normalized, SCOPE_STANDARD)


def sector_scope_code(scope_code: str, sector: str) -> str:
    """The Government/Private leaf for a generic GHS subclaim scope.

    Non-GHS scopes pass through unchanged so callers can apply this at the
    claim/document boundary without duplicating product-profile checks.
    """
    return _GHS_SECTOR_SCOPE_CODES.get((scope_code, sector), scope_code)


def generic_scope_code(scope_code: str) -> str | None:
    """The invisible compatibility parent of a sector-specific GHS leaf."""
    return _GHS_GENERIC_SCOPE_BY_SECTOR_SCOPE.get(scope_code)


def sector_scope_codes(scope_code: str) -> tuple[str, ...]:
    """Government then Private leaf codes for one generic GHS subclaim."""
    govt = _GHS_SECTOR_SCOPE_CODES.get((scope_code, SECTOR_GOVT))
    private = _GHS_SECTOR_SCOPE_CODES.get((scope_code, SECTOR_PRIVATE))
    return (govt, private) if govt and private else ()


def claim_scope_key(
    claim_kind: str | None,
    claim_key: str | None,
    scope_code: str = SCOPE_STANDARD,
) -> str:
    """Canonical identity shared by portal choices and broker settings."""
    kind = (claim_kind or "").strip().casefold()
    key = " ".join(str(claim_key or "").split()).casefold()
    if kind == "flex":
        return f"{kind}:{key}"
    scope = (scope_code or SCOPE_STANDARD).strip().casefold()
    return f"{kind}:{key}:{scope}"


def claim_scope_definitions(
    product_code: str | None,
    base_label: str,
    benefit_schedules: list[dict[str, Any] | None] | None = None,
) -> tuple[ClaimScopeDefinition, ...]:
    """Selectable scopes for a product.

    The member form supplies its one plan schedule. The broker configuration
    surface supplies every schedule for the product in the selected year, so it
    sees the union of claim choices any covered member can actually receive.
    Inpatient sub-types are intrinsic to the profile; optional GP riders appear
    only when at least one supplied schedule funds them.
    """
    profile = claim_profile_for(product_code)
    if not profile.member_claimable:
        return ()
    if profile.category == CATEGORY_INPATIENT:
        return tuple(
            ClaimScopeDefinition(scope_code_for_sub_type(label), label, label)
            for label in profile.sub_types
        )

    scopes = [ClaimScopeDefinition(SCOPE_STANDARD, base_label, None)]
    if not profile.sub_type_required:
        schedules = benefit_schedules or []
        scopes.extend(
            ClaimScopeDefinition(scope_code_for_sub_type(label), label, label)
            for label in profile.sub_types
            if any(benefit_row_for_sub_type(schedule, label) for schedule in schedules)
        )
    return tuple(scopes)


def is_inpatient_product(product_code: str | None) -> bool:
    """Whether a product's claims draw on an inpatient benefit.

    Lives here because this module owns the profiles, and it has three
    consumers now — the claims reports' scope filter, the broker claim payload
    (which tells the assessment form whether to offer sector and admission
    dates) and, through the payload, the form itself. A product-code list in
    any of them would be a place to forget when a product is added, and the
    frontend must not reimplement it: a drifted copy hides the sector field on
    a hospitalisation claim while the report still prints the column.

    Keyed on the PRODUCT, not the sub-type: a pre-/post-hospitalisation consult
    is billed by a specialist clinic but is an inpatient benefit (see
    `_target_settings`), so a sub-type test would file it under outpatient.
    """
    return claim_profile_for(product_code).category == CATEGORY_INPATIENT


# The inpatient sub-type whose documents depend on the hospital sector.
SUB_TYPE_HOSPITALISATION = GHS_SUB_TYPES[1]

# The consult that sits BEFORE or AFTER an admission — billed by the specialist
# clinic, not the hospital, and claimed against the inpatient product rather
# than the outpatient specialist benefit.
SUB_TYPE_PRE_POST = GHS_SUB_TYPES[0]


# The anchor a claim type may name — the visit it CONTINUES. Served on
# `ClaimTypeOption.anchor_mode` and consumed by `services/claim_episodes.py`.
# "admission" = the hospital stay a pre-/post- consult is claimed against;
# "sp_course" = the first visit of a specialist course of treatment.
ANCHOR_ADMISSION = "admission"
ANCHOR_SP_COURSE = "sp_course"
ANCHOR_MODES: tuple[str, ...] = (ANCHOR_ADMISSION, ANCHOR_SP_COURSE)


def is_pre_post_consult(
    product_code: str | None,
    sub_type: str | None,
    *,
    claim_kind: str = CLAIM_KIND_INSURED,
) -> bool:
    """The consult that sits before or after an admission.

    Extracted because two unrelated rules test it — the treating doctor is
    required here and nowhere else, and this is the claim type that names an
    admission — and a second spelling of the test would let one of them drift
    onto a different set of claims than the other.
    """
    if claim_kind != CLAIM_KIND_INSURED:
        return False
    profile = claim_profile_for(product_code)
    return (
        profile.category == CATEGORY_INPATIENT
        and normalize_sub_type(sub_type) == SUB_TYPE_PRE_POST
    )


def requires_doctor_name(
    product_code: str | None,
    sub_type: str | None,
    *,
    claim_kind: str = CLAIM_KIND_INSURED,
) -> bool:
    """Whether the claim must name the treating doctor.

    Only pre-/post-hospitalisation consults do: the insurer ties the consult to
    the admission through the attending doctor, and unlike every other claim
    field that link is not derivable from the bill's amount, date or provider.
    Served to the form on `ClaimTypeOption.requires_doctor_name` rather than
    re-derived in TypeScript — the sub-type label is a string the frontend must
    never have to match on.
    """
    return is_pre_post_consult(product_code, sub_type, claim_kind=claim_kind)


def supports_stay_dates(
    product_code: str | None,
    sub_type: str | None,
    *,
    claim_kind: str = CLAIM_KIND_INSURED,
) -> bool:
    """Whether this claim may record an optional hospital-stay date range.

    Served to both member forms rather than re-derived from the display label
    or from document-slot configuration. The latter happens to identify this
    subtype today, but required documents are broker-editable while the clinical
    meaning of an admission/discharge pair is not. The visit date remains the
    required treatment date because day-surgery documents may state only that.
    """
    if claim_kind != CLAIM_KIND_INSURED:
        return False
    profile = claim_profile_for(product_code)
    return (
        profile.category == CATEGORY_INPATIENT
        and normalize_sub_type(sub_type) == SUB_TYPE_HOSPITALISATION
    )


def anchor_mode_for(
    product_code: str | None,
    sub_type: str | None,
    *,
    claim_kind: str = CLAIM_KIND_INSURED,
) -> str | None:
    """Which earlier visit this claim type may be anchored to, if any.

    SERVED on `ClaimTypeOption.anchor_mode` for the same reason
    `requires_doctor_name` is: the client would otherwise have to match on the
    sub-type LABEL, and a relabel there is silent — the picker would simply stop
    appearing while the server kept accepting the link.

    Note this answers the question for a claim TYPE, so a specialist claim
    reports `sp_course` whichever visit type it carries. Only a follow-up may
    actually name one; that narrowing is `claim_episodes.anchor_mode_for_claim`,
    and on the form it is the `visit_type` control the member has already set.
    """
    if is_pre_post_consult(product_code, sub_type, claim_kind=claim_kind):
        return ANCHOR_ADMISSION
    if claim_kind != CLAIM_KIND_INSURED:
        return None
    if claim_profile_for(product_code).requires_referral:
        return ANCHOR_SP_COURSE
    return None

# Hospitalisation/Day Surgery document sets by hospital sector — also exposed
# through /portal/coverage-options so the form can switch slots as the member
# picks the hospital. Unlisted/overseas hospitals get the private set (the
# stricter default; the broker can waive at review).
HOSPITALISATION_SLOTS_BY_SECTOR: dict[str, list[str]] = {
    "govt": [DOC_FINALISED_TAX_INVOICE],
    "private": [
        DOC_SUMMARY_TAX_INVOICE,
        DOC_ITEMISED_TAX_INVOICE,
        DOC_DISCHARGE_SUMMARY,
    ],
}


def required_doc_slots(
    product_code: str | None,
    sub_type: str | None,
    provider_name: str | None = None,
    *,
    claim_kind: str = CLAIM_KIND_INSURED,
) -> list[str]:
    """Required-document slot keys for a claim (broker-specified 2026-07-21).

    - Outpatient / flex / everything else → invoice or receipt.
    - SP → the SP-or-hospital invoice (the referral letter rides the separate
      referral mechanism, not a document slot).
    - Inpatient Hospitalisation/Day Surgery → by hospital sector: government
      (GRH) needs a Finalised Tax Invoice; private — and unlisted/overseas,
      the stricter default the broker can waive at review — needs Summary +
      Itemised Tax Invoices + Discharge Summary.
    """
    if claim_kind != CLAIM_KIND_INSURED:
        return [DOC_INVOICE_RECEIPT]
    profile = claim_profile_for(product_code)
    if profile.requires_referral:
        return [DOC_SP_INVOICE]
    if (
        profile.category == CATEGORY_INPATIENT
        and normalize_sub_type(sub_type) == SUB_TYPE_HOSPITALISATION
    ):
        sector = hospital_sector(provider_name)
        key = "govt" if sector == SECTOR_GOVT else "private"
        return list(HOSPITALISATION_SLOTS_BY_SECTOR[key])
    return [DOC_INVOICE_RECEIPT]


def assert_documents_satisfy_slots(
    slot_keys: list[str],
    documents: list[StoredDocument],
    labels: dict[str, str] | None = None,
) -> None:
    """Submit-time check that every required slot is filled. The generic
    invoice/receipt slot accepts any attached document (legacy drafts and
    API clients don't tag); the specific slots need a matching `doc_type`."""
    tagged = {d.doc_type for d in documents if d.doc_type}
    missing: list[str] = []
    for key in slot_keys:
        if key == DOC_INVOICE_RECEIPT:
            if not documents:
                missing.append(key)
        elif key not in tagged:
            missing.append(key)
    if missing:
        names = labels or DOC_SLOT_LABELS
        missing_labels = ", ".join(
            names.get(key, key.replace("_", " ")) for key in missing
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Missing required documents: {missing_labels}.",
        )


def person_employee_ids(db: Session, employee: Employee) -> list[str]:
    """Every `Employee` row that is THIS person, across benefit years.

    `Employee` rows are per policy YEAR — a renewal creates a new one — so "the
    same person" is not "the same id", and anything keyed on a single row stops
    resolving the day the year turns over. That is not cosmetic here: a referral
    letter is stamped with the employee id current when it was uploaded, so a
    specialist course begun in November is still the course being followed up in
    February, riding a letter that now belongs to last year's row.

    The binding rule mirrors `member_access.locate_employee`: a row the same
    member account is stamped on, or an UNCLAIMED row carrying the same staff id
    (how a new year's roster arrives, before anyone has signed in against it).
    **A row bound to a DIFFERENT account is never claimed** — a recycled or
    placeholder staff id would otherwise hand one person another's referral
    letters and, through the anchor picker, their diagnoses.
    """
    if not employee.staff_id:
        return [employee.id]
    owned: ColumnElement[bool] = Employee.member_account_id.is_(None)
    if employee.member_account_id:
        owned = or_(owned, Employee.member_account_id == employee.member_account_id)
    ids = list(
        db.execute(
            select(Employee.id).where(
                Employee.client_id == employee.client_id,
                Employee.staff_id == employee.staff_id,
                owned,
            )
        ).scalars()
    )
    if employee.id not in ids:
        ids.append(employee.id)
    return ids


def referral_letters_for(db: Session, employee: Employee) -> list[StoredDocument]:
    """The member's referral letters, newest first — across benefit years.

    Scoped through `person_employee_ids`, not `employee.id`: letters that
    vanished from the picker at renewal would make a member re-upload a letter
    we already hold, and `resolve_sp_referral` would 422 a follow-up whose
    course started in the previous year.
    """
    return list(
        db.execute(
            select(StoredDocument)
            .where(
                StoredDocument.entity_type == DOC_ENTITY_REFERRAL,
                StoredDocument.entity_id.in_(person_employee_ids(db, employee)),
                StoredDocument.storage_state == STORAGE_AVAILABLE,
            )
            .order_by(StoredDocument.created_at.desc())
        ).scalars()
    )


def latest_referral_letter(db: Session, employee: Employee) -> StoredDocument | None:
    letters = referral_letters_for(db, employee)
    return letters[0] if letters else None


def resolve_sp_referral(
    db: Session,
    employee: Employee,
    *,
    visit_type: str | None,
    referral_document_id: str | None,
    anchor: Claim | None = None,
) -> str | None:
    """The referral letter a specialist claim rides on. First visits must name
    one; follow-ups reuse the letter of the visit they CONTINUE, falling back to
    the latest on file, and only 422 when the system can't track any.

    ``anchor`` is the first visit of this course (`services/claim_episodes.py`).
    It takes precedence over the newest letter because "newest" is wrong the
    moment a member is under two specialists at once: a cardiology referral
    uploaded last week would be attached to this month's orthopaedic follow-up,
    silently — and the review's `_check_referral` passes it, because a letter IS
    attached. The fallback stays for a follow-up with no anchor chosen, which is
    every claim filed before episodes existed.
    """
    if visit_type not in VISIT_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Specialist claims must state whether this is a first visit or a "
            "follow-up visit.",
        )
    if referral_document_id:
        return referral_document_id
    if visit_type == VISIT_FOLLOW_UP:
        if anchor is not None and anchor.referral_document_id:
            return anchor.referral_document_id
        letter = latest_referral_letter(db, employee)
        if letter is not None:
            return letter.id
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "We couldn't find a referral letter on file for your follow-up "
            "visit — attach the referral letter for this treatment.",
        )
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Specialist first visits need a referral letter — attach one or pick "
        "a previously uploaded letter.",
    )


def assert_doctor_name_valid(
    product_code: str | None,
    sub_type: str | None,
    doctor_name: str | None,
    *,
    claim_kind: str = CLAIM_KIND_INSURED,
) -> None:
    """Pre-/post-hospitalisation claims must name the treating doctor.

    Its OWN function, and not just a clause of `assert_intake_valid`, because
    it is the one intake rule whose enforcement point is narrower than "create
    and submit": the doctor can only be entered on the claim FORM, so a
    `needs_info` resubmission — where the member's only control is attaching
    documents — must not be refused for want of it. See `submit_claim`.

    Deliberately NOT rejected on the other claim types the way `visit_type` is:
    this is a free-text fact about the visit, not a mode switch, so a client
    that sends one elsewhere is harmless — whereas an unexpected referral or
    visit type changes which rules apply.
    """
    if requires_doctor_name(
        product_code, sub_type, claim_kind=claim_kind
    ) and not (doctor_name or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Name the doctor you saw — a pre- or post-hospitalisation "
            "follow-up is claimed against your hospital admission, and the "
            "doctor's name is how it's matched to it.",
        )


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
    visit_type: str | None = None,
) -> None:
    """Profile-driven form validation. 422 with a member-readable message so
    the wrong claim type / missing sub-type / missing referral can't be
    submitted; the AI review then cross-checks the documents against these
    same fields."""
    if claim_kind != CLAIM_KIND_INSURED:
        return  # flex claims: wallet currency + category checks live elsewhere

    if currency.upper() not in ALLOWED_CURRENCIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"'{currency}' is not a supported claim currency.",
        )

    profile = claim_profile_for(product_code)
    sub_type = normalize_sub_type(sub_type)

    if profile.sub_types:
        if not sub_type and profile.sub_type_required:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Select the claim sub-type for this hospital & surgical claim.",
            )
        if sub_type and sub_type not in profile.sub_types:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"'{sub_type}' is not a valid sub-type for {product_code} claims.",
            )
    elif sub_type:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{product_code} claims do not take a claim sub-type.",
        )

    if profile.diagnosis_required and not effective_diagnosis(diagnosis):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Select the diagnosis for this claim (choose 'Other' and describe "
            "it if it isn't listed).",
        )

    # NOTE: the doctor-name rule is deliberately NOT here — it has a narrower
    # enforcement point than the rest. See `assert_doctor_name_valid`.

    if profile.requires_referral:
        # 2026-07-21: the "not applicable" declaration was removed for SP —
        # the visit type decides instead. First visits must name a letter;
        # follow-ups auto-link the latest on file (resolve_sp_referral runs
        # before this validation, so a valid claim arrives with one set).
        if visit_type not in VISIT_TYPES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Specialist claims must state whether this is a first visit "
                "or a follow-up visit.",
            )
        if referral_document_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Specialist claims need a referral letter — attach one or "
                "pick a previously uploaded letter.",
            )
    else:
        if referral_document_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{product_code} claims do not take a referral letter.",
            )
        if visit_type is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{product_code} claims do not take a visit type.",
            )

    if referral_document_id is not None:
        doc = db.get(StoredDocument, referral_document_id)
        if (
            doc is None
            or doc.entity_type != DOC_ENTITY_REFERRAL
            or doc.storage_state != STORAGE_AVAILABLE
            # The PERSON, not this year's row (`person_employee_ids`). A course
            # begun before the renewal carries a letter stamped with the old
            # employee id, and the anchor picker offers that course across the
            # year boundary on purpose — checking a single row here would refuse
            # the letter the member was just told to use, naming a document they
            # never chose and cannot fix.
            or doc.entity_id not in person_employee_ids(db, employee)
        ):
            # Same not-403 convention as tenant scoping: someone else's letter
            # simply doesn't exist.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Referral letter not found"
            )
