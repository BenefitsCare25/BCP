"""Placement slip parser — stable import surface (re-export shim).

The implementation lives in the ``app.services.slip_parsing`` package (models /
participation / header / walk / rates / dispatch) plus ``placement_slip_sob``
for the Schedule-of-Benefits section. This module re-exports the whole public
and test-facing surface so existing imports keep working unchanged.

For each product sheet (those that aren't billing/setup/summary):
1. Find the row containing 'Basis of Cover' within the first 30 rows.
2. Within 1-5 rows below, find the column-header row containing
   'Category' AND ('Participation' OR 'Plan').
3. Walk rows from header_idx+1 collecting categories until a stop condition.

Validated against STM (4607 employees, 99% match rate) and VDL placement slips.
"""
from __future__ import annotations

from app.services import product_registry
from app.services.excel_reader import Cell
from app.services.placement_slip_sob import (
    _detect_plan_columns,
    _extract_plans_from_sheet,
    _find_data_start,
    _find_sob_section,
    _fingerprint_from_parts,
    _profile_sob_columns,
    _SobRoles,
    roles_from_dict,
    roles_to_dict,
    sob_template_fingerprint,
)
from app.services.slip_parsing.dispatch import (
    NON_PRODUCT_SHEETS,
    ClassificationResolver,
    ProfileResolver,
    _extract_categories_from_sheet,
    _SheetResult,
    parse_placement_slip,
)
from app.services.slip_parsing.header import (
    _age_from_birthday,
    _find_column_header_row,
    _normalize_age,
    _scan_policy_header,
    _up_to_age,
)
from app.services.slip_parsing.models import (
    ExtractedBenefitItem,
    ExtractedCategory,
    ExtractedEndorsement,
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
from app.services.slip_parsing.rates import (
    _blended_product_rate,
    _enrich_with_rates,
    _extract_rate_data,
    _extract_voluntary_rates,
    _parse_age_band,
    extract_rate_section,
)
from app.services.slip_parsing.text import (
    _cell_text,
    _currency_amount,
    _int_code,
    _lower,
    _non_empty,
    _norm,
    _row_text,
    _safe_float,
    split_plan_codes,
)
from app.services.slip_parsing.walk import (
    _Columns,
    _identify_columns,
    _identify_count_columns,
    _walk_data_rows,
)

# Codes the parser will recognize as the "real" product token when a sheet
# name contains multiple `-`-separated parts. Sourced from the product
# registry (the parser has no DB access and must handle codes from new client
# slips before they're seeded). Kept as a module attribute so drift checks and
# tests can introspect/patch it.
_KNOWN_PRODUCT_CODES: frozenset[str] = product_registry.known_codes()


def _derive_product_code(sheet_name: str) -> str:
    """Map a sheet name to a canonical product code (see
    ``product_registry.derive_product_code`` for the recognized shapes).

    Passes the module-level ``_KNOWN_PRODUCT_CODES`` through so tests that
    patch it keep influencing derivation.
    """
    code, _known = product_registry.derive_product_code(
        sheet_name, known=_KNOWN_PRODUCT_CODES
    )
    return code


__all__ = [
    "NON_PRODUCT_SHEETS",
    "_KNOWN_PRODUCT_CODES",
    "Cell",
    "ClassificationResolver",
    "ExtractedBenefitItem",
    "ExtractedCategory",
    "ExtractedEndorsement",
    "ExtractedLimit",
    "ExtractedPlan",
    "ExtractedSubItem",
    "ParticipationSpec",
    "PlacementSlip",
    "PolicyHeader",
    "ProductSlip",
    "ProfileResolver",
    "_Columns",
    "_SheetResult",
    "_SobRoles",
    "_age_from_birthday",
    "_blended_product_rate",
    "_cell_text",
    "_currency_amount",
    "_derive_product_code",
    "_detect_plan_columns",
    "_enrich_with_rates",
    "_extract_categories_from_sheet",
    "_extract_plans_from_sheet",
    "_extract_rate_data",
    "_extract_voluntary_rates",
    "_find_column_header_row",
    "_find_data_start",
    "_find_sob_section",
    "_fingerprint_from_parts",
    "_identify_columns",
    "_identify_count_columns",
    "_int_code",
    "_lower",
    "_non_empty",
    "_norm",
    "_normalize_age",
    "_parse_age_band",
    "_profile_sob_columns",
    "_row_text",
    "_safe_float",
    "_scan_policy_header",
    "_up_to_age",
    "_walk_data_rows",
    "extract_rate_section",
    "normalize_participation",
    "parse_participation",
    "parse_placement_slip",
    "roles_from_dict",
    "roles_to_dict",
    "sob_template_fingerprint",
    "split_plan_codes",
]
