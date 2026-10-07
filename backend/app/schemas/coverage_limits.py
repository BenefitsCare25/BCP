"""Coverage limit check payloads (``services/coverage_limits.py``)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LimitKindStr = Literal[
    "over_age",
    "over_entry_age",
    "dependant_over_age",
    "underwriting",
    "capped",
    "no_salary",
    "no_dob",
]


class LimitOverrides(BaseModel):
    """Unsaved setup values to preview a product's limits with. Strings, as the
    setup form holds them ("75", "75 ANB", "1600000")."""

    employee_age_limit: str | None = Field(default=None, max_length=40)
    last_entry_age: str | None = Field(default=None, max_length=40)
    spouse_age_limit: str | None = Field(default=None, max_length=40)
    child_age_limit: str | None = Field(default=None, max_length=40)
    max_sum_insured: str | None = Field(default=None, max_length=40)
    employees_above_last_entry_age: str | None = Field(default=None, max_length=4000)


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


class CoverageLimitsOut(BaseModel):
    alerts: list[CoverageLimitAlert]
    # People affected per kind; a person crossing three products counts once.
    counts: dict[str, int]
