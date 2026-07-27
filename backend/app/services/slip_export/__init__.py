"""Placement / quotation slip export of a policy year's configured products.

Reverses the intake direction: where the parser turns an insurer's placement
slip into Category/Plan rows, this package renders those rows back into a
slip-shaped workbook modelled on the broker's real slips (reference: the CDL
2026 placement + quotation workbooks) — one sheet per product with the header
label block, the Basis of Cover table, the Rate section, voluntary age-banded
rates, and the Schedule of Benefits.

Two modes share the renderer:

* ``placement`` — the placed state: insurer named, rates and premiums filled.
* ``quotation`` — the shopping document that accompanies the Fact-Find form:
  same structure and figures, but the insurer, every rate/premium cell and the
  annual-premium total are left BLANK for the quoting insurer to complete.

Nothing here is keyed on a product code. Columns, rate-table shape, header
fields and terms are all derived from what each product's own configuration
carries, so a product type the platform has never seen exports correctly as
soon as its slip parses or its setup form is filled.

Member counts come from the ROSTER (``category_member_counts``), falling back to
the figure the slip stated where nothing matched — the sheet says which, per
table. Commercial terms the platform has no field for (refund formula, rating
groups) stay as labelled blanks for the broker, exactly how the reference slips
leave their unknown cells. Config-only: no member PII, and premium figures are
emitted as stored (GST-exclusive — grossing is a display concern, never
re-applied here).
"""
from __future__ import annotations

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.models import PolicyYear
from app.services.slip_export.workbook import build


def build_placement_slip_workbook(db: Session, py: PolicyYear) -> Workbook:
    return build(db, py, "placement")


def build_quotation_slip_workbook(db: Session, py: PolicyYear) -> Workbook:
    return build(db, py, "quotation")


__all__ = ["build_placement_slip_workbook", "build_quotation_slip_workbook"]
