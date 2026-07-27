"""Per-sheet orchestration and the workbook entry point.

For each product sheet: scan the policy header, locate the Basis-of-Cover
column header, walk the category rows, enrich with the Rate section, pick up
any voluntary age-band table, then extract the Schedule of Benefits. The
product registry classifies each sheet (code, layout family, known/unknown);
unknown codes still extract via the generic content-driven pipeline and are
flagged ``registry_known=False`` so the API layer can surface
``needs_classification`` instead of trusting a silent default.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services import product_registry
from app.services.excel_reader import Sheet, open_workbook
from app.services.slip_parsing.header import _find_column_header_row, _scan_policy_header
from app.services.slip_parsing.models import (
    ExtractedCategory,
    ExtractedPlan,
    PlacementSlip,
    PolicyHeader,
    ProductSlip,
)
from app.services.slip_parsing.rates import (
    _enrich_with_rates,
    _extract_voluntary_rates,
    extract_rate_section,
)
from app.services.slip_parsing.walk import (
    _identify_columns,
    _identify_count_columns,
    _walk_data_rows,
)

# A resolver maps a template fingerprint to a stored column-role override (as a
# plain dict) or None. Injected by the API layer so the pure parser stays
# DB-free; see app/services/slip_template_memory.py.
ProfileResolver = Callable[[str], dict[str, Any] | None]

# Maps a product code to the tenant's stored `product_metadata` overrides
# (form_profile / line / layout_family / has_dependants) or None. Injected by
# the API layer so a broker's classification of an unknown product applies on
# the next upload without giving the parser DB access.
ClassificationResolver = Callable[[str], dict[str, Any] | None]

NON_PRODUCT_SHEETS: frozenset[str] = frozenset(
    {"billing numbers", "comments", "setup", "summary", "renewal overall premium"}
)


@dataclass(frozen=True)
class _SheetResult:
    policy_header: PolicyHeader
    categories: tuple[ExtractedCategory, ...]
    plans: tuple[ExtractedPlan, ...] = ()
    sob_fingerprint: str | None = None
    sob_roles: dict[str, Any] | None = None
    voluntary_rates: tuple[dict[str, Any], ...] = ()
    tier_labels: dict[str, str] | None = None


def _extract_categories_from_sheet(
    sheet: Sheet,
    product_code: str = "",
    profile_resolver: ProfileResolver | None = None,
) -> _SheetResult:
    # Imported here (not module top) to avoid a cycle: the SOB module shares
    # this package's models/text helpers.
    from app.services.placement_slip_sob import (
        _detect_plan_columns,
        _extract_plans_from_sheet,
        _find_data_start,
        _find_sob_section,
        _fingerprint_from_parts,
        _profile_sob_columns,
        roles_from_dict,
        roles_to_dict,
    )

    rows = sheet.rows
    header_fields = _scan_policy_header(rows)
    basis_idx = header_fields.basis_row

    # Some templates (e.g. CBRE Dental) omit the literal "Basis of Cover" row
    # and jump straight from policy header to the column header. Fall back to
    # scanning the first 30 rows for an Insured+Category column header.
    if basis_idx < 0:
        header_idx = _find_column_header_row(rows, basis_idx=-1)
    else:
        header_idx = _find_column_header_row(rows, basis_idx)
    if header_idx < 0:
        return _SheetResult(header_fields.header, ())

    cols = _identify_columns(rows[header_idx])
    if cols.category < 0:
        return _SheetResult(header_fields.header, ())
    # Expand the count column into its per-tier block when the sheet splits it
    # (and skip that sub-header row during the walk).
    cols = _identify_count_columns(rows, header_idx, cols)

    categories = _walk_data_rows(rows, header_idx, cols, product_code=product_code)

    # Enrich categories with premium rate data from the Rate section.
    rate_data, tier_labels = extract_rate_section(rows)
    categories = _enrich_with_rates(categories, rate_data)

    # Age-banded voluntary rate table — drives voluntary employee and dependant
    # pricing off the member's age band, not the flat compulsory rate.
    voluntary_rates = _extract_voluntary_rates(rows)

    # Locate the SOB layout ONCE (section index, plan columns, data start) and
    # thread it through fingerprinting, role resolution, and extraction instead of
    # re-scanning the sheet 2-3x. A stored broker correction (matched by template
    # fingerprint) wins over the content profiler.
    sob_idx = _find_sob_section(rows)
    if sob_idx < 0:
        return _SheetResult(
            header_fields.header,
            categories,
            voluntary_rates=voluntary_rates,
            tier_labels=tier_labels,
        )

    plan_cols = _detect_plan_columns(rows, sob_idx)
    data_start = _find_data_start(rows, sob_idx)
    fingerprint = _fingerprint_from_parts(
        product_code, header_fields.header.insurer, plan_cols
    )
    override = (
        profile_resolver(fingerprint) if profile_resolver and fingerprint else None
    )
    used_roles = (
        roles_from_dict(override)
        if override
        else _profile_sob_columns(rows, data_start, plan_cols)
    )

    plans = _extract_plans_from_sheet(
        rows,
        roles_override=used_roles,
        sob_idx=sob_idx,
        plan_cols=plan_cols,
        data_start=data_start,
    )

    return _SheetResult(
        header_fields.header,
        categories,
        plans,
        sob_fingerprint=fingerprint,
        sob_roles=roles_to_dict(used_roles) if used_roles else None,
        voluntary_rates=voluntary_rates,
        tier_labels=tier_labels,
    )


def parse_placement_slip(
    path: Path | str,
    client_label: str,
    profile_resolver: ProfileResolver | None = None,
    classification_resolver: ClassificationResolver | None = None,
) -> PlacementSlip:
    """Parse a placement-slip workbook end-to-end.

    ``profile_resolver`` (optional) maps a template fingerprint to a stored
    broker-corrected column mapping; when it returns one, that override drives
    SOB extraction for the matching sheet instead of the content profiler.

    ``classification_resolver`` (optional) supplies the tenant's stored
    ``product_metadata`` per product code, so a broker's classification of a
    previously-unknown product (form profile / layout family) applies on
    re-upload and clears the ``needs_classification`` flag.
    """
    products: list[ProductSlip] = []
    skipped: list[dict[str, Any]] = []
    with open_workbook(path) as wb:
        sheet_count = len(wb.sheet_names)
        for sheet_name in wb.sheet_names:
            if sheet_name.strip().lower() in NON_PRODUCT_SHEETS:
                skipped.append({"sheet": sheet_name, "reason": "non_product"})
                continue
            sheet = wb.sheet(sheet_name)
            product_code, known = product_registry.derive_product_code(sheet_name)
            metadata = (
                classification_resolver(product_code)
                if classification_resolver
                else None
            )
            entry = product_registry.resolve_entry(product_code, metadata)
            result = _extract_categories_from_sheet(
                sheet, product_code, profile_resolver
            )
            if not result.categories:
                skipped.append({"sheet": sheet_name, "reason": "no_categories_found"})
                continue
            products.append(
                ProductSlip(
                    sheet=sheet_name,
                    product_code=product_code,
                    policy_header=result.policy_header,
                    categories=result.categories,
                    plans=result.plans,
                    sob_fingerprint=result.sob_fingerprint,
                    sob_roles=result.sob_roles,
                    voluntary_rates=result.voluntary_rates,
                    tier_labels=result.tier_labels,
                    layout_family=entry.layout_family,
                    # A broker classification (stored metadata) counts as known.
                    registry_known=known or bool(metadata),
                )
            )
    return PlacementSlip(
        client=client_label,
        products=tuple(products),
        diagnostics={"skipped_sheets": skipped, "sheet_count": sheet_count},
    )
