"""Wire shapes for dual-coverage cases, opportunities and decisions."""
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class DualPartyOut(BaseModel):
    employee_id: str | None = None
    staff_id: str
    employee_name: str | None = None
    dependant_id: str | None = None
    relationship: str | None = None
    covered: bool = False
    covered_products: list[str] = Field(default_factory=list)
    # A dependant row whose sponsor is not an active employee, or that is not
    # linked at all: evidence the duplicate exists, never a live coverage line.
    unlinked: bool = False


class DualDecisionOut(BaseModel):
    decision: str
    carried_by_employee_id: str | None = None
    carried_by_staff_id: str | None = None
    note: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    # True when the family composition changed after the decision was taken. The
    # case re-surfaces — the failure mode is always "ask again", never "silently
    # resolved".
    stale: bool = False


class DualCaseOut(BaseModel):
    subject_key: str
    name: str
    dob: str | None = None
    nric_masked: str | None = None
    relationship: str | None = None
    # "nric" is certain; "name_dob" is strong but occasionally wrong, which is
    # why `not_a_match` is a first-class decision.
    match_tier: Literal["nric", "name_dob"]
    flags: list[str] = Field(default_factory=list)
    parties: list[DualPartyOut] = Field(default_factory=list)
    # Products BOTH sides cover — the intersection, so this is real double
    # exposure rather than two unrelated covers.
    overlapping_products: list[str] = Field(default_factory=list)
    severity: Literal["warn", "info"]
    decision: DualDecisionOut | None = None


class DualLifeRefOut(BaseModel):
    """One dependant ROW's membership in a shared life.

    What a roster table needs to mark the row and NAME both employees, without
    carrying the case's decision workflow. Keyed by dependant row rather than by
    life because that is what a table has in hand.
    """

    dependant_id: str
    subject_key: str
    severity: Literal["warn", "info"]
    # Decided, and the decision still describes this family.
    resolved: bool
    # Every side of the life, in case order — the table names them all rather
    # than saying "also somewhere else".
    parties: list[DualPartyOut] = Field(default_factory=list)


class DualOpportunityOut(BaseModel):
    subject_key: str
    employees: list[DualPartyOut]
    child_name: str
    child_dob: str | None = None
    listed_under_staff_id: str
    other_staff_id: str
    decision: DualDecisionOut | None = None


class DualCoverageOut(BaseModel):
    """Counts are always exact; the lists are capped (`preview_cap`)."""

    unresolved_cases: int
    total_cases: int
    total_opportunities: int
    cases: list[DualCaseOut]
    opportunities: list[DualOpportunityOut]
    preview_cap: int
    # Deliberately NOT capped, unlike `cases`. This drives a per-row marker on a
    # PAGINATED table, so a cap would silently leave rows on later pages
    # unmarked — the one failure this column exists to prevent. Each entry is
    # small, and the list is bounded by the duplicates a roster actually has.
    lives: list[DualLifeRefOut] = Field(default_factory=list)


class DualDecisionIn(BaseModel):
    subject_key: str = Field(min_length=1, max_length=64)
    subject_kind: Literal["life", "couple"] = "life"
    decision: Literal["carried_by", "intentional_both", "not_a_match", "dismissed"] = (
        "carried_by"
    )
    carried_by_employee_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _carried_by_needs_an_employee(self) -> Self:
        # "Carried by nobody" is not a decision — it would clear the flag while
        # recording nothing about who keeps the life.
        if self.decision == "carried_by" and not self.carried_by_employee_id:
            raise ValueError("carried_by requires carried_by_employee_id.")
        return self
