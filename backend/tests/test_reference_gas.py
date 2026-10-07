"""Acceptance checks against private local files; no employee records are committed."""

from datetime import date
from pathlib import Path

import pytest

from app.services.placement_slip_parser import parse_placement_slip
from app.services.slip_reconcile import reconcile_slip
from app.services.slip_variants import assign_variants

SOURCE = Path(__file__).resolve().parents[2] / "reference/GAS/Placement Slips 2026 (5).xls"


@pytest.mark.skipif(not SOURCE.exists(), reason="private GAS reference absent")
def test_gas_category_plan_rate_and_depot_boundaries() -> None:
    parsed = parse_placement_slip(SOURCE, client_label="acceptance", rate_date=date(2026, 10, 1))
    result = reconcile_slip(assign_variants(parsed))
    by_code = {p.product_code: p for p in result.slip.products}
    assert {code: len(p.categories) for code, p in by_code.items()} == {
        "GTL": 4,
        "GPA": 4,
        "GHS": 4,
        "GP": 1,
        "SP": 2,
        "WICA-EASTCOAST": 4,
        "WICA-LOYANG": 4,
    }
    assert len(by_code["SP"].plans) == 2
    assert by_code["GP"].categories[0].premium_rate == 615
    assert by_code["GP"].categories[0].annual_premium == 622995
    assert [c.premium_rate for c in by_code["SP"].categories] == [109, 104]
    assert all(c.rate_tiers for c in by_code["GHS"].categories)
    assert all(c.premium_rate == 2.12 for c in by_code["GTL"].categories)
    assert by_code["GPA"].extraction_issues
    assert all(c.premium_rate is None for c in by_code["GPA"].categories)
    # Depots split headcount/SI only; each cohort is one category for both.
    gpa = by_code["GPA"].categories
    assert all(c.location_scope is None for c in gpa)
    assert [c.num_employees for c in gpa] == [4, 117, 89, 1698]
    assert all(
        {b["location"] for b in c.location_breakdown} == {"Loyang Depot", "East Coast Depot"}
        for c in gpa
    )
    assert [p.display_name for p in by_code["GTL"].plans] == [
        "Directors",
        "All Professionals, Executives & Management Staff",
        "All Other Staff (excl. Bus Drivers)",
        "All Bus Drivers",
    ]
    for code, depot in [("WICA-EASTCOAST", "East Coast Depot"), ("WICA-LOYANG", "Loyang Depot")]:
        assert all(c.location_scope == depot for c in by_code[code].categories)
        assert all(
            c.insured == by_code[code].policy_header.insured for c in by_code[code].categories
        )
        assert all(c.source_insured and depot in c.source_insured for c in by_code[code].categories)
        assert all(c.premium_rate is not None for c in by_code[code].categories)


DIRECTORS = SOURCE.with_name("Placement Slips 2026 - Director.xls")


@pytest.mark.skipif(not DIRECTORS.exists(), reason="private GAS directors reference absent")
def test_gas_directors_slip_prices_combined_tier_column() -> None:
    parsed = parse_placement_slip(DIRECTORS, client_label="acceptance", rate_date=date(2026, 10, 1))
    by_code = {p.product_code: p for p in reconcile_slip(assign_variants(parsed)).slip.products}
    ghs = by_code["GHS"].categories[0]
    # "ES / EC" is one rate column pricing both tiers; its premium counts once.
    assert ghs.rate_tiers["ES"]["rate"] == ghs.rate_tiers["EC"]["rate"] == 4700
    assert ghs.rate_tiers["EC"]["premium"] == 0
    assert ghs.annual_premium == 14100 + 7311
    assert by_code["GP"].policy_header.policy_no == "50011774"
