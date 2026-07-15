"""Acceptance suite over the real reference placement-slip workbooks.

Gated: the workbooks contain client PII and are NEVER committed. The suite
reads them from ``INSPRO_REFERENCE_SLIPS`` (default: the repo's ``reference/``
directory) and skips per-file when absent, mirroring the STM/VDL fixture
pattern. Expectations are deliberately minimum thresholds (not exact counts)
so a slip revision doesn't break the suite — regressions in *coverage*
(a sheet stops extracting, rates disappear, participation stops parsing) do.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services.placement_slip_parser import (
    parse_participation,
    parse_placement_slip,
)
from app.services.slip_reconcile import reconcile_slip

REFERENCE_DIR = Path(
    os.environ.get(
        "INSPRO_REFERENCE_SLIPS",
        str(Path(__file__).resolve().parents[2] / "reference"),
    )
)

# Defense against ever committing the real workbooks: the suite only reads
# from a directory OUTSIDE tests/fixtures.
assert "fixtures" not in str(REFERENCE_DIR)


@dataclass(frozen=True)
class SheetExpect:
    code: str
    family: str
    min_categories: int = 1
    # Minimum # categories carrying a pricing signal (rate/tiers/premium).
    # 0 = the sheet legitimately states no numeric rates ("Pending",
    # "Part of GHS", premium included in a sibling sheet).
    min_rated: int = 1
    # Allowed rate_basis values (subset check on the bases actually produced).
    bases: frozenset[str] = frozenset()
    min_plans: int = 0
    expects_voluntary_bands: bool = False
    min_dependant_scope: int = 0
    expects_location_scope: bool = False


def _sheet(code, family, **kw) -> SheetExpect:
    return SheetExpect(code=code, family=family, **kw)


WORKBOOKS: dict[str, dict[str, SheetExpect]] = {
    "Placement Slips 2026.xls": {  # CDL
        "GTL": _sheet("GTL", "si_based", min_categories=25, min_rated=20,
                      bases=frozenset({"per_1000_si"}),
                      expects_voluntary_bands=True, min_dependant_scope=6),
        "GCI - Additional": _sheet("GCI", "si_based", min_categories=25,
                                   min_rated=20, bases=frozenset({"per_1000_si"}),
                                   expects_voluntary_bands=True),
        # 16 = 4 compulsory + 12 voluntary upgrade/downgrade pairs, including
        # the plan-code-only continuation rows (SM → D01/D02/D03, Clerical →
        # U01/U02/U03, …) that a regression would silently drop again.
        "GHS": _sheet("GHS", "plan_tier", min_categories=16, min_rated=16,
                      bases=frozenset({"tiered"}), min_plans=4),
        "GMM": _sheet("GMM", "plan_tier", min_categories=16, min_rated=16,
                      bases=frozenset({"tiered"}), min_plans=4),
        "GCGP": _sheet("GCGP", "plan_tier", min_categories=6, min_rated=6,
                       bases=frozenset({"flat", "per_member"})),
        "GCSP": _sheet("GCSP", "plan_tier", min_categories=6, min_rated=6,
                       bases=frozenset({"flat", "per_member"})),
        "GD": _sheet("GD", "plan_tier", min_categories=2, min_rated=2),
        "GPA": _sheet("GPA", "si_based", min_categories=25, min_rated=25,
                      bases=frozenset({"per_1000_si"}), min_dependant_scope=6,
                      expects_location_scope=True),
        "GBT": _sheet("GBT", "travel", min_categories=1, min_rated=1,
                      bases=frozenset({"annual_flat"})),
        "OSI": _sheet("OSI", "named_person", min_categories=1, min_rated=1,
                      bases=frozenset({"tiered"})),
        "WICI": _sheet("WICI", "earnings", min_categories=4, min_rated=4,
                       bases=frozenset({"earnings_based"})),
    },
    "Placement Slips - CBRE Group (2025-2026).xls": {
        "GTL": _sheet("GTL", "si_based", min_categories=3, min_rated=3,
                      bases=frozenset({"per_1000_si"})),
        "GDD ": _sheet("GDD", "si_based", min_rated=1,
                       bases=frozenset({"per_1000_si"})),
        "GHS": _sheet("GHS", "plan_tier", min_categories=5, min_rated=5,
                      bases=frozenset({"tiered"}), min_plans=3),
        "GMM": _sheet("GMM", "plan_tier", min_categories=10, min_rated=10,
                      bases=frozenset({"tiered"})),
        "GP": _sheet("GP", "plan_tier", min_categories=5, min_rated=5),
        "SP": _sheet("SP", "plan_tier", min_categories=5, min_rated=5),
        "Dental": _sheet("DENTAL", "plan_tier", min_rated=1),
        "GPA": _sheet("GPA", "si_based", min_categories=3, min_rated=2,
                      bases=frozenset({"per_1000_si"})),
    },
    "Placement Slips - CBRE MCST  (2025-2026).xlsx": {
        "GTL": _sheet("GTL", "si_based", bases=frozenset({"per_1000_si"})),
        "GDD ": _sheet("GDD", "si_based", bases=frozenset({"per_1000_si"})),
        "GHS": _sheet("GHS", "plan_tier", bases=frozenset({"tiered"})),
        "GMM": _sheet("GMM", "plan_tier", bases=frozenset({"tiered"})),
        "GPA": _sheet("GPA", "si_based", bases=frozenset({"per_1000_si"})),
    },
    "STMicroelectronics - Placement Slips 2026_workingfile (1).xls": {
        "GEL-GTL": _sheet("GTL", "si_based", min_categories=4, min_rated=4,
                          bases=frozenset({"per_1000_si"})),
        "GEL-GHS": _sheet("GHS", "plan_tier", min_categories=6, min_rated=6,
                          bases=frozenset({"tiered"}), min_plans=6),
        "GEL-GMM": _sheet("GMM", "plan_tier", min_categories=3, min_rated=3,
                          bases=frozenset({"tiered"})),
        # SP is bundled into GHS ("Part of GHS", $0) — no numeric rates exist.
        "GEL-SP": _sheet("SP", "plan_tier", min_categories=3, min_rated=0),
        "Zurich-GPA": _sheet("GPA", "si_based", min_categories=4, min_rated=4,
                             bases=frozenset({"per_1000_si"})),
        " Chubb -GBT": _sheet("GBT", "travel", min_rated=1),
        "Allianz-WICI": _sheet("WICI", "earnings", min_categories=7,
                               min_rated=7, bases=frozenset({"earnings_based"})),
    },
    "VDL - Placement Slips 2026 (as at 13 Apr 2026).xls": {
        "GTL ": _sheet("GTL", "si_based", min_categories=2, min_rated=2,
                       bases=frozenset({"per_1000_si"})),
        "GHS - Locals": _sheet("GHS-LOCALS", "plan_tier", min_categories=6,
                               min_rated=6, bases=frozenset({"tiered"}),
                               min_plans=5),
        # Secondees premium is included in the Locals figures — no rate table.
        "GHS - Secondees": _sheet("GHS-SECONDEES", "plan_tier",
                                  min_categories=2, min_rated=0),
        "GHS - Dependants": _sheet("GHS-DEPENDANTS", "plan_tier",
                                   min_categories=6, min_rated=6,
                                   bases=frozenset({"tiered"}),
                                   min_dependant_scope=6),
        "GMM": _sheet("GMM", "plan_tier", min_categories=4, min_rated=4,
                      bases=frozenset({"tiered"})),
        "GCGP": _sheet("GCGP", "plan_tier", min_categories=6, min_rated=6),
        "GCSP": _sheet("GCSP", "plan_tier", min_categories=6, min_rated=6),
        "GPA": _sheet("GPA", "si_based", min_categories=2, min_rated=2,
                      bases=frozenset({"per_1000_si"})),
        "GBT": _sheet("GBT", "travel", min_rated=1),
        "WICA": _sheet("WICA", "earnings", min_categories=10, min_rated=10,
                       bases=frozenset({"earnings_based"})),
    },
    "Hartree Partners & CHC Energy - Placement slips 2026 - 2027 (1).xlsx": {
        "GTL": _sheet("GTL", "si_based", bases=frozenset({"per_1000_si"})),
        "GCI": _sheet("GCI", "si_based", bases=frozenset({"per_1000_si"})),
        "GHS": _sheet("GHS", "plan_tier", min_categories=2, min_rated=2,
                      bases=frozenset({"tiered"})),
        "GP": _sheet("GP", "plan_tier", min_categories=2, min_rated=2),
        "SP": _sheet("SP", "plan_tier", min_categories=2, min_rated=2),
        "Dental": _sheet("DENTAL", "plan_tier", min_categories=2, min_rated=1),
        "GBT": _sheet("GBT", "travel", min_rated=1),
        # Rates read "Pending" / #VALUE! at source — nothing numeric to extract.
        "WICA": _sheet("WICA", "earnings", min_categories=3, min_rated=0),
    },
    "Papua New Guinea - Placement Slips 2026.xlsx": {
        "GTL": _sheet("GTL", "si_based", bases=frozenset({"per_1000_si"})),
        "GHS": _sheet("GHS", "plan_tier", bases=frozenset({"tiered"})),
        "GCGP": _sheet("GCGP", "plan_tier"),
        "GCSP": _sheet("GCSP", "plan_tier"),
        "GD": _sheet("GD", "plan_tier"),
        "GPA": _sheet("GPA", "si_based", bases=frozenset({"per_1000_si"})),
        "WICI": _sheet("WICI", "earnings",
                       min_categories=2, min_rated=2,
                       bases=frozenset({"earnings_based"})),
    },
}

_PARSED: dict[str, object] = {}


def _parse(filename: str):
    path = REFERENCE_DIR / filename
    if not path.exists():
        # CDL's root workbook also exists as a copy under CDL/.
        alt = REFERENCE_DIR / "CDL" / filename
        path = alt if alt.exists() else path
    if not path.exists():
        pytest.skip(f"reference workbook absent: {filename}")
    if filename not in _PARSED:
        _PARSED[filename] = parse_placement_slip(str(path), client_label="acceptance")
    return _PARSED[filename]


def _rated(cat) -> bool:
    return (
        cat.premium_rate is not None
        or bool(cat.rate_tiers)
        or cat.annual_premium is not None
    )


@pytest.mark.parametrize("filename", sorted(WORKBOOKS))
def test_workbook_extracts_every_expected_sheet(filename: str) -> None:
    slip = _parse(filename)
    by_sheet = {p.sheet: p for p in slip.products}
    missing = [s for s in WORKBOOKS[filename] if s not in by_sheet]
    assert not missing, f"{filename}: sheets not extracted: {missing}"


@pytest.mark.parametrize(
    ("filename", "sheet"),
    [(f, s) for f in sorted(WORKBOOKS) for s in WORKBOOKS[f]],
)
def test_sheet_extraction_quality(filename: str, sheet: str) -> None:
    slip = _parse(filename)
    expect = WORKBOOKS[filename][sheet]
    product = next((p for p in slip.products if p.sheet == sheet), None)
    assert product is not None, f"sheet {sheet!r} not extracted"

    assert product.product_code == expect.code
    assert product.layout_family == expect.family
    assert product.registry_known is True

    cats = product.categories
    assert len(cats) >= expect.min_categories, (
        f"{len(cats)} categories < {expect.min_categories}"
    )

    rated = [c for c in cats if _rated(c)]
    assert len(rated) >= expect.min_rated, (
        f"{len(rated)} rated categories < {expect.min_rated}"
    )
    if expect.bases:
        bases = {c.rate_basis for c in cats if c.rate_basis}
        assert bases & expect.bases, f"bases {bases} lack {expect.bases}"

    assert len(product.plans) >= expect.min_plans

    if expect.expects_voluntary_bands:
        assert len(product.voluntary_rates) >= 3
        assert all(
            isinstance(b.get("rate"), (int, float)) for b in product.voluntary_rates
        )

    dep = [c for c in cats if c.member_scope == "dependant"]
    assert len(dep) >= expect.min_dependant_scope

    if expect.expects_location_scope:
        assert any(c.location_scope for c in cats)

    # Participation sanity: every stated participation cell parses to an
    # employee or dependant mode — no silently-dropped modes.
    for c in cats:
        if not c.participation.strip():
            continue
        spec = parse_participation(c.participation)
        assert spec.employee is not None or spec.dependant is not None, (
            f"unparsed participation {c.participation!r} on {c.category!r}"
        )


@pytest.mark.parametrize("filename", sorted(WORKBOOKS))
def test_reconcile_flags_nothing_unknown(filename: str) -> None:
    """Every sheet in the reference set resolves to a registry-known product —
    none may fall into the needs_classification path."""
    slip = _parse(filename)
    rec = reconcile_slip(slip)
    unknown = [d.product_code for d in rec.diagnostics if d.needs_classification]
    assert not unknown, f"{filename}: unclassified products {unknown}"
