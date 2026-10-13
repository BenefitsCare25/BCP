"""API shapes for importing a company's own Employee Listing."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.adc import AdcPreview


class ListingColumnOut(BaseModel):
    letter: str
    header: str
    role: str


class ListingBlockOut(BaseModel):
    index: int
    kind: str
    banner: str
    code_hint: str | None
    columns: list[ListingColumnOut]


class ListingLayoutOut(BaseModel):
    sheet: str
    header_row: int  # 1-based
    reference_date: date | None
    blocks: list[ListingBlockOut]
    trailing: list[ListingColumnOut]


class ListingProductOut(BaseModel):
    code: str
    display_name: str


class ListingCategoryOption(BaseModel):
    product_code: str
    category_id: str
    category_label: str
    plan_code: str | None


class ListingLabelOut(BaseModel):
    block: int
    key: str
    label: str
    employees: int
    dependants: int
    plans: list[str]
    # Dependants covered through their employee's family tier where the
    # listing's dependant rows state no cover.
    tier_dependants: int = 0
    choice: ListingCategoryOption | None
    not_covered: bool
    confidence: float
    source: str


class ListingCheck(BaseModel):
    code: str
    message: str
    count: int
    rows: list[int] = Field(default_factory=list)


class ListingNoticeOut(BaseModel):
    """A file-level finding (wrong period, ages at another date)."""

    code: str
    tone: str  # "warn" | "info"
    message: str


class ListingPeopleOut(BaseModel):
    """Who the file lists, before it is compared with the roster."""

    employees: int
    dependants: int
    spouses: int
    children: int


class ListingEntityOut(BaseModel):
    """One Entity spelling on the listing and the slip entity it resolves to."""

    spelling: str
    employees: int
    status: str  # "matched" | "aliased" | "unmatched"
    entity: str | None = None
    # For an unmatched spelling: the slip entity it most likely means.
    suggestion: str | None = None


class ListingIssueOut(BaseModel):
    row: int | None
    field: str | None
    code: str
    message: str


class JoinerRuleOut(BaseModel):
    category_id: str
    category_label: str
    rule: str
    listed: int
    exceptions: int


class JoinerProductOut(BaseModel):
    product_code: str
    attributes: list[str]
    rules: list[JoinerRuleOut]
    exceptions: int


class ListingPreviewOut(BaseModel):
    layout: ListingLayoutOut
    products: list[ListingProductOut]
    block_products: dict[str, list[str]]
    labels: list[ListingLabelOut]
    options: dict[str, list[ListingCategoryOption]]
    members: AdcPreview
    issues: list[ListingIssueOut]
    checks: list[ListingCheck]
    reused_profile: bool
    # Rules that place joiners not on the listing, learned from it.
    joiner_rules: list[JoinerProductOut] = Field(default_factory=list)
    people: ListingPeopleOut
    entities: list[ListingEntityOut] = Field(default_factory=list)
    notices: list[ListingNoticeOut] = Field(default_factory=list)
    policy_period: tuple[date, date] | None = None


class LabelChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str | None = Field(default=None, max_length=36)
    not_covered: bool = False


class ListingMappingIn(BaseModel):
    """The broker's reviewed decisions, sent with preview (to re-suggest) and apply."""

    model_config = ConfigDict(extra="forbid")

    block_products: dict[str, list[str]] = Field(default_factory=dict)
    # block index -> normalized listing label -> decision
    labels: dict[str, dict[str, LabelChoiceIn]] = Field(default_factory=dict)
    # listing Entity spelling -> the slip entity it is linked to on apply.
    entity_links: dict[str, str] = Field(default_factory=dict, max_length=200)

    @field_validator("entity_links")
    @classmethod
    def _bounded_links(cls, links: dict[str, str]) -> dict[str, str]:
        if any(len(k) > 255 or len(v) > 255 for k, v in links.items()):
            raise ValueError("Entity names are limited to 255 characters.")
        return links


class ListingApplyOut(BaseModel):
    added: int
    changed: int
    deleted: int
    missing_terminated: int
    unchanged: int
    rematched: int
    assignments: int
    dependant_assignments: int
    profile_saved: bool
    flex_errors: list[str]
    joiner_rules_written: int = 0
    underwriting_updated: int = 0
    entity_aliases_linked: int = 0
