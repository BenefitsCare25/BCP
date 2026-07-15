"""ADC (Additions / Deletions / Changes) roster movement — preview + apply.

The template round-trips the current roster with an ``Action`` column; the
preview classifies + validates + diffs each movement without mutating, and apply
re-evaluates the same file (so the dry-run can't diverge) before committing.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AdcFieldDiff(BaseModel):
    field: str
    old: str | None = None
    new: str | None = None


class AdcOp(BaseModel):
    """One classified movement row (add / change / delete)."""

    row: int  # 1-based workbook row number (for the broker to locate it)
    record_type: str  # "employee" | "dependant"
    name: str | None = None
    staff_id: str | None = None
    nric_masked: str | None = None
    target_id: str | None = None  # resolved existing record (change / delete)
    effective: str | None = None  # ISO effective date (delete)
    field_diffs: list[AdcFieldDiff] = Field(default_factory=list)


class AdcIssue(BaseModel):
    """A row that can't be applied — unknown action, unresolved target, or a
    duplicate addition. Reported, never silently dropped."""

    row: int
    record_type: str
    message: str


class AdcPreview(BaseModel):
    additions: list[AdcOp] = Field(default_factory=list)
    changes: list[AdcOp] = Field(default_factory=list)
    deletions: list[AdcOp] = Field(default_factory=list)
    issues: list[AdcIssue] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class AdcApplyResult(BaseModel):
    added: int = 0
    changed: int = 0
    deleted: int = 0
    skipped: int = 0  # additions that resolved to an existing record
    rematched: int = 0
    issues: list[AdcIssue] = Field(default_factory=list)
    flex_errors: list[str] = Field(default_factory=list)
