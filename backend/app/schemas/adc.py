"""Roster movement (Additions / Changes / Deletions) — preview + apply.

Movements are DERIVED by diffing an uploaded member listing against the roster;
there is no ``Action`` column. The preview classifies + validates + diffs
without mutating, and apply re-evaluates the same file (so the dry-run can't
diverge) before committing. See `services/adc.py` for why `missing` is its own
bucket and not a deletion.
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
    """A row that can't be applied — no identifying column, or repeated within
    the file. Reported, never silently dropped."""

    row: int
    record_type: str
    message: str


class AdcWarning(BaseModel):
    """A row that IS applied but looks wrong. Distinct from `AdcIssue`, which
    is a row that cannot be applied at all — conflating them would make an
    advisory note read as a refusal."""

    row: int
    record_type: str
    message: str


class AdcPreview(BaseModel):
    additions: list[AdcOp] = Field(default_factory=list)
    changes: list[AdcOp] = Field(default_factory=list)
    #: Terminations the FILE states, via a past leaving date on the row.
    deletions: list[AdcOp] = Field(default_factory=list)
    #: On file but named nowhere in the upload. NOT a deletion — applied only
    #: when the caller opts in, because a partial export looks identical to a
    #: full census that dropped people. `counts["roster_total"]` is the
    #: denominator the UI needs to tell those two apart.
    missing: list[AdcOp] = Field(default_factory=list)
    issues: list[AdcIssue] = Field(default_factory=list)
    warnings: list[AdcWarning] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    #: Fingerprint of the `missing` set. The client returns it with apply so a
    #: roster that moved in between can't quietly terminate a different group.
    missing_digest: str | None = None


class AdcApplyResult(BaseModel):
    added: int = 0
    changed: int = 0
    deleted: int = 0
    #: Terminated because they were absent from the file (opt-in only), kept
    #: apart from `deleted` so the report says which evidence ended cover.
    missing_terminated: int = 0
    unchanged: int = 0
    rematched: int = 0
    issues: list[AdcIssue] = Field(default_factory=list)
    flex_errors: list[str] = Field(default_factory=list)
