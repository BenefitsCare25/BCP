"""Reconciliation: every category's plan_code must resolve to a real plan.

Covers the four shapes seen across the reference corpus — descriptive fan-out,
composite-code split, assign-default, and the genuinely-unmappable / no-plans
cases that must be flagged (not silently broken).
"""
from __future__ import annotations

from app.services.placement_slip_parser import (
    ExtractedBenefitItem,
    ExtractedCategory,
    ExtractedPlan,
    PlacementSlip,
    PolicyHeader,
    ProductSlip,
)
from app.services.slip_reconcile import reconcile_slip


def _cat(category: str, plan_code: str) -> ExtractedCategory:
    return ExtractedCategory(
        insured="ACME", category=category, participation="Compulsory",
        plan_code=plan_code, source_row=1,
    )


def _plan(code: str, name: str = "Schedule of Benefits") -> ExtractedPlan:
    return ExtractedPlan(code=code, display_name=name, items=())


def _slip(product: ProductSlip) -> PlacementSlip:
    return PlacementSlip(client="t", products=(product,))


def _product(code: str, cats, plans, sob_fingerprint=None) -> ProductSlip:
    return ProductSlip(
        sheet=code, product_code=code, policy_header=PolicyHeader(),
        categories=tuple(cats), plans=tuple(plans), sob_fingerprint=sob_fingerprint,
    )


def _no_dangling(rec) -> bool:
    for ps in rec.slip.products:
        codes = {p.code.strip() for p in ps.plans}
        for c in ps.categories:
            pc = (c.plan_code or "").strip()
            if pc and pc not in codes:
                return False
    return True


def test_descriptive_fan_out_creates_a_plan_per_code() -> None:
    # GTL: 4 grade-band categories cite Plan A/B/C/D, one shared schedule.
    prod = _product(
        "GTL",
        [_cat("Grade 16+", "A"), _cat("Grade 8-15", "B"),
         _cat("Bargainable", "C"), _cat("Non-barg", "D")],
        [_plan("1")],
    )
    rec = reconcile_slip(_slip(prod))
    d = rec.diagnostics[0]
    assert d.reconciliation == "fan_out"
    assert d.n_plans == 4
    assert {p.code for p in rec.slip.products[0].plans} == {"A", "B", "C", "D"}
    assert _no_dangling(rec)


def test_composite_plan_code_is_split() -> None:
    # CBRE GHS: plan column header "1A/1B" covers categories 1A and 1B.
    prod = _product(
        "GHS",
        [_cat("Band 1A", "1A"), _cat("Band 1B", "1B"), _cat("Band 3", "3")],
        [_plan("1A/1B", "Plan 1"), _plan("3", "Plan 3")],
    )
    rec = reconcile_slip(_slip(prod))
    assert rec.diagnostics[0].reconciliation == "fan_out"
    assert {p.code for p in rec.slip.products[0].plans} == {"1A", "1B", "3"}
    assert _no_dangling(rec)


def test_assign_default_when_categories_have_no_code() -> None:
    prod = _product(
        "WICA", [_cat("Manual workers", ""), _cat("Non-manual", "")], [_plan("1")]
    )
    rec = reconcile_slip(_slip(prod))
    assert rec.diagnostics[0].reconciliation == "assign_default"
    assert all(c.plan_code == "1" for c in rec.slip.products[0].categories)
    assert _no_dangling(rec)


def test_empty_schedule_of_benefits_is_flagged() -> None:
    # A SOB section was located on the sheet (fingerprint present) but extraction
    # produced zero benefit lines — the GBT-style failure where the column layout
    # went unrecognized (the parser drops zero-item plans, so plans=()). It must
    # surface as needs_attention + empty_sob so the column-mapping fixer appears.
    prod = _product("GHS", [_cat("All staff", "1")], [], sob_fingerprint="fp123")
    d = reconcile_slip(_slip(prod)).diagnostics[0]
    assert d.n_benefit_items == 0
    assert d.empty_sob is True
    assert d.needs_attention is True
    assert d.fingerprint == "fp123"
    assert any("no benefit lines were extracted" in i for i in d.issues)


def test_no_sob_section_is_not_flagged_empty_sob() -> None:
    # No SOB section at all (no fingerprint) is NOT empty_sob — there are no
    # columns to correct; it's the ordinary no_plans case.
    prod = _product("GHS", [_cat("All staff", "1")], [], sob_fingerprint=None)
    d = reconcile_slip(_slip(prod)).diagnostics[0]
    assert d.empty_sob is False
    assert d.reconciliation == "no_plans"


def test_populated_schedule_of_benefits_not_flagged_empty() -> None:
    item = ExtractedBenefitItem(number="1", name="Daily Room & Board", value="1 Bed")
    prod = _product(
        "GHS", [_cat("All staff", "1")],
        [ExtractedPlan(code="1", display_name="Plan 1", items=(item,))],
        sob_fingerprint="fp123",
    )
    d = reconcile_slip(_slip(prod)).diagnostics[0]
    assert d.n_benefit_items == 1
    assert d.empty_sob is False
    assert d.needs_attention is False


def test_per_plan_consistent_is_unchanged() -> None:
    prod = _product(
        "GHS", [_cat("A", "1"), _cat("B", "2"), _cat("C", "3")],
        [_plan("1"), _plan("2"), _plan("3")],
    )
    rec = reconcile_slip(_slip(prod))
    assert rec.diagnostics[0].reconciliation == "consistent"
    assert rec.diagnostics[0].n_plans == 3
    assert _no_dangling(rec)


def test_unmappable_is_flagged_not_fabricated() -> None:
    # Category cites a code no plan header covers — must be flagged for review.
    prod = _product(
        "GCGP", [_cat("Band B3", "B3")], [_plan("A"), _plan("B1 / B2")]
    )
    rec = reconcile_slip(_slip(prod))
    d = rec.diagnostics[0]
    assert d.reconciliation == "unmappable"
    assert d.needs_attention is True
    assert any("B3" in i for i in d.issues)


def test_no_plans_is_flagged() -> None:
    prod = _product("GBT", [_cat("All staff on travel", "1")], [])
    rec = reconcile_slip(_slip(prod))
    d = rec.diagnostics[0]
    assert d.reconciliation == "no_plans"
    assert d.needs_attention is True
    assert d.n_plans == 0


def test_unreferenced_plans_are_preserved() -> None:
    # A plan with no category pointing at it must not be dropped.
    prod = _product("GHS", [_cat("Band 1", "1")], [_plan("1"), _plan("2")])
    rec = reconcile_slip(_slip(prod))
    assert {p.code for p in rec.slip.products[0].plans} == {"1", "2"}


# ── Corpus invariant ─────────────────────────────────────────────────────────
# Across every real slip we can find, a category may only dangle (cite a plan
# code with no matching Plan) when its product is explicitly flagged for
# attention. A "consistent"/"fan_out"/"assign_default" product must never leave a
# silently broken link. Reference files are git-ignored PII — skip when absent.
import glob  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from app.services.placement_slip_parser import parse_placement_slip  # noqa: E402

_CORPUS_DIRS = [
    Path(__file__).parent / "fixtures" / "placement_slips",
    Path("C:/Users/huien/inspro/reference"),
]


def _corpus_files() -> list[str]:
    files: list[str] = []
    for d in _CORPUS_DIRS:
        if d.exists():
            for f in glob.glob(str(d / "*.xls*")):
                if "Upload Template" not in os.path.basename(f):
                    files.append(f)
    return files


@pytest.mark.parametrize("path", _corpus_files())
def test_corpus_no_silent_dangling_links(path: str) -> None:
    rec = reconcile_slip(parse_placement_slip(path, client_label="t"))
    diag_by_sheet = {d.sheet: d for d in rec.diagnostics}
    for ps in rec.slip.products:
        codes = {p.code.strip() for p in ps.plans}
        diag = diag_by_sheet[ps.sheet]
        for c in ps.categories:
            pc = (c.plan_code or "").strip()
            if pc and pc not in codes:
                # Dangling is only allowed when the product is flagged for review.
                assert diag.needs_attention, (
                    f"{os.path.basename(path)} :: {ps.product_code} category "
                    f"{c.category[:30]!r} dangles on plan {pc!r} but product is "
                    f"not flagged (reconciliation={diag.reconciliation})"
                )


def test_corpus_is_present() -> None:
    # Guard against the parametrized test silently collecting zero cases.
    if not _corpus_files():
        pytest.skip("no slip corpus available")
    assert _corpus_files()
