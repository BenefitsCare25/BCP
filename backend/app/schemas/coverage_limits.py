"""Coverage limit check payloads (``services/coverage_limits.py``)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LimitKindStr = Literal[
    "over_age",
    "over_entry_age",
    "dependant_over_age",
    "dependant_not_eligible",
    "relationship_unknown",
    "underwriting",
    "capped",
    "no_salary",
    "no_dob",
]


# The setup's limit answers are free text ("Up to age 18, or 25 if in full-time
# study"); the saved check reads the first number from any length, so the
# preview accepts generous wording too (the form clamps to the same caps).
LIMIT_TEXT_MAX = 1000
EXEMPT_LIST_MAX = 4000


class LimitOverrides(BaseModel):
    """Unsaved setup values to preview a product's limits with. Strings, as the
    setup form holds them ("75", "75 ANB", "1600000")."""

    employee_age_limit: str | None = Field(default=None, max_length=LIMIT_TEXT_MAX)
    last_entry_age: str | None = Field(default=None, max_length=LIMIT_TEXT_MAX)
    spouse_age_limit: str | None = Field(default=None, max_length=LIMIT_TEXT_MAX)
    child_age_limit: str | None = Field(default=None, max_length=LIMIT_TEXT_MAX)
    max_sum_insured: str | None = Field(default=None, max_length=LIMIT_TEXT_MAX)
    employees_above_last_entry_age: str | None = Field(
        default=None, max_length=EXEMPT_LIST_MAX
    )


class CoverageLimitAlert(BaseModel):
    """One limit one person crosses on one product."""

    kind: LimitKindStr
    # "action" needs the broker; "info" states a fact the slip intends (a cap).
    severity: Literal["action", "info"]
    product_code: str
    product_name: str
    employee_id: str
    staff_id: str
    employee_name: str | None = None
    dependant_id: str | None = None
    dependant_name: str | None = None
    relationship: str | None = None
    age: int | None = None
    limit: float | None = None
    amount: float | None = None
    message: str
    # The broker's call on an over-age dependant: kept covered (with why) or
    # excluded from the product. A decided alert no longer needs action.
    decision: Literal["keep", "exclude"] | None = None
    decision_reason: str | None = None


class CoverageLimitsOut(BaseModel):
    alerts: list[CoverageLimitAlert]
    # People affected per kind; a person crossing three products counts once.
    counts: dict[str, int]


class DependantCoverDecisionIn(BaseModel):
    """Keep an over-age dependant covered (saying why) or exclude them."""

    dependant_id: str = Field(min_length=1, max_length=36)
    product_code: str = Field(min_length=1, max_length=64)
    decision: Literal["keep", "exclude"]
    reason: str | None = Field(default=None, max_length=255)


class DependantCoverDecisionOut(BaseModel):
    dependant_id: str
    product_code: str
    decision: Literal["keep", "exclude"]
    reason: str | None = None
