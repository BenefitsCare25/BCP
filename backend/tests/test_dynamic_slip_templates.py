"""Template variation and unsafe inference regressions; all data is synthetic."""

from datetime import date

import pytest

from app.services.excel_reader import Cell
from app.services.placement_slip_sob import _detect_plan_columns, _find_sob_section
from app.services.slip_parsing.models import ExtractedCategory
from app.services.slip_parsing.rates import (
    _enrich_with_rates,
    _RateRow,
    extract_rate_schedules,
)
from app.services.slip_parsing.walk import _identify_columns, _walk_data_rows


def test_company_labelled_cohorts_keep_grade_and_pass_separate() -> None:
    from app.services.eligibility_mapping import AttributeValueCatalog, propose_category_rule

    values = ["A and B - MSO Grade", "C and D - SP Grade", "C and D - SPass and WP only"]
    catalog = AttributeValueCatalog(
        values={"category": values, "pass": ["SP", "WP"]},
        data_types={"category": "string", "pass": "string"},
        populated={"category": 3, "pass": 2},
        employee_count=3,
    )
    for description, expected in zip(
        [
            "All Other Employees (MSO Grade)",
            "All Other Employees (SP Grade)",
            "All Other Employees (S-Pass & WP only)",
        ],
        values,
        strict=True,
    ):
        result = propose_category_rule(description, catalog)
        assert result.rule == {"=": ["category", expected]}
    assert propose_category_rule("All Other Employees (Unknown Grade)", catalog).rule is None
    assert propose_category_rule("All Employees (incl. S-Pass & WP)", catalog).rule == {"and": []}
    assert propose_category_rule(
        "All Others Employees (incl. S-Pass & WP)", catalog
    ).relative_remainder
    assert propose_category_rule("All Other Staff (excl. Bus Drivers)", catalog).rule is None


def test_value_maps_are_company_scoped_and_leave_unknown_values_unmapped() -> None:
    from app.models import EmployeeAttributeSchema
    from app.services.derivation_engine import derive

    default = EmployeeAttributeSchema(
        attribute_id="work_location",
        client_id=None,
        derivation_rule={"op": "passthrough", "source": "depot"},
    )
    specific = EmployeeAttributeSchema(
        attribute_id="work_location",
        client_id="company-a",
        derivation_rule={
            "op": "value_map",
            "source": "depot",
            "unmapped": "omit",
            "mappings": [{"from": "North", "to": "Main Depot"}],
        },
    )
    assert derive({"depot": " NORTH "}, [default, specific]) == {"work_location": "Main Depot"}
    assert derive({"depot": "South"}, [default, specific]) == {}
    assert derive({"depot": "North"}, [default]) == {"work_location": "North"}


def test_product_entity_selection_cannot_widen_a_category() -> None:
    from app.models import Category
    from app.services.matching_engine import _entity_allows, category_entity_gate

    category = Category(plan_assignments={"insured": ["Subsidiary A"]})
    gate = category_entity_gate(category, frozenset({"subsidiary a", "subsidiary b"}))
    assert _entity_allows(gate, frozenset({"subsidiary a"}))
    assert not _entity_allows(gate, frozenset({"subsidiary b"}))
    assert not _entity_allows(gate, frozenset())
    disjoint = category_entity_gate(category, frozenset({"subsidiary b"}))
    assert not _entity_allows(disjoint, frozenset({"subsidiary b"}))


def test_separate_periods_become_variants_and_same_policy_sheets_are_unioned() -> None:
    from dataclasses import replace

    from app.services.slip_parsing.models import PlacementSlip, PolicyHeader, ProductSlip
    from app.services.slip_to_setup import merge_product_sheets
    from app.services.slip_variants import assign_variants

    first = ProductSlip(
        "WICI (North)",
        "WICI",
        PolicyHeader(insured="Example Ltd", period="01/07/2026 to 30/06/2027"),
        (ExtractedCategory("Example Ltd", "Technical", "Compulsory", "1", 20),),
    )
    second = replace(
        first,
        sheet="WICI (South)",
        policy_header=replace(first.policy_header, period="01/10/2026 to 30/09/2027"),
    )
    variants = assign_variants(PlacementSlip("example", (first, second))).products
    assert len({p.product_code for p in variants}) == 2
    merged = merge_product_sheets([first, replace(second, policy_header=first.policy_header)])
    assert len(merged.categories) == 2
    assert {c.source_sheet for c in merged.categories} == {first.sheet, second.sheet}


def test_duplicate_premium_headers_do_not_shift_plan_key_into_total() -> None:
    rows: list[list[Cell]] = [
        ["Rates for 2026/2027"],
        ["Insured", "Plan", "Premium", "Rate", "Premium"],
        ["Example Ltd", 1, 10, 120, 1200],
        ["Total Annual Premium", None, None, None, 1200],
    ]
    rates, _, _, _ = extract_rate_schedules(rows, effective_date=date(2026, 10, 1))
    assert rates[0].key == "1"
    assert rates[0].annual_premium == 1200


@pytest.mark.parametrize("offset", [0, 1, 4])
@pytest.mark.parametrize(
    "boundary",
    [
        ["* For illustration only. Actual headcount/name for billing to be provided."],
        ["Maximum Limit per Insured Person :", 1600000],
        ["Rates for 2026/2027"],
        ["Premium Rate & Total Annual Premium"],
        ["Insured", "Category", "Estimated Annual Earnings", "Rate", "Premium"],
    ],
)
def test_basis_stops_before_other_sections(offset: int, boundary: list[Cell]) -> None:
    rows: list[list[Cell]] = [
        ["Insured", "Category", "Participation", "Basis", "Headcount"],
        ["Example Ltd", "Operations", "Compulsory", 10000, 12],
        boundary,
        ["Example Ltd", "Operations", 120000, 2.5, 300],
    ]
    rows = [[None] * offset + row for row in rows]
    categories = _walk_data_rows(rows, 0, _identify_columns(rows[0]))
    assert len(categories) == 1
    assert categories[0].num_employees == 12


@pytest.mark.parametrize("plan_column", [2, 3, 5, 11])
def test_plan_headers_are_position_independent(plan_column: int) -> None:
    rows: list[list[Cell]] = [
        ["SCHEDULE OF BENEFITS"] + [None] * (plan_column - 1) + ["PLAN 1", "PLAN 2"]
    ]
    assert _find_sob_section(rows) == 0
    assert _detect_plan_columns(rows, 0) == [
        ("1", "PLAN 1", plan_column),
        ("2", "PLAN 2", plan_column + 1),
    ]


def _dated_rows() -> list[list[Cell]]:
    return [
        ["Rates for 2025/2026"],
        ["Insured", "Plan", "Headcount", "Rate", "Premium"],
        ["Example Ltd", 1, 10, 100, 1000],
        ["Total Annual Premium", None, None, None, 1000],
        ["Rates for 2026/2027"],
        ["Insured", "Plan", "Headcount", "Rate", "Premium"],
        ["Example Ltd", 1, 10, 120, 1200],
        ["Total Annual Premium", None, None, None, 1200],
    ]


@pytest.mark.parametrize(
    ("effective", "expected"),
    [
        (date(2025, 10, 1), 100),
        (date(2026, 9, 30), 100),
        (date(2026, 10, 1), 120),
    ],
)
def test_rates_use_policy_anniversary_and_keep_both_source_tables(
    effective: date,
    expected: int,
) -> None:
    rates, _, schedules, issues = extract_rate_schedules(
        _dated_rows(), effective_date=effective, policy_period="01/10/2025 to 30/09/2027"
    )
    assert not issues
    assert len(rates) == 1 and rates[0].rate == expected
    assert len(schedules) == 2
    assert sum(s["selected"] for s in schedules) == 1
    assert schedules[0]["end_date"] == "2026-09-30"
    assert schedules[1]["rates"][0]["rate"] == 120


@pytest.mark.parametrize("effective", [None, date(2028, 1, 1)])
def test_rates_never_choose_an_ambiguous_or_expired_schedule(effective: date | None) -> None:
    rates, _, schedules, issues = extract_rate_schedules(_dated_rows(), effective_date=effective)
    assert rates == [] and issues and len(schedules) == 2


def test_untitled_earnings_table_is_detected() -> None:
    rows: list[list[Cell]] = [
        ["Insured", "Category", "Participation", "Headcount", "Estimated Annual Earnings"],
        ["Example Ltd", "Technical", "Compulsory", 10, 250000],
        [],
        ["Insured", "Category", "Estimated Annual Earnings", "Rate", "Premium"],
        ["Example Ltd", "Technical", 250000, 0.005, 1250],
    ]
    rates, _, _, issues = extract_rate_schedules(rows)
    assert not issues and len(rates) == 1
    assert rates[0].rate_basis == "earnings_based"
    assert rates[0].estimated_annual_earnings == 250000


def test_rates_cannot_leak_between_insured_entities() -> None:
    category = ExtractedCategory("Branch C", "Technical", "Compulsory", "1", 4)
    rates = [
        _RateRow("Technical", "flat", rate=100, insured="Branch A"),
        _RateRow("Technical", "flat", rate=200, insured="Branch B"),
    ]
    assert _enrich_with_rates((category,), rates)[0].premium_rate is None


def test_subsidiary_names_are_not_policy_aliases_by_containment() -> None:
    from app.services.slip_parsing.models import PlacementSlip, PolicyHeader, ProductSlip
    from app.services.slip_variants import assign_variants

    products = tuple(
        ProductSlip(
            sheet=f"GHS ({name})",
            product_code="GHS",
            categories=(),
            policy_header=PolicyHeader(insured=f"{name} Ltd", insurer="Insurer"),
        )
        for name in ["Example", "Example Energy"]
    )
    result = assign_variants(PlacementSlip("company", products))
    assert len({p.product_code for p in result.products}) == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [(50011771.0, "50011771"), ("000123", "000123"), ("POL-123.0", "POL-123.0")],
)
def test_policy_number_keeps_literal_codes_without_excel_numeric_suffix(
    value: Cell, expected: str
) -> None:
    from app.services.slip_parsing.header import _scan_policy_header

    scan = _scan_policy_header([["Policy No:", value]])
    assert scan.header.policy_no == expected
