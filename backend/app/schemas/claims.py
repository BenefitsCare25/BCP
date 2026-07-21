"""Pydantic schemas for the claims module (portal member + broker surfaces)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Documents ─────────────────────────────────────────────────────────────────


class StoredDocumentOut(_Base):
    id: str
    file_name: str
    mime_type: str | None = None
    size_bytes: int
    sha256: str
    created_at: datetime


# ── Claims ────────────────────────────────────────────────────────────────────


class ClaimCreateIn(BaseModel):
    claim_kind: str = Field(pattern="^(insured|flex)$")
    # insured claims (the Benefit/SOB-item picker was removed from the form —
    # benefit attribution is the broker's call at review time)
    product_code: str | None = Field(default=None, max_length=64)
    # flex claims
    flex_category_name: str | None = Field(default=None, max_length=255)
    claim_type: str = Field(min_length=1, max_length=64)
    # Required for GHS-family products; validated against the intake profile.
    sub_type: str | None = Field(default=None, max_length=64)
    incurred_date: date
    provider_name: str = Field(min_length=2, max_length=255)
    invoice_number: str = Field(min_length=1, max_length=128)
    diagnosis: str | None = Field(default=None, max_length=512)
    remarks: str | None = Field(default=None, max_length=500)
    amount_claimed: float = Field(gt=0, le=1_000_000)
    currency: str = Field(default="SGD", min_length=3, max_length=8)
    dependant_id: str | None = None
    # Specialist claims: a previously uploaded referral letter, or an explicit
    # "not applicable" declaration (recorded for the broker + AI review).
    referral_document_id: str | None = None
    referral_not_applicable: bool = False


class ClaimOut(_Base):
    id: str
    claim_kind: str
    product_code: str | None = None
    benefit_key: str | None = None
    flex_category_name: str | None = None
    claim_type: str
    sub_type: str | None = None
    incurred_date: date
    provider_name: str | None = None
    invoice_number: str | None = None
    diagnosis: str | None = None
    remarks: str | None = None
    referral_document_id: str | None = None
    referral_document: StoredDocumentOut | None = None
    # Mirrors the form declaration (from form_fields) for display.
    referral_not_applicable: bool = False
    amount_claimed: float
    currency: str
    amount_converted: float | None = None
    amount_approved: float | None = None
    status: str
    dependant_id: str | None = None
    # Resolved claimant display name when the claim is for a dependant.
    dependant_name: str | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    decision_notes: str | None = None
    created_at: datetime
    documents: list[StoredDocumentOut] = Field(default_factory=list)


class ClaimList(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ClaimOut] = Field(default_factory=list)


# ── AI review (broker-only — members never see fraud signals) ─────────────────


class ClaimAIReviewSummary(_Base):
    id: str
    status: str  # pending | complete | error
    verdict: str | None = None  # clean | flagged
    confidence: float | None = None
    summary: str | None = None
    created_at: datetime


class ClaimAIReviewOut(ClaimAIReviewSummary):
    extractions: list[dict] | None = None
    field_comparisons: list[dict] | None = None
    rule_results: list[dict] | None = None
    vision_checks: list[dict] | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_estimate_usd: float | None = None
    error_detail: str | None = None
    superseded: bool = False


# Broker view carries the claimant context the member view doesn't need.
class BrokerClaimOut(ClaimOut):
    client_id: str
    policy_year_id: str
    employee_id: str
    staff_id: str | None = None
    employee_name: str | None = None
    ai_review: ClaimAIReviewSummary | None = None
    # Remaining amount in the claim's tightest utilization bucket (see
    # `remaining_for_claim`); None = no numeric limit known. Computed on the
    # single-claim detail only — the list stays cheap.
    remaining_limit: float | None = None


class BrokerClaimList(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[BrokerClaimOut] = Field(default_factory=list)


class ClaimDecisionIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|needs_info)$")
    note: str | None = Field(default=None, max_length=2000)
    approved_amount: float | None = Field(default=None, gt=0)
    # Approving beyond the bucket's remaining limit 409s (`limit_exceeded`)
    # unless the broker explicitly acknowledges the overrun.
    acknowledge: bool = False


# ── Utilization (computed-on-read; see services/utilization.py) ───────────────


class UtilizationBucket(BaseModel):
    product_code: str | None = None
    product_name: str | None = None
    benefit_key: str | None = None  # None = product-level roll-up
    limit: float | None = None  # parsed numeric annual limit, when known
    limit_display: str | None = None  # verbatim limit text for the UI
    approved: float = 0.0
    pending: float = 0.0  # in-flight claims — shown separately, never subtracted
    remaining: float | None = None  # limit - approved
    claim_count: int = 0
    # Claims against coverage no longer on the statement (coverage changed
    # after submission).
    orphaned: bool = False
    # A limit text exists (with digits) but no annual limit could be derived
    # (per-unit/percent value or parse failure) — the over-limit approve guard
    # is INACTIVE for this bucket, which is not the same as "unlimited".
    limit_unparsed: bool = False


class FlexCategoryUtilization(BaseModel):
    name: str
    sub_limit: float | None = None
    approved: float = 0.0
    pending: float = 0.0
    remaining: float | None = None


class FlexUtilization(BaseModel):
    currency: str | None = None
    wallet_amount: float | None = None
    price_tags_total: float | None = None
    flex_balance: float | None = None  # wallet - enrollment price-tags
    approved: float = 0.0  # approved flex claims
    pending: float = 0.0
    available: float | None = None  # flex_balance - approved
    categories: list[FlexCategoryUtilization] = Field(default_factory=list)


class UtilizationOut(BaseModel):
    policy_year_id: str
    insured: list[UtilizationBucket] = Field(default_factory=list)
    flex: FlexUtilization | None = None


# ── Coverage options (drives the member claim-form picker) ────────────────────


# One entry in the claim-type dropdown. The sub-type is folded into the
# selection (inpatient setting / GP rider), never a second picker.
class ClaimTypeOption(BaseModel):
    label: str
    sub_type: str | None = None


class InsuredClaimOption(BaseModel):
    product_code: str
    product_name: str | None = None
    plan_code: str | None = None
    annual_policy_limit: str | None = None
    covers_dependants: bool = False
    covered_dependant_ids: list[str] = Field(default_factory=list)
    # Claim-intake profile (claim_intake.py) — drives the conditional form
    # fields: SP referral requirement, diagnosis search.
    sub_types: list[str] = Field(default_factory=list)
    requires_referral: bool = False
    diagnosis_group: str | None = None
    diagnosis_required: bool = False
    # Outpatient / Inpatient / other grouping + the dropdown entries this
    # product contributes (plan-aware: GP riders appear only when the member's
    # schedule carries a matching row).
    category: str = "other"
    claim_types: list[ClaimTypeOption] = Field(default_factory=list)


class FlexClaimCategoryOption(BaseModel):
    name: str
    sub_limit: float | None = None
    note: str | None = None


class FlexClaimOptions(BaseModel):
    currency: str | None = None
    wallet_amount: float | None = None
    flex_balance: float | None = None
    categories: list[FlexClaimCategoryOption] = Field(default_factory=list)


class CoverageOptionsOut(BaseModel):
    policy_year_start: str
    policy_year_end: str
    insured: list[InsuredClaimOption] = Field(default_factory=list)
    flex: FlexClaimOptions | None = None
    dependants: list[dict] = Field(default_factory=list)  # {id, name, relationship}
    # Single source of truth for the currency picker (claim_intake.py).
    currencies: list[str] = Field(default_factory=list)


class DiagnosisOut(BaseModel):
    label: str
    icd10: str | None = None


class DiagnosisSearchOut(BaseModel):
    group: str | None = None
    items: list[DiagnosisOut] = Field(default_factory=list)


# ── Dependant self-add (portal) ───────────────────────────────────────────────


class PortalDependantCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    relationship: str = Field(min_length=1, max_length=64)
    dob: date | None = None
    gender: str | None = Field(default=None, max_length=16)
    id_no: str | None = Field(default=None, max_length=64)


class DependantApprovalIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=2000)
