"""Placement-slip parsing package.

The parser was split out of the monolithic ``placement_slip_parser.py`` into
layout-focused modules; ``app.services.placement_slip_parser`` remains the
stable import surface (a re-export shim), so consumers and tests never import
from these submodules directly.

Module map:
- ``models``        — extracted-data dataclasses (categories, plans, slips)
- ``text``          — cell/text normalization helpers + shared regexes
- ``participation`` — participation-cell parsing (mode / audience / direction / scope)
- ``header``        — policy-header scan + column-header row location
- ``walk``          — Basis-of-Cover column identification + data-row walk
- ``rates``         — Rate-section extraction + category enrichment
- ``dispatch``      — per-sheet orchestration and the workbook entry point
"""
from app.services.slip_parsing.dispatch import (
    NON_PRODUCT_SHEETS,
    ProfileResolver,
    parse_placement_slip,
)
from app.services.slip_parsing.models import (
    ExtractedBenefitItem,
    ExtractedCategory,
    ExtractedLimit,
    ExtractedPlan,
    ExtractedSubItem,
    PlacementSlip,
    PolicyHeader,
    ProductSlip,
)
from app.services.slip_parsing.participation import (
    ParticipationSpec,
    normalize_participation,
    parse_participation,
)
from app.services.slip_parsing.text import split_plan_codes

__all__ = [
    "NON_PRODUCT_SHEETS",
    "ExtractedBenefitItem",
    "ExtractedCategory",
    "ExtractedLimit",
    "ExtractedPlan",
    "ExtractedSubItem",
    "ParticipationSpec",
    "PlacementSlip",
    "PolicyHeader",
    "ProductSlip",
    "ProfileResolver",
    "normalize_participation",
    "parse_participation",
    "parse_placement_slip",
    "split_plan_codes",
]
