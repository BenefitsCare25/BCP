"""Data read from a company's own Employee Listing (EL) workbook.

The EL is the client's working register: one row per employee, followed by a
row per dependant, then a column block per insured product holding that
person's category, plan, family tier, administration type, sums insured,
underwriting state and premium. Layouts differ per company, so the reader
records which column plays which role rather than assuming fixed positions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

BlockKind = Literal["employee", "dependant", "product"]


@dataclass(frozen=True)
class ElColumn:
    index: int  # 0-based worksheet column
    letter: str  # Excel letter, for broker-facing references
    header: str  # header text with whitespace collapsed
    role: str  # semantic role, "other" when unrecognised


@dataclass(frozen=True)
class ElBlock:
    """A banner-delimited group of columns (EMPLOYEE, DEPENDANTS, one product)."""

    kind: BlockKind
    banner: str
    columns: tuple[ElColumn, ...]
    # Product blocks only: the product code the block names ("GTL", "GP"),
    # from the banner's "(GTL)" or a header such as "GP Premium". A hint for
    # the broker's block → product mapping, never applied without review.
    code_hint: str | None = None

    def column(self, role: str) -> ElColumn | None:
        return next((c for c in self.columns if c.role == role), None)


@dataclass(frozen=True)
class ElLayout:
    sheet: str
    banner_row: int  # 0-based; -1 when the sheet has no banner row
    header_row: int  # 0-based
    blocks: tuple[ElBlock, ...]
    # Columns outside every block that carry row-level facts (last day of
    # service, remarks, internal update notes).
    trailing: tuple[ElColumn, ...]
    reference_date: date | None = None  # the age reference above the DOB column

    @property
    def employee(self) -> ElBlock | None:
        return next((b for b in self.blocks if b.kind == "employee"), None)

    @property
    def dependant(self) -> ElBlock | None:
        return next((b for b in self.blocks if b.kind == "dependant"), None)

    @property
    def products(self) -> tuple[ElBlock, ...]:
        return tuple(b for b in self.blocks if b.kind == "product")


@dataclass(frozen=True)
class ElCover:
    """One person's values in one product block, keyed by column role."""

    block: int  # index into ElLayout.blocks
    values: dict[str, Any]

    @property
    def is_empty(self) -> bool:
        """No cover is recorded: age and zero amounts are formula residue
        (a GST cell computed on a blank premium), not cover."""
        return all(
            v in (None, "", 0) for k, v in self.values.items() if k != "age"
        )


@dataclass
class ElDependant:
    row: int  # 1-based worksheet row
    fields: dict[str, Any]
    covers: list[ElCover] = field(default_factory=list)


@dataclass
class ElEmployee:
    row: int  # 1-based worksheet row
    fields: dict[str, Any]
    covers: list[ElCover] = field(default_factory=list)
    trailing: dict[str, Any] = field(default_factory=dict)
    dependants: list[ElDependant] = field(default_factory=list)


@dataclass(frozen=True)
class ElIssue:
    """A row-level finding. Messages name rows and fields, never personal data."""

    row: int | None
    field: str | None
    code: str
    message: str


@dataclass
class ElWorkbook:
    layout: ElLayout
    employees: list[ElEmployee]
    issues: list[ElIssue]
    # 1-based rows that hold a person's details but were not read as anyone:
    # a dependant with no employee row, or a row with an ID but no name.
    unread_rows: list[int] = field(default_factory=list)
