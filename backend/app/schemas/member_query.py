"""Roster selection — the query a broker composes instead of keying staff ids.

``MemberQuery`` is the request half; the facet models are the vocabulary the
picker offers. Both are deliberately independent of the bulk-update module so a
selection can be reused by any surface that acts on a population (the bulk
coverage tool today; Coverage & Members next).
"""
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

# Coverage deviation, scoped to the product a request is about:
#   default    — no override; the member sits on their cohort's plan
#   overridden — an override exists (enrollment, bulk, or manual admin edit)
#   declined   — an override that opts out of the product entirely
CoverageStateStr = Literal["any", "default", "overridden", "declined"]


class AttributeFilter(BaseModel):
    """One roster-attribute predicate (department, grade, entity, …).

    Values are matched case/whitespace-insensitively against the merged
    ``attribute_values`` + ``derived_attribute_values`` bag, derived winning —
    the same precedence the leave-rate and flex-tier vocabularies use.
    """

    key: str = Field(min_length=1, max_length=64)
    values: list[str] = Field(default_factory=list, max_length=500)
    op: Literal["in", "not_in"] = "in"

    @model_validator(mode="after")
    def _nonempty(self) -> Self:
        if not [v for v in self.values if v.strip()]:
            raise ValueError("An attribute filter needs at least one value.")
        return self


class AgeFilter(BaseModel):
    """Inclusive age window, Age-Next-Birthday as of the benefit year's start.

    ANB because that is the app's canonical convention for every eligibility
    window (see the age section of CLAUDE.md); a filter quoting actual age would
    silently disagree with the dependant/NEL rules a broker reads elsewhere.
    """

    min: int | None = Field(default=None, ge=0, le=120)
    max: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("age.min must not exceed age.max.")
        if self.min is None and self.max is None:
            raise ValueError("An age filter needs a min, a max, or both.")
        return self


class MemberQuery(BaseModel):
    """A population, described as a rule.

    Resolution order (``services/member_query.resolve_selection``): the filters
    are ANDed, the explicit ids are ADDED, and ``exclude_employee_ids`` is
    SUBTRACTED last.
    """

    q: str | None = Field(default=None, max_length=255)
    # Leavers are excluded by default, matching the roster's own default view. A
    # bulk tool that silently includes terminated members reinstates cover for
    # people who have left.
    include_terminated: bool = False
    category_ids: list[str] = Field(default_factory=list, max_length=500)
    # Members covered by ALL of these products (a matched category per product).
    product_codes: list[str] = Field(default_factory=list, max_length=50)
    # Members whose EFFECTIVE plan for the request's product is one of these.
    current_plan_codes: list[str] = Field(default_factory=list, max_length=100)
    coverage_state: CoverageStateStr = "any"
    attributes: list[AttributeFilter] = Field(default_factory=list, max_length=20)
    age: AgeFilter | None = None
    # Explicit ADDITIONS to whatever the filters match.
    employee_ids: list[str] = Field(default_factory=list, max_length=5000)
    staff_ids: list[str] = Field(default_factory=list, max_length=5000)
    # Explicit REMOVALS, applied last. This is what keeps an apply request small:
    # the broker previews 412 members, unticks 3, and re-sends the same rule plus
    # 3 exclusions rather than 409 ids.
    exclude_employee_ids: list[str] = Field(default_factory=list, max_length=5000)

    def has_filters(self) -> bool:
        return bool(
            self.q
            or self.category_ids
            or self.product_codes
            or self.current_plan_codes
            or self.attributes
            or self.age
            or self.coverage_state != "any"
        )

    @model_validator(mode="after")
    def _nonempty(self) -> Self:
        # ``include_terminated`` alone is not a selection — it widens a pool
        # nothing has asked for yet, and resolving it would target the roster.
        if not (self.has_filters() or self.employee_ids or self.staff_ids):
            raise ValueError(
                "Provide at least one filter, employee_ids, or staff_ids."
            )
        return self


# ── Facets (the picker's vocabulary) ────────────────────────────────────────


class FacetValue(BaseModel):
    value: str
    count: int


class AttributeFacet(BaseModel):
    key: str
    label: str
    values: list[FacetValue]
    # True when the value list was capped — a cap that isn't reported reads as
    # "that's all of them".
    truncated: bool = False


class CategoryFacet(BaseModel):
    id: str
    label: str
    product_code: str | None = None
    count: int


class PlanFacet(BaseModel):
    code: str
    # Members whose EFFECTIVE plan is this one (override-aware), not the number
    # the category default would suggest — otherwise the picker disagrees with
    # the preview it feeds.
    count: int


class ProductFacet(BaseModel):
    id: str
    code: str
    name: str | None = None
    covered: int
    declined: int
    plans: list[PlanFacet]


class MemberFacetsOut(BaseModel):
    employees_total: int
    terminated_total: int
    attributes: list[AttributeFacet]
    categories: list[CategoryFacet]
    products: list[ProductFacet]


# ── Count + list resolution ─────────────────────────────────────────────────


class UnresolvedRefOut(BaseModel):
    kind: Literal["employee_id", "staff_id"]
    value: str
    reason: str


class MemberQueryCountIn(BaseModel):
    query: MemberQuery
    # Scopes ``current_plan_codes`` / ``coverage_state``; ignored otherwise.
    product_code: str | None = None


class MemberQueryCountOut(BaseModel):
    total: int
    unresolved: list[UnresolvedRefOut] = Field(default_factory=list)


class MemberListResolveIn(BaseModel):
    """Free text pasted from a spreadsheet column, an email, or a chat message."""

    text: str = Field(max_length=200_000)
    include_terminated: bool = False


class ResolvedMemberOut(BaseModel):
    id: str
    staff_id: str
    employee_name: str | None = None
    matched_on: Literal["staff_id", "nric"]


class MemberListResolveOut(BaseModel):
    matched: list[ResolvedMemberOut]
    unmatched: list[str]
    # Tokens that appeared more than once, or resolved to an already-matched
    # member — reported so a pasted list that looks like 40 people but is 37
    # doesn't quietly become 37.
    duplicates: int = 0
