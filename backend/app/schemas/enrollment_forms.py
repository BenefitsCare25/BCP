"""Online enrolment e-form — setup, member form context, sign-and-submit and
the submission register shared by the broker, HR and member surfaces."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enrollment import EnrollmentElectionIn, LeaveElectionIn

ClauseScope = Literal["all", "dependants"]
FormSource = Literal["portal", "paper"]
FormStatus = Literal["submitted", "acknowledged", "superseded"]

_ID_PATTERN = r"^[a-z0-9_-]{1,40}$"


# ── Form setup (broker) ───────────────────────────────────────────────────────


class FormClause(BaseModel):
    """One declaration the member must tick before signing."""

    id: str = Field(pattern=_ID_PATTERN)
    text: str = Field(min_length=3, max_length=1500)
    # "dependants" clauses only appear when the member names a family member
    # (the third-party data consent, the pre-existing exclusion).
    applies_to: ClauseScope = "all"


class FormDocument(BaseModel):
    """A document the member is asked to read — a link, or a file the broker
    uploaded (``document_id``)."""

    id: str = Field(pattern=_ID_PATTERN)
    label: str = Field(min_length=1, max_length=160)
    url: str | None = Field(default=None, max_length=1000)
    document_id: str | None = Field(default=None, max_length=36)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not value.lower().startswith("https://"):
            raise ValueError("Document links must start with https://")
        return value


class FormContribution(BaseModel):
    """What share of the premium the EMPLOYEE pays for a product, in percent.
    None = this part is not shown to members (the company pays it, or the
    broker has not set it). Premiums are only ever shown as the member's share."""

    employee_pct: float | None = Field(default=None, ge=0, le=100)
    dependant_pct: float | None = Field(default=None, ge=0, le=100)


class FormRule(BaseModel):
    """Anyone covered on ``product_code`` must also be covered on
    ``requires_product_code`` — "EMM must be selected together with GHS"."""

    product_code: str = Field(min_length=1, max_length=64)
    requires_product_code: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _distinct(self) -> FormRule:
        if self.product_code == self.requires_product_code:
            raise ValueError("A product cannot require itself.")
        return self


class FormSettings(BaseModel):
    title: str = Field(default="Benefits enrolment form", min_length=3, max_length=160)
    # Replaces the generated "you are automatically covered for…" notice.
    intro: str | None = Field(default=None, max_length=3000)
    submission_note: str | None = Field(default=None, max_length=1000)
    helpline: str | None = Field(default=None, max_length=300)
    eligibility_notes: list[str] = Field(default_factory=list, max_length=20)
    clauses: list[FormClause] = Field(default_factory=list, max_length=20)
    documents: list[FormDocument] = Field(default_factory=list, max_length=20)
    # Keyed by product CODE (stable across slip re-parses, unlike product ids).
    contributions: dict[str, FormContribution] = Field(default_factory=dict)
    rules: list[FormRule] = Field(default_factory=list, max_length=20)

    @field_validator("eligibility_notes")
    @classmethod
    def _notes(cls, value: list[str]) -> list[str]:
        cleaned = [n.strip() for n in value if n and n.strip()]
        if any(len(n) > 500 for n in cleaned):
            raise ValueError("Each note must be 500 characters or fewer.")
        return cleaned

    @model_validator(mode="after")
    def _unique_ids(self) -> FormSettings:
        for label, items in (("clause", self.clauses), ("document", self.documents)):
            ids = [i.id for i in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Each {label} needs a unique id.")
        return self


class FormProductOut(BaseModel):
    """A product of the benefit year, for the setup screen's contribution grid."""

    product_code: str
    product_name: str | None
    participation: str | None  # compulsory | voluntary | mixed
    has_dependant_cover: bool
    # How family cover is taken on this product: compulsory | voluntary | mixed.
    dependant_participation: str | None = None


class FormConfigOut(BaseModel):
    window_id: str
    is_default: bool  # True until a broker saves — the settings are generated
    settings: FormSettings
    products: list[FormProductOut]
    age_limits: dict[str, dict[str, int]]
    updated_at: datetime | None = None


# ── Member form context ──────────────────────────────────────────────────────


class ParticularsOut(BaseModel):
    name: str | None
    staff_id: str
    id_masked: str
    gender: str | None
    dob: str | None
    job_grade: str | None
    date_of_hire: str | None
    email: str | None
    contact_no: str | None


class FormDependantOut(BaseModel):
    id: str
    name: str | None
    relationship: str | None
    role: str | None  # spouse | child | None
    gender: str | None
    id_masked: str
    dob: str | None
    age_next_birthday: int | None
    occupation: str | None
    status: str  # active | pending
    eligible: bool
    eligibility_note: str | None = None
    # Products whose OWN age window (benefit settings may differ per product)
    # this person falls outside — they cannot be covered on these.
    ineligible_products: list[str] = Field(default_factory=list)


class ContributionTierOut(BaseModel):
    """One plan's premium table, as the paper form printed it.

    ``premium`` is the FULL annual premium per family composition (EO/ES/EC/EF
    for a tiered rate table; EO alone for a flat or sum-insured plan, with
    ``premium_per_dependant`` for a flat per-head table). The member's SHARE is
    ``employee`` (their own cover) plus ``family[role]`` (tiered) or
    ``per_dependant`` x count (flat). None = not shown."""

    tier_key: str
    mode: Literal["tiered", "flat"]
    premium: dict[str, float] = Field(default_factory=dict)
    premium_per_dependant: float | None = None
    employee: float | None = None
    family: dict[str, float] = Field(default_factory=dict)  # spouse | child | both
    per_dependant: float | None = None


class PlanFactOut(BaseModel):
    """What one electable plan gives, in the paper form's one line: the key
    benefit (room & board for hospital cover) and the sum insured."""

    product_code: str
    tier_key: str
    label: str
    highlight: str | None = None
    sum_insured: float | None = None
    insurer: str | None = None


class ProductContributionOut(BaseModel):
    product_code: str
    employee_pct: float | None
    dependant_pct: float | None
    gst_included: bool
    tiers: list[ContributionTierOut]


class CompulsoryLineOut(BaseModel):
    product_code: str
    product_name: str | None
    plan_label: str | None


class FormDocumentOut(BaseModel):
    id: str
    label: str
    url: str | None
    document_id: str | None
    file_name: str | None = None


class FormRuleOut(BaseModel):
    product_code: str
    product_name: str | None
    requires_product_code: str
    requires_product_name: str | None


class FormSubmissionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reference_no: str
    version: int
    source: FormSource
    status: FormStatus
    submitted_at: datetime
    signature_name: str | None
    acknowledged_at: datetime | None
    has_pdf: bool
    enrollment_status: str | None = None


class MemberFormContextOut(BaseModel):
    company_name: str
    title: str
    policy_start: date
    policy_end: date
    closes_at: datetime
    window_type: str
    intro_lines: list[str]
    submission_note: str | None
    helpline: str | None
    eligibility_notes: list[str]
    clauses: list[FormClause]
    documents: list[FormDocumentOut]
    rules: list[FormRuleOut]
    particulars: ParticularsOut
    compulsory: list[CompulsoryLineOut]
    contributions: list[ProductContributionOut]
    plans: list[PlanFactOut] = Field(default_factory=list)
    # Flex members: the allowance their price tags draw from (None = no flex).
    flex_wallet: float | None = None
    flex_currency: str | None = None
    flex_proration_note: str | None = None
    dependants: list[FormDependantOut]
    latest: FormSubmissionSummary | None = None


# ── Sign and submit ──────────────────────────────────────────────────────────


class ParticularsIn(BaseModel):
    contact_no: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("contact_no")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not all(ch.isdigit() or ch in " +-()" for ch in value):
            raise ValueError("Contact number may contain digits, spaces, + - ( ) only.")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        local, _, domain = value.partition("@")
        if not local or "." not in domain or " " in value:
            raise ValueError("Enter a valid email address.")
        return value


class PendingDependantRequest(BaseModel):
    """A family member added during this enrolment (awaiting verification) and
    the products the member asks them to be covered on."""

    dependant_id: str = Field(min_length=1, max_length=36)
    product_codes: list[str] = Field(min_length=1, max_length=20)


class FormSignIn(BaseModel):
    elections: list[EnrollmentElectionIn] | None = Field(default=None, min_length=1)
    leave: LeaveElectionIn | None = None
    particulars: ParticularsIn = Field(default_factory=ParticularsIn)
    pending_requests: list[PendingDependantRequest] = Field(default_factory=list, max_length=20)
    accepted_clause_ids: list[str] = Field(default_factory=list, max_length=40)
    signature_name: str = Field(min_length=2, max_length=255)
    confirm: bool

    @field_validator("signature_name")
    @classmethod
    def _signature(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Type your full name to sign.")
        return cleaned

    @model_validator(mode="after")
    def _confirmed(self) -> FormSignIn:
        if not self.confirm:
            raise ValueError("Confirm the declaration to sign the form.")
        return self


# ── Register (broker / HR) ───────────────────────────────────────────────────


class FormRegisterItem(BaseModel):
    id: str
    reference_no: str
    version: int
    source: FormSource
    status: FormStatus
    employee_id: str
    staff_id: str | None
    employee_name: str | None
    id_masked: str
    window_id: str | None
    window_name: str | None
    submitted_at: datetime
    signature_name: str | None
    acknowledged_at: datetime | None
    enrollment_status: str | None
    changes: int
    has_pdf: bool


class FormRegisterOut(BaseModel):
    items: list[FormRegisterItem]
    total: int
    offset: int
    limit: int
    counts: dict[str, int]


class FormAcknowledgeIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
