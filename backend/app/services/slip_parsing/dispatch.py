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

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from app.services import product_registry
from app.services.excel_reader import Sheet, open_workbook
from app.services.slip_parsing.endorsements import extract_endorsements
from app.services.slip_parsing.header import _find_column_header_row, _scan_policy_header
from app.services.slip_parsing.models import (
    ExtractedCategory,
    ExtractedEndorsement,
    ExtractedPlan,
    PlacementSlip,
    PolicyHeader,
    ProductSlip,
    SlipSection,
    SlipTerm,
)
from app.services.slip_parsing.rates import (
    _enrich_with_rates,
    _extract_voluntary_rates,
    _find_rate_section_start,
    extract_rate_schedules,
)
from app.services.slip_parsing.sections import extract_sections, extract_terms
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
    {
        "billing numbers",
        "comments",
        "endorsement",
        "endorsements",
        "endorsment",
        "setup",
        "summary",
        "renewal overall premium",
    }
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
    endorsements: tuple[ExtractedEndorsement, ...] = ()
    terms: tuple[SlipTerm, ...] = ()
    sections: tuple[SlipSection, ...] = ()
    merges_resolved: bool = False
    rate_schedules: tuple[dict[str, Any], ...] = ()
    extraction_issues: tuple[str, ...] = ()


def _free_text(
    rows: list[list[Any]],
    categories: tuple[ExtractedCategory, ...],
    sob_idx: int,
    sob_end: int,
) -> tuple[tuple[SlipTerm, ...], tuple[SlipSection, ...]]:
    """Terms and titled sections in the gaps the structured parsers leave.

    Regions are positional: after the last Basis-of-Cover row up to the Rate
    section (sub-tables), from the Rate section to the benefit schedule
    (terms), and from where the schedule stops to the end of the sheet.
    """
    n = len(rows)
    basis_end = max((c.source_row for c in categories), default=0)
    rate_start = _find_rate_section_start(rows)
    schedule_start = sob_idx if sob_idx >= 0 else n
    gap_end = rate_start if rate_start > basis_end else schedule_start
    terms_start = rate_start if rate_start >= 0 else basis_end
    terms = extract_terms(rows, terms_start, schedule_start)
    # Without a Rate section ahead of the schedule the two regions overlap; a
    # row already kept as a term must not be exported a second time inside a
    # section block.
    term_rows = frozenset(t.source_row - 1 for t in terms)
    sections = list(extract_sections(rows, basis_end, gap_end, "basis", exclude=term_rows))
    if 0 <= sob_end < n:
        sections += extract_sections(rows, sob_end, n, "after_sob")
    return terms, tuple(sections)


def _fill_plan_spans(
    rows: list[list[Any]],
    merged_ranges: tuple[tuple[int, int, int, int], ...],
    plan_cols: list[tuple[str, str, int]],
    sob_idx: int,
) -> tuple[list[list[Any]], bool]:
    """Rows with each value merged ACROSS plan columns copied into every plan,
    and whether the schedule states shared values by merging at all.

    A schedule states a shared value once, merged over the plans it applies to
    ("150% of item 1 to 7" across PLAN 1..3). The workbook reports it only in
    the merge's first cell, so the other plans read blank — and a blank plan
    cell later inherits whichever column sorts first, handing one plan's value
    to plans the slip never gave it. Only horizontal spans covering two or more
    plan columns are filled, and only on the span's first row: a vertical merge
    (one plan's limit spanning its sub-rows) must stay a single cell.

    A merge that starts left of the first plan column is a heading or a label
    spanning the table ("OUTPATIENT BENEFITS" across name + plans), never a
    plan value, so it is left alone. The flag is True only when the schedule
    itself merges across plans: then a plan cell still blank is the slip's own
    blank. A sheet that merges nothing there (a merged title row doesn't count)
    writes shared values once and leaves the rest blank, and those must inherit.
    """
    plan_idx = sorted(c for _, _, c in plan_cols)
    if sob_idx < 0 or len(plan_idx) < 2 or not merged_ranges:
        return rows, False
    out = rows
    copied: set[int] = set()
    spans = False
    for r0, _r1, c0, c1 in merged_ranges:
        if r0 <= sob_idx or r0 >= len(rows) or c0 < plan_idx[0]:
            continue
        covered = [c for c in plan_idx if c0 <= c < c1]
        if len(covered) < 2:
            continue
        spans = True
        row = rows[r0] or []
        value = row[c0] if c0 < len(row) else None
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if out is rows:
            out = list(rows)
        if r0 not in copied:
            out[r0] = list(row) + [None] * max(0, covered[-1] + 1 - len(row))
            copied.add(r0)
        for c in covered:
            if out[r0][c] is None or (isinstance(out[r0][c], str) and not out[r0][c].strip()):
                out[r0][c] = value
    return out, spans


def _location_terms(category: ExtractedCategory) -> tuple[Any, ...]:
    """What a cohort is entitled to, independent of where it works."""
    return (
        category.basis,
        category.participation.strip().lower(),
        category.premium_rate,
        category.rate_basis,
        repr(sorted((category.rate_tiers or {}).items())),
        category.member_scope,
    )


def _merge_location_splits(
    categories: tuple[ExtractedCategory, ...],
) -> tuple[ExtractedCategory, ...]:
    """One category per cohort when a slip repeats the same cohorts per depot.

    GAS's GPA lists Directors / Professionals / Other Staff / Bus Drivers once
    for Loyang Depot and again for East Coast Depot with identical terms. The
    location then only splits headcount and sum insured, so the rows merge into
    one category carrying a per-location breakdown. Cohorts whose terms differ
    by location stay separate, location-scoped categories, as do locations
    stated in Participation ("Compulsory - SG Office"), which define who is
    eligible rather than where a sum insured is reported.
    """
    scoped = [
        c for c in categories
        if c.location_scope and c.location_scope in (c.source_insured or "")
    ]
    if len({c.location_scope for c in scoped}) < 2:
        return categories
    groups: dict[tuple[str, str], list[ExtractedCategory]] = {}
    for c in scoped:
        groups.setdefault((c.insured.strip().lower(), c.category.strip().lower()), []).append(c)
    if any(
        len({c.location_scope for c in group}) != len(group)
        or len({_location_terms(c) for c in group}) != 1
        for group in groups.values()
    ):
        return categories

    def _total(values: list[float | int | None]) -> float | None:
        known = [v for v in values if v is not None]
        return sum(known) if known else None

    merged: dict[tuple[str, str], ExtractedCategory] = {}
    out: list[ExtractedCategory] = []
    member_ids = {id(c) for c in scoped}
    for c in categories:
        if id(c) not in member_ids:
            out.append(c)
            continue
        key = (c.insured.strip().lower(), c.category.strip().lower())
        if key in merged:
            continue
        group = groups[key]
        employees = _total([g.num_employees for g in group])
        merged[key] = replace(
            c,
            location_scope=None,
            num_employees=int(employees) if employees is not None else None,
            sum_insured=_total([g.sum_insured for g in group]),
            annual_premium=_total([g.annual_premium for g in group]),
            estimated_annual_earnings=_total([g.estimated_annual_earnings for g in group]),
            location_breakdown=tuple(
                {
                    "location": g.location_scope,
                    "num_employees": g.num_employees,
                    "sum_insured": g.sum_insured,
                    "source_row": g.source_row,
                }
                for g in group
            ),
        )
        out.append(merged[key])
    return tuple(out)


def _extract_categories_from_sheet(
    sheet: Sheet,
    product_code: str = "",
    profile_resolver: ProfileResolver | None = None,
    rate_date: date | None = None,
) -> _SheetResult:
    # Imported here (not module top) to avoid a cycle: the SOB module shares
    # this package's models/text helpers.
    from app.services.placement_slip_sob import (
        _detect_plan_columns,
        _extract_plans_from_sheet,
        _find_data_start,
        _find_sob_section,
        _fingerprint_from_parts,
        _is_stop_row,
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
        return _SheetResult(
            header_fields.header,
            (),
            endorsements=extract_endorsements(sheet),
        )

    cols = _identify_columns(rows[header_idx])
    if cols.category < 0:
        return _SheetResult(
            header_fields.header,
            (),
            endorsements=extract_endorsements(sheet),
        )
    # Expand the count column into its per-tier block when the sheet splits it
    # (and skip that sub-header row during the walk).
    cols = _identify_count_columns(rows, header_idx, cols)

    categories = _walk_data_rows(
        rows,
        header_idx,
        cols,
        product_code=product_code,
        merged_ranges=sheet.merged_ranges,
    )

    # Enrich categories with premium rate data from the Rate section.
    rate_data, tier_labels, rate_schedules, rate_issues = extract_rate_schedules(
        rows, effective_date=rate_date, policy_period=header_fields.header.period
    )
    categories = _enrich_with_rates(categories, rate_data)
    # Explicit depot/site qualifiers describe location, not another legal
    # entity. Split only when the remaining name equals the policy's insured.
    from app.services.matching_engine import normalize_entity

    scoped_categories = []
    for category in categories:
        qualified = re.match(
            r"^(.+?)(?:\s*\(\s*|\s+-\s+)([^()]+\b(?:depot|site|office))\s*\)?$",
            category.insured,
            re.I,
        )
        if qualified and normalize_entity(qualified[1]) == normalize_entity(
            header_fields.header.insured
        ):
            category = replace(
                category,
                insured=header_fields.header.insured or qualified[1],
                location_scope=qualified[2].strip(),
                source_insured=category.insured,
            )
        scoped_categories.append(category)
    categories = _merge_location_splits(tuple(scoped_categories))
    if rate_schedules:
        missing = [
            c
            for c in categories
            if c.premium_rate is None and not c.rate_tiers and not c.annual_premium
        ]
        if missing:
            rate_issues += (
                f"{len(missing)} category row(s) have no usable rate. "
                "Check blank source rates and insured-entity names before pricing.",
            )

    # Age-banded voluntary rate table — drives voluntary employee and dependant
    # pricing off the member's age band, not the flat compulsory rate.
    voluntary_rates = _extract_voluntary_rates(rows)

    # Locate the SOB layout ONCE (section index, plan columns, data start) and
    # thread it through fingerprinting, role resolution, and extraction instead of
    # re-scanning the sheet 2-3x. A stored broker correction (matched by template
    # fingerprint) wins over the content profiler.
    sob_idx = _find_sob_section(rows)
    if sob_idx < 0:
        terms, sections = _free_text(rows, categories, -1, -1)
        return _SheetResult(
            header_fields.header,
            categories,
            voluntary_rates=voluntary_rates,
            tier_labels=tier_labels,
            endorsements=extract_endorsements(sheet),
            terms=terms,
            sections=sections,
            rate_schedules=rate_schedules,
            extraction_issues=rate_issues,
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

    plan_rows, merges_resolved = _fill_plan_spans(
        rows, sheet.merged_ranges, plan_cols, sob_idx
    )
    plans = _extract_plans_from_sheet(
        plan_rows,
        roles_override=used_roles,
        sob_idx=sob_idx,
        plan_cols=plan_cols,
        data_start=data_start,
    )

    sob_end = next(
        (i for i in range(data_start, len(rows)) if _is_stop_row(rows[i] or [])),
        -1,
    )
    terms, sections = _free_text(rows, categories, sob_idx, sob_end)
    return _SheetResult(
        header_fields.header,
        categories,
        plans,
        sob_fingerprint=fingerprint,
        sob_roles=roles_to_dict(used_roles) if used_roles else None,
        voluntary_rates=voluntary_rates,
        tier_labels=tier_labels,
        endorsements=extract_endorsements(sheet),
        terms=terms,
        sections=sections,
        merges_resolved=merges_resolved,
        rate_schedules=rate_schedules,
        extraction_issues=rate_issues,
    )


def parse_placement_slip(
    path: Path | str,
    client_label: str,
    profile_resolver: ProfileResolver | None = None,
    classification_resolver: ClassificationResolver | None = None,
    rate_date: date | None = None,
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
    with open_workbook(path, include_merged_ranges=True) as wb:
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
                sheet, product_code, profile_resolver, rate_date
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
                    endorsements=result.endorsements,
                    terms=result.terms,
                    sections=result.sections,
                    merges_resolved=result.merges_resolved,
                    layout_family=entry.layout_family,
                    # A broker classification (stored metadata) counts as known.
                    registry_known=known or bool(metadata),
                    rate_schedules=result.rate_schedules,
                    extraction_issues=result.extraction_issues,
                )
            )
    return PlacementSlip(
        client=client_label,
        products=tuple(products),
        diagnostics={"skipped_sheets": skipped, "sheet_count": sheet_count},
    )
