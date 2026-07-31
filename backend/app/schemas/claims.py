"""Pydantic schemas for the claims module (portal member + broker surfaces)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Documents ─────────────────────────────────────────────────────────────────


# A required-document upload slot on the claim form / claim detail.
class DocSlotOut(BaseModel):
    key: str
    label: str


class StoredDocumentOut(_Base):
    id: str
    file_name: str
    # Which required-document slot this upload fills (claim_intake.DOC_SLOT
    # keys); None = untagged/additional document.
    doc_type: str | None = None
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
    # Specialist claims: "first" | "follow_up" (drives the referral rule).
    visit_type: str | None = Field(default=None, max_length=16)
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
    visit_type: str | None = None
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
    # Required-document slots this claim must fill at submit (resolved from the
    # claim's own product/sub-type/hospital) — drives the tagged-upload UI on
    # the draft/needs_info detail page so resubmission can satisfy every slot.
    required_doc_slots: list[DocSlotOut] = Field(default_factory=list)


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


class HospitalOut(BaseModel):
    name: str
    sector: str  # govt | private


# One entry in the claim-type dropdown. The sub-type is folded into the
# selection (inpatient setting / GP rider), never a second picker.
class ClaimTypeOption(BaseModel):
    label: str
    sub_type: str | None = None
    # Required-document slots for this claim type. When the requirement
    # depends on the hospital (Hospitalisation/Day Surgery), `doc_slots` is
    # the unlisted-hospital default and `doc_slots_by_sector` carries the
    # govt/private sets keyed by `HospitalOut.sector`.
    doc_slots: list[DocSlotOut] = Field(default_factory=list)
    doc_slots_by_sector: dict[str, list[DocSlotOut]] | None = None


class InsuredClaimOption(BaseModel):
    product_code: str
    product_name: str | None = None
    plan_code: str | None = None
    annual_policy_limit: str | None = None
    covers_dependants: bool = False
    covered_dependant_ids: list[str] = Field(default_factory=list)
    # Insurer + the member's ID with that insurer (from the roster
    # `insurer_member_ids` map). Display-only: the ID shown on the claim form
    # keys off the selected claim type's product/insurer. Blank when the roster
    # carries no ID for this insurer.
    insurer: str | None = None
    insurer_member_id: str | None = None
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
    # Required-document slots for flex claims (the generic invoice/receipt).
    doc_slots: list[DocSlotOut] = Field(default_factory=list)


class CoverageOptionsOut(BaseModel):
    policy_year_start: str
    policy_year_end: str
    insured: list[InsuredClaimOption] = Field(default_factory=list)
    flex: FlexClaimOptions | None = None
    dependants: list[dict] = Field(default_factory=list)  # {id, name, relationship}
    # Single source of truth for the currency picker (claim_intake.py).
    currencies: list[str] = Field(default_factory=list)
    # Hospital picker for inpatient claims (sg_hospitals.py) — sector drives
    # the document requirements.
    hospitals: list[HospitalOut] = Field(default_factory=list)


# ── Claim intake autofill (document-driven prefill) ───────────────────────────


class IntakeClaimant(BaseModel):
    kind: str  # "self" | "dependant"
    dependant_id: str | None = None
    name: str | None = None
    confidence: float = 0.0


class IntakeFields(BaseModel):
    provider_name: str | None = None
    incurred_date: str | None = None  # ISO date
    invoice_number: str | None = None
    amount: float | None = None
    currency: str | None = None
    diagnosis: str | None = None


class IntakeDocument(BaseModel):
    """One uploaded document in an autofill set, with its recognised type and
    the required-document slot it fills (when unambiguous)."""

    file_name: str
    # 0-based position in the ORIGINAL upload — the form joins its File objects
    # to these documents on this (robust to duplicate file names, and to the
    # endpoint skipping an unreadable file mid-set).
    upload_index: int = 0
    detected_doc_type: str | None = None
    doc_slot: str | None = None
    # Multi-claim uploads: when the set carries several DISTINCT invoices (one
    # visit each), every invoice document anchors its own claim — this is its
    # 0-based order (0 = the claim prefilled now). None = supporting document.
    claim_index: int | None = None
    # A LATER anchor's OWN reading, used to prefill its claim when the form
    # advances to it. None for the first claim + supporting docs (they prefill
    # from the top-level merged suggestion, never from here).
    fields: IntakeFields | None = None
    # Field names of `fields` the AI was unsure about (only set alongside
    # `fields`) — the form flags them when it advances to this claim.
    low_confidence: list[str] = Field(default_factory=list)


class ClaimIntakeSuggestionOut(BaseModel):
    """What the AI read off an uploaded receipt, mapped to claim-form fields.
    Every value is a SUGGESTION — the member confirms/edits before submit."""
    # False when extraction is unavailable (no AI provider / budget / breaker /
    # parse fault) — the form stays fully manual.
    available: bool = True
    reason: str | None = None
    document_type: str | None = None
    # Broker-recognised document type (claim_doc_types registry display name,
    # e.g. "Discharge Summary", "Tax Invoice (Finalised)") when identified —
    # mirrors the primary document (first that fills a slot).
    detected_doc_type: str | None = None
    # Required-document slot key the primary upload fills, when unambiguous.
    doc_slot: str | None = None
    # Per-document classification for the whole uploaded set (up to 3) — the
    # form drops each file into the slot it fills.
    documents: list[IntakeDocument] = Field(default_factory=list)
    # True when the set carries ≥2 DISTINCT invoices — separate visits that
    # need one claim each. The top-level fields prefill the FIRST invoice's
    # claim; the rest ride on `documents[].claim_index`/`fields`.
    multi_claim: bool = False
    # Preselected claimant (self / a dependant), when a patient name matched.
    claimant: IntakeClaimant | None = None
    # Encoded claim-type selection (`insured:<code>:<idx>` / `flex:<name>`) when
    # the setting maps unambiguously to ONE of the member's claim types.
    claim_selection: str | None = None
    # Plausible claim-type selections when the setting is ambiguous — the member
    # picks. Empty when we have no signal at all.
    claim_candidates: list[str] = Field(default_factory=list)
    fields: IntakeFields = Field(default_factory=IntakeFields)
    # Field names (of `fields`) the model was unsure about — the UI flags them.
    low_confidence: list[str] = Field(default_factory=list)


# ── Claim document types (broker-configurable registry) ───────────────────────


class ClaimDocKeyField(BaseModel):
    """One completeness-check field. ``keywords`` are the label-match tokens;
    empty → the field matches on its own name. ``optional`` fields are checked
    but their absence is never a completeness warning (e.g. Surgery on a
    non-surgical discharge summary)."""

    name: str = Field(min_length=1, max_length=64)
    keywords: list[str] = Field(default_factory=list, max_length=16)
    optional: bool = False


class ClaimDocTypeIn(BaseModel):
    display: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    key_fields: list[ClaimDocKeyField] = Field(default_factory=list, max_length=32)
    sector: str | None = Field(default=None, pattern="^(govt|private)$")
    slot_key: str | None = Field(default=None, max_length=64)


class ClaimDocTypeOut(BaseModel):
    id: str
    key: str
    display: str
    aliases: list[str] = Field(default_factory=list)
    key_fields: list[ClaimDocKeyField] = Field(default_factory=list)
    sector: str | None = None
    slot_key: str | None = None
    # True for a row seeded from the in-code defaults (key match) — the UI
    # labels these; they're still fully editable.
    is_default: bool = False


class DiagnosisOut(BaseModel):
    label: str
    icd10: str | None = None


# ── Claim review rule setup (per claim type, broker-configurable) ─────────────


class ReviewFieldMapModel(BaseModel):
    """One claim-form ↔ document field pair the AI review compares."""

    portal_field: str = Field(min_length=1, max_length=64)
    document_field: str = Field(min_length=1, max_length=128)
    mode: Literal["fuzzy", "exact", "numeric"] = "fuzzy"
    tolerance: float | None = Field(default=None, ge=0)
    # Spend an extra AI vision pass on this field when the text comparison
    # disagrees. Purely a cost/accuracy control.
    verify_with_vision: bool = False
    # Flag the claim when it states this field but NO document substantiates
    # it. Independent of the vision flag on purpose — turning off a vision
    # re-check must never switch off the unsubstantiated-value guard.
    require_evidence: bool = False


class ReviewAIRuleModel(BaseModel):
    """One AI-judged business rule. Only a CRITICAL failure can flag the
    claim; warning/info failures surface to the broker without auto-flagging."""

    id: str | None = Field(default=None, max_length=64)
    rule: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="general", min_length=1, max_length=64)
    severity: Literal["critical", "warning", "info"] = "critical"


class ClaimReviewConfigIn(BaseModel):
    claim_kind: Literal["insured", "flex"]
    # Product code (insured) or flex benefit-category name (flex).
    claim_key: str = Field(min_length=1, max_length=128)
    display_label: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    # The caps bound review-prompt growth.
    field_maps: list[ReviewFieldMapModel] = Field(min_length=1, max_length=30)
    ai_rules: list[ReviewAIRuleModel] = Field(default_factory=list, max_length=60)
    # Extra document families required ON TOP of the automatic derivation.
    required_documents: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("claim_key", "display_label")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # min_length alone accepts "   ", which the API then normalizes to ""
        # — a row that would be committed and thereafter fail to serialize.
        # Normalize here so what validates is what gets stored.
        cleaned = " ".join(v.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ClaimReviewConfigOut(BaseModel):
    """Deliberately NOT a subclass of ``ClaimReviewConfigIn``.

    Reading must never fail: a legacy or hand-edited row that violates a
    write-side constraint has to stay listable (and therefore deletable),
    not 500 the whole company's config surface. The service's defensive
    reader (`claim_review_configs.config_from_row`) already drops malformed
    entries and bounds sizes, so the values here are sane by construction.
    """

    id: str
    claim_kind: str
    claim_key: str
    display_label: str
    enabled: bool = True
    field_maps: list[ReviewFieldMapModel] = Field(default_factory=list)
    ai_rules: list[ReviewAIRuleModel] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)


class ReviewDefaultConfigOut(BaseModel):
    """The in-code default setup — prefills the editor when a claim type is
    first customized."""

    field_maps: list[ReviewFieldMapModel] = Field(default_factory=list)
    ai_rules: list[ReviewAIRuleModel] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)


class ReviewClaimTypeOut(BaseModel):
    """One claim type of the company (the config scope vocabulary)."""

    claim_kind: Literal["insured", "flex"]
    claim_key: str
    display_label: str
    sub_types: list[str] = Field(default_factory=list)


class ReviewScopeOptionsOut(BaseModel):
    claim_types: list[ReviewClaimTypeOut] = Field(default_factory=list)
    default_config: ReviewDefaultConfigOut


class ReviewPromptPreviewOut(BaseModel):
    prompt: str


class SourceReviewConfigOut(BaseModel):
    """A configured claim type of ANOTHER company, offered for import."""

    id: str
    claim_kind: str
    claim_key: str
    display_label: str
    enabled: bool
    field_map_count: int
    rule_count: int
    required_document_count: int


class ImportSourceCompanyOut(BaseModel):
    """A company whose rule setup may be imported from (same broker firm)."""

    id: str
    name: str
    configured_count: int


class ImportReviewConfigsIn(BaseModel):
    source_client_id: str = Field(min_length=1, max_length=36)
    config_ids: list[str] = Field(min_length=1, max_length=50)


class ImportReviewConfigsOut(BaseModel):
    imported: list[ClaimReviewConfigOut] = Field(default_factory=list)


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
