"""Dependant selection — the roster's other population.

Dependants are not employees, so they need their own predicates (relationship,
link state, the status of a portal self-add). But a dependant's *cohort* is its
sponsoring employee's cohort, so the employee half is the existing
``MemberFilters`` nested verbatim rather than a parallel vocabulary — that is
what makes "show me the dependants of everyone in Subsidiary B on Plan 3" one
query instead of two screens.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.member_query import AgeFilter, FacetValue, MemberFilters

# Mirrors ``models.dependant`` — a portal self-add lands as ``pending_approval``
# and only becomes coverage-bearing once a broker approves it.
DependantStatusStr = Literal["active", "pending_approval", "rejected", "terminated"]
# ``classify_relationship`` answers spouse/child/None; "other" is that None,
# named. It is the bucket parents and unclassifiable roster wording fall into,
# and is worth filtering on precisely because it is where data problems collect.
DependantRoleStr = Literal["spouse", "child", "other"]
LinkStateStr = Literal["any", "linked", "unlinked"]


class DependantFilters(BaseModel):
    q: str | None = Field(default=None, max_length=255)
    # EMPTY means the default view: active only. There is deliberately no "all"
    # token — the UI ticks every box, so what was asked for is always explicit.
    statuses: list[DependantStatusStr] = Field(default_factory=list, max_length=4)
    # Raw roster wording ("Spouse", "Son", "Daughter"). Free text with no enum,
    # so the vocabulary is served from the facets, never hardcoded.
    relationships: list[str] = Field(default_factory=list, max_length=200)
    roles: list[DependantRoleStr] = Field(default_factory=list, max_length=3)
    link_state: LinkStateStr = "any"
    link_methods: list[str] = Field(default_factory=list, max_length=20)
    # Age-Next-Birthday at the benefit year's start — the same convention every
    # dependant eligibility window uses, so "children ageing out" agrees with
    # the rules that will actually drop them.
    age: AgeFilter | None = None
    # The SPONSORING employee. Nested rather than duplicated, so a dependant can
    # be filtered by category, product, plan, entity or any roster attribute.
    # Setting it necessarily excludes unlinked dependants — they have no
    # employee to test.
    employee: MemberFilters | None = None

    def has_filters(self) -> bool:
        return bool(
            self.q
            or self.statuses
            or self.relationships
            or self.roles
            or self.link_methods
            or self.age
            or self.link_state != "any"
            or (self.employee is not None and self.employee.has_filters())
        )


class DependantQueryListIn(BaseModel):
    query: DependantFilters = Field(default_factory=DependantFilters)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


# ── Facets ──────────────────────────────────────────────────────────────────


class DependantFacetsOut(BaseModel):
    """Vocabulary + headcounts for the Dependants filter bar.

    Every facet except ``statuses`` counts the DEFAULT view (active dependants),
    because that is the population those filters narrow. ``statuses`` spans every
    dependant in the year, since it is the control that *widens* the population —
    the same split ``MemberFacetsOut`` makes with ``terminated_total``.
    """

    active_total: int
    all_statuses_total: int
    linked: int
    unlinked: int
    statuses: list[FacetValue]
    relationships: list[FacetValue]
    roles: list[FacetValue]
    link_methods: list[FacetValue]
