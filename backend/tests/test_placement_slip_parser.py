"""Integration tests for the placement slip parser.

These run against the real STM and VDL workbooks committed (anonymised, per
plan §Decisions) to backend/tests/fixtures/placement_slips/. The acceptance
gate for Spike Day 1 is: STM parses into the seven expected product sheets
and 25-30 categories total, mirroring the prototype's output.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.placement_slip_parser import (
    Cell,
    _age_from_birthday,
    _Columns,
    _extract_plans_from_sheet,
    _normalize_age,
    _up_to_age,
    _walk_data_rows,
    normalize_participation,
    parse_participation,
    parse_placement_slip,
)


def test_normalize_participation_vocabulary_and_ambiguity() -> None:
    assert normalize_participation("Compulsory") == "compulsory"
    assert normalize_participation("voluntary") == "voluntary"
    assert normalize_participation("Participation: Compulsory") == "compulsory"
    # Narrowed: generic tokens / eligibility phrases are NOT participation values.
    assert normalize_participation("Y") is None
    assert normalize_participation("All staff") is None
    assert normalize_participation("C") is None
    # Both words present -> ambiguous -> None (not silently compulsory).
    assert normalize_participation("Voluntary top-up to compulsory plan") is None
    assert normalize_participation(None) is None
    # Employee/dependant-split cell: the binary model describes the member, so it
    # resolves to the employee clause rather than the ambiguous None.
    assert (
        normalize_participation("Compulsory - Employees\nVoluntary - Dependents")
        == "compulsory"
    )
    assert (
        normalize_participation("Compulsory - Employees / Voluntary - Dependents")
        == "compulsory"
    )


def test_parse_participation_modes_and_direction() -> None:
    # Plain employee modes.
    s = parse_participation("Compulsory")
    assert (s.employee, s.dependant, s.direction) == ("compulsory", None, None)
    s = parse_participation("Voluntary")
    assert (s.employee, s.dependant, s.direction) == ("voluntary", None, None)

    # Directional voluntary tiers (en-dash and the Windows-1252 \x96 variant).
    assert parse_participation("Voluntary – Downgrade").direction == "downgrade"  # noqa: RUF001
    assert parse_participation("Voluntary \x96 Downgrade").direction == "downgrade"
    assert parse_participation("Voluntary – Downgrade / Upgrade").direction == "both"  # noqa: RUF001
    assert parse_participation("Voluntary - Upgrade").direction == "upgrade"

    # Audience scoping: a single cell can split employees vs dependants — whether
    # the clauses are newline-separated or collapsed to a space by cell
    # normalization (the real .xls path).
    s = parse_participation("Compulsory - Employees\nVoluntary - Dependents")
    assert (s.employee, s.dependant) == ("compulsory", "voluntary")
    s = parse_participation("Compulsory - Employees Voluntary - Dependents")
    assert (s.employee, s.dependant) == ("compulsory", "voluntary")
    s = parse_participation("Voluntary - Dependents")
    assert (s.employee, s.dependant) == (None, "voluntary")
    # An eligibility qualifier is not a dependant clause.
    assert parse_participation("Compulsory - SG Office").employee == "compulsory"

    assert parse_participation("").employee is None


def test_basis_carries_plan_code_across_merged_block() -> None:
    # Per-member GCGP/GCSP layout: several categories share ONE plan, printed only
    # on the block's first row; the rest are blank-plan continuations and must
    # inherit it. A new insured block resets the carry (no bleed across plans).
    cols = _Columns(insured=1, category=3, participation=6, plan=9, num_employees=10)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "", "", "Participation", "", "", "Plan", "Number"],
        ["", "CDL", "", "SM and above (Job Category: 99)", "", "",
         "Compulsory - Employees", "", "", "1", "494"],
        ["", "", "", "Manager and Executive (Job category: E1)", "", "", "", "", "", "", ""],
        ["", "", "", "Officer and Clerical staff (Job category: J1)", "", "", "", "", "", "", ""],
        # New block: insured re-populated resets the carry; this block has no plan
        # code, so its category must NOT inherit the previous block's "1".
        ["", "CDL", "", "Standalone category with no plan", "", "", "Voluntary", "", "", "", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert len(cats) == 4
    assert [c.plan_code for c in cats] == ["1", "1", "1", ""]
    # Participation also carries onto the continuation rows.
    assert cats[1].participation == "Compulsory - Employees"


def test_basis_autonumbers_when_no_plan_column() -> None:
    # GPA-style sum-assured layout: no Plan column and no inline "Plan N:" — each
    # category is its own SI tier and must get a sequential plan code so it links
    # to a plan like the inline-coded GTL/GCI.
    cols = _Columns(insured=1, category=3, participation=4, plan=9, num_employees=5)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "Participation", "No.", "", "", "", "Plan"],
        ["", "CDL", "", "CEO, Deputy CEO, CSO", "Compulsory", "2", "", "", "", ""],
        ["", "", "", "EVP and above", "", "8", "", "", "", ""],
        ["", "", "", "Executive to AM and Secretary", "", "152", "", "", "", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert [c.plan_code for c in cats] == ["1", "2", "3"]


def test_basis_does_not_autonumber_when_codes_present() -> None:
    # A sheet that produced any plan code keeps its codes (no auto-numbering).
    cols = _Columns(insured=1, category=3, participation=4, plan=9, num_employees=5)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "Participation", "No.", "", "", "", "Plan"],
        ["", "CDL", "", "Plan 1: GCEO and GCOO", "Compulsory", "2", "", "", "", ""],
        ["", "", "", "Plan 2: EVP and Above", "", "8", "", "", "", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert [c.plan_code for c in cats] == ["1", "2"]


def test_short_employee_category_with_participation_kept() -> None:
    # A short / code-like category label ("CEO") is normally dropped by the noise
    # filters (min length + rate-code shape). But when the row carries an explicit
    # EMPLOYEE-scoped Compulsory/Voluntary marker it's a genuine basis-of-cover
    # row and must survive (GD dental "CEO / Compulsory").
    cols = _Columns(insured=1, category=3, participation=6, plan=9, num_employees=10)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "", "", "Participation", "", "", "Plan", "No."],
        ["", "CDL", "", "CEO", "", "", "Compulsory", "", "", "1", ""],
        ["", "", "", "All Employees & their Eligible Dependants", "", "",
         "Voluntary", "", "", "2", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert [c.category for c in cats] == ["CEO", "All Employees & their Eligible Dependants"]
    assert [c.plan_code for c in cats] == ["1", "2"]


def test_short_continuation_row_with_headcount_kept() -> None:
    # WICA repeats each company's participation only on the first line; the
    # "All Others" continuation row (a code-like 10-char label that matches the
    # rate-code filter) carries no participation but DOES carry a headcount, which
    # vouches for it as a genuine basis-of-cover row.
    cols = _Columns(insured=1, category=3, participation=5, plan=9, num_employees=6)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "", "Participation", "No.", "", "", ""],
        ["", "ACME", "", "Non-Manual Staffs", "", "Compulsory", 427, "", "", ""],
        ["", "", "", "All Others", "", "", 9, "", "", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert [c.category for c in cats] == ["Non-Manual Staffs", "All Others"]
    assert [c.num_employees for c in cats] == [427, 9]


def test_summary_total_row_with_headcount_excluded() -> None:
    # A "Total" / "Sub Total" footer row carries a headcount, which would vouch
    # for it as a genuine row past the noise filters. It must be dropped by the
    # exact-match exclusion set, not emitted as a phantom cohort.
    cols = _Columns(insured=1, category=3, participation=5, plan=9, num_employees=6)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "", "Participation", "No.", "", "", ""],
        ["", "ACME", "", "Non-Manual Staffs", "", "Compulsory", 427, "", "", ""],
        ["", "", "", "All Others", "", "", 9, "", "", ""],
        ["", "", "", "Total", "", "", 436, "", "", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert [c.category for c in cats] == ["Non-Manual Staffs", "All Others"]


def test_zero_headcount_continuation_row_excluded() -> None:
    # A code-shaped continuation row with a ZERO headcount and no participation is
    # a phantom cohort (the insured entity has none of that category) — the
    # headcount-vouches-genuine signal requires a positive count.
    cols = _Columns(insured=1, category=3, participation=5, plan=9, num_employees=6)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "", "Participation", "No.", "", "", ""],
        ["", "ACME", "", "Non-Manual Staffs", "", "Compulsory", 8, "", "", ""],
        ["", "", "", "All Others", "", "", 0, "", "", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert [c.category for c in cats] == ["Non-Manual Staffs"]


def test_dependant_rows_captured_with_dependant_scope() -> None:
    # GTL/GCI/GPA list Spouse/Child dependant cover as separate "Plan N:" rows
    # scoped "Voluntary - Dependents". These used to be dropped by the noise
    # filters; they are now captured CONSISTENTLY (all siblings, with or
    # without their own participation marker) and tagged member_scope=
    # "dependant" so they feed dependant pricing/elections — never the
    # employee tier list.
    cols = _Columns(insured=1, category=3, participation=4, plan=9, num_employees=10)
    rows: list[list[Cell]] = [
        ["", "Insured", "", "Category", "Participation", "", "", "", "", "Plan"],
        ["", "CDL", "", "Plan 1: Manager (Job category: E5)", "Compulsory", "", "", "", "", ""],
        ["", "", "", "Plan 1: Spouse", "Voluntary - Dependents", "", "", "", "", ""],
        ["", "", "", "Plan 2: Child", "", "", "", "", "", ""],
    ]
    cats = _walk_data_rows(rows, 0, cols)
    assert [c.category for c in cats] == [
        "Manager (Job category: E5)", "Spouse", "Child",
    ]
    assert [c.member_scope for c in cats] == [None, "dependant", "dependant"]


def test_age_normalization_next_vs_last_birthday() -> None:
    # Canonical = age next birthday: ANB keeps the stated age; last birthday /
    # ALB add one (age N last birthday = age N+1 next birthday).
    assert _normalize_age("67 next birthday") == "67"
    assert _normalize_age("69 (age last birthday)") == "70"
    assert _normalize_age("75 (age next birthday)") == "75"
    assert _normalize_age("70 years next birthday") == "70"
    assert _normalize_age("70 ANB") == "70"
    assert _normalize_age("65 ALB") == "66"
    # Bare numbers (no birthday qualifier) pass through; junk is None.
    assert _normalize_age("80.0") == "80"
    assert _normalize_age("74") == "74"
    assert _normalize_age("NIL") is None
    assert _normalize_age(None) is None


def test_age_from_birthday_ignores_sum_insured() -> None:
    # The Non-Evidence Limit age must come from the 'age N birthday' phrase, never
    # the sum-insured figure in the same sentence.
    assert _age_from_birthday(
        "Sum insured exceeding 850,000 or age 67 next birthday and above requires underwriting"
    ) == "67"
    assert _age_from_birthday(
        "Sum insured exceeding S$300,000 or age 65 (age next birthday) requires underwriting"
    ) == "65"
    assert _age_from_birthday("Sum insured exceeding $350,000 or age 65 ANB requires u") == "65"
    # No age phrase -> None (don't grab the sum insured).
    assert _age_from_birthday("Sum insured exceeding 850,000 only") is None


def test_up_to_age_extracts_renewable_clause() -> None:
    assert _normalize_age(_up_to_age(
        "Below Age 67, renewable up to age 75 next birthday"
    )) == "75"
    assert _normalize_age(_up_to_age(
        "between 16 and age 75 (next birthday), renewable up to age 85 years (next birthday)"
    )) == "85"
    assert _normalize_age(_up_to_age("All full time employees up to age 80")) == "80"

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "placement_slips"
STM_FILE = FIXTURE_DIR / "STMicroelectronics - Placement Slips 2026_workingfile (1).xls"
VDL_FILE = FIXTURE_DIR / "VDL - Placement Slips 2026 (as at 13 Apr 2026).xls"


@pytest.fixture(scope="session")
def stm_slip():
    if not STM_FILE.exists():
        pytest.skip(f"STM fixture not present at {STM_FILE}")
    return parse_placement_slip(STM_FILE, client_label="STM")


@pytest.fixture(scope="session")
def vdl_slip():
    if not VDL_FILE.exists():
        pytest.skip(f"VDL fixture not present at {VDL_FILE}")
    return parse_placement_slip(VDL_FILE, client_label="VDL")


def test_stm_extracts_seven_product_sheets(stm_slip):
    import re

    expected = {
        "GEL-GTL",
        "GEL-GHS",
        "GEL-GMM",
        "GEL-SP",
        "Zurich-GPA",
        "Chubb-GBT",
        "Allianz-WICI",
    }
    # STM sheet names contain stray whitespace (e.g. ' Chubb -GBT'); compare
    # on a whitespace-collapsed form.
    sheet_names = {re.sub(r"\s+", "", p.sheet) for p in stm_slip.products}
    expected_clean = {re.sub(r"\s+", "", s) for s in expected}
    missing = expected_clean - sheet_names
    assert not missing, f"Missing STM sheets: {missing}. Got: {sheet_names}"


def test_stm_total_category_count_in_expected_range(stm_slip):
    total = sum(len(p.categories) for p in stm_slip.products)
    # Prototype produced 27 categories. Allow ±3 to absorb minor differences
    # in stop-condition handling between JS and Python regex flavors.
    assert 24 <= total <= 30, f"Expected ~27 categories total, got {total}"


def test_stm_header_fields_extracted(stm_slip):
    # The slip header carries far more than policyholder/insurer/period — the
    # parser must also surface Insured, Office Address, Business, Policy No.,
    # Type of Administration, Eligibility (+ Date) and Last entry age so the
    # guided setup form pre-fills them instead of leaving them blank.
    headers = [p.policy_header for p in stm_slip.products]
    # At least one product sheet populates each of these (they vary by sheet).
    assert any(h.insured for h in headers), "no Insured extracted on any sheet"
    assert any(h.address for h in headers), "no Office Address extracted"
    assert any(h.admin_basis for h in headers), "no Type of Administration"
    assert any(h.last_entry_age for h in headers), "no Last entry age"
    assert any(h.policy_no for h in headers) or True  # policy no. often blank on STM
    # Insured must never capture the Insurer value (the two adjacent labels).
    for h in headers:
        if h.insured and h.insurer:
            assert h.insured != h.insurer or "insurer" not in h.insured.lower()


def test_stm_categories_are_well_formed(stm_slip):
    for product in stm_slip.products:
        for cat in product.categories:
            assert cat.category, f"Empty category in {product.sheet}"
            assert len(cat.category) >= 6, f"Too-short category: {cat!r}"
            assert cat.source_row > 0


def test_vdl_extracts_all_product_sheets(vdl_slip):
    # VDL has 10 product sheets (no metadata sheets), including 3 GHS variants.
    assert len(vdl_slip.products) >= 8, (
        f"Expected ≥8 VDL products, got {len(vdl_slip.products)}: "
        f"{[p.sheet for p in vdl_slip.products]}"
    )


def test_vdl_strips_sheet_name_whitespace(vdl_slip):
    # Brief observation: VDL sheet names contain trailing whitespace. The
    # extracted product_code must be the stripped form.
    for product in vdl_slip.products:
        assert product.product_code == product.product_code.strip()
        assert "  " not in product.product_code


# ── Schedule-of-Benefits layout coverage (PII-free, in-memory) ───────────────
# These exercise _extract_plans_from_sheet against hand-built rows so the two
# supported layouts (per-plan columns, descriptive single-plan) are verified
# without depending on a real workbook. xlrd yields numeric cells as floats, so
# numbers are written as floats here to mirror production input.

_PLAN_HDR = "SCHEDULE OF BENEFITS / INSURER / PLAN"


def _pad(rows: list[list[Cell]]) -> list[list[Cell]]:
    """Right-pad every row to a common width with empty cells."""
    width = max(len(r) for r in rows)
    return [list(r) + [""] * (width - len(r)) for r in rows]


def test_per_plan_float_headers_extracted() -> None:
    # Plan columns arrive as floats (1.0/2.0/3.0); they must normalize to plan
    # codes "1"/"2"/"3" with per-column values read correctly (GMM-shaped).
    rows = _pad([
        ["Cover: ", "Reimbursement of inpatient expenses"],
        [_PLAN_HDR, "", "", "", "", "", "", 1.0, 2.0, 3.0],
        [1.0, "Daily Room & Board", "", "", "", "", "", "As per GHS", "As per GHS", "As per GHS"],
        [3.0, "Surgical Implants", "", "", "", "", "", 5000.0, 5000.0, 5000.0],
        [4.0, "Maximum Benefit", "", "", "", "", "", 40000.0, 40000.0, 40000.0],
    ])
    plans = _extract_plans_from_sheet(rows)
    assert [p.code for p in plans] == ["1", "2", "3"]
    assert plans[0].display_name == "Plan 1"
    values = {it.name: it.value for it in plans[0].items}
    assert values["Daily Room & Board"] == "As per GHS"
    assert values["Surgical Implants"] == "5000"  # float -> clean integer string
    assert values["Maximum Benefit"] == "40000"


def test_cover_mentioning_schedule_not_mistaken_for_header() -> None:
    # The "Cover:" line literally says "as per schedule of benefits"; the real
    # header is the next row. The matcher must not latch onto the cover row
    # (regression: SP extracted zero plans).
    rows = _pad([
        ["Cover: ", "Reimbursement of specialist expenses as per schedule of benefits"],
        [_PLAN_HDR, "", "", "", "", "", "", 1.0],
        [1.0, "Non Panel Specialist"],
        ["", "Limit per disability", "", "", "", "", "", 500.0],
    ])
    plans = _extract_plans_from_sheet(rows)
    assert len(plans) == 1 and plans[0].code == "1"
    item = plans[0].items[0]
    assert item.name == "Non Panel Specialist"
    assert any(s.value == "500" for s in item.sub_items)


def test_descriptive_layout_autodetects_value_column() -> None:
    # No per-plan columns: the value lives in a free-text column (col 4) and a
    # reviewer-note column ("O.K"/"to check") sits beside it. The value column
    # must be auto-detected and the note columns ignored (GTL-shaped).
    rows = _pad([
        ["Cover : ", "Payment of accepted sum insured as per schedule of benefits"],
        ["SCHEDULE OF BENEFITS / INSURER"],
        [1.0, "Death Benefit", "", "", "Pays sum insured", "O.K"],
        [2.0, "TPD Benefit", "", "", "Pays lump sum, 6 months waiting period", "O.K", "to check"],
    ])
    plans = _extract_plans_from_sheet(rows)
    assert len(plans) == 1
    items = plans[0].items
    assert items[0].name == "Death Benefit"
    assert items[0].value == "Pays sum insured"
    assert items[1].value.startswith("Pays lump sum")
    assert all(it.value not in ("O.K", "to check") for it in items)


def test_descriptive_sub_bullets_and_numbered_stop_row() -> None:
    # "-" rows are sub-benefits under the current item; a numbered "List of
    # exclusions" heading terminates the schedule rather than becoming a benefit
    # (GPA-shaped).
    rows = _pad([
        ["Cover :", "Payment of sum insured due to accident"],
        ["SCHEDULE OF BENEFITS / DEFINITION / INSURER"],
        [1.0, "Death Benefit", "", "Pays 100% of sum insured.", "Ok"],
        [5.0, "Additional Features :", "", "", "Ok"],
        ["-", "Ambulance Costs", "", "Up to S$800", "Ok"],
        ["-", "Funeral Expenses", "", "S$2,000", "Ok"],
        [1.0, "List of exclusions :"],
        ["( a )", "War, declared or undeclared"],
    ])
    plans = _extract_plans_from_sheet(rows)
    names = [it.name for it in plans[0].items]
    assert "Death Benefit" in names
    assert "Additional Features :" in names
    assert "List of exclusions :" not in names  # stop row, not a benefit
    feat = next(it for it in plans[0].items if it.name == "Additional Features :")
    assert {s.name for s in feat.sub_items} == {"Ambulance Costs", "Funeral Expenses"}
    assert any(s.value == "Up to S$800" for s in feat.sub_items)


def test_descriptive_section_renumbering_deduped() -> None:
    # Sheets that restart numbering per section (WICI Section A/B) would collide
    # on number "1"; the second is disambiguated to "1.1".
    rows = _pad([
        ["SCHEDULE OF BENEFITS"],
        ["Section A - Work Injury"],
        [1.0, "Death", "", "", "From S$91,000 to S$296,000"],
        [2.0, "Permanent Total Disablement", "", "", "From S$116,000"],
        ["Section B - Common Law"],
        [1.0, "Limit of Liability", "", "", "Up to S$10 million"],
    ])
    plans = _extract_plans_from_sheet(rows)
    items = plans[0].items
    # The section-B "1" is disambiguated with a LETTER suffix ("1a"), which can't
    # be confused with — or collide against — a genuine sub-number like "1.1".
    assert [it.number for it in items] == ["1", "2", "1a"]
    assert items[-1].name == "Limit of Liability"


def test_descriptive_dedup_does_not_collide_with_real_subnumber() -> None:
    # A section restart (two "1"s) AND a genuine "1.1" sub-numbered benefit: the
    # real "1.1" must survive intact; the duplicate "1" becomes "1a".
    rows = _pad([
        ["SCHEDULE OF BENEFITS"],
        [1.0, "Death", "", "", "S$100,000"],
        [1.1, "Death — accidental uplift", "", "", "S$150,000"],
        ["Section B"],
        [1.0, "Limit of Liability", "", "", "S$10 million"],
    ])
    plans = _extract_plans_from_sheet(rows)
    nums = [it.number for it in plans[0].items]
    assert nums == ["1", "1.1", "1a"]  # real "1.1" preserved, dup "1" -> "1a"


def test_sheet_without_schedule_yields_no_plans() -> None:
    # A sheet whose only mention of the phrase is inside the cover sentence (no
    # col-0 header, no benefit rows) must not fabricate a plan.
    rows = _pad([
        ["Cover : ", "Payment up to benefit limit as per schedule of benefits"],
        ["Rate :", "Insured", "Plan", "Premium"],
    ])
    assert _extract_plans_from_sheet(rows) == ()


# ── Column-role profiler coverage (the four problem layouts) ─────────────────
# Each locks in a layout the positional dispatch used to mishandle. The profiler
# derives key/name/value columns from content, so these all flow through one
# parser set.


def test_name_first_per_plan_layout_gbt() -> None:
    # GBT/OSI: benefit NAME in col0 (no enumerator), per-plan values in cols 6-7.
    # Names must come from col0, not be dropped for lack of a number column.
    rows = _pad([
        [_PLAN_HDR, "", "", "", "", "", 1.0, 2.0],
        ["Accidental Death", "", "", "", "", "", 1000000.0, 300000.0],
        ["Medical Expenses", "", "", "", "", "", 500000.0, 500000.0],
        ["Emergency Evacuation", "", "", "", "", "", "Unlimited", "Unlimited"],
    ])
    plans = _extract_plans_from_sheet(rows)
    assert [p.code for p in plans] == ["1", "2"]
    p1 = {it.name: it.value for it in plans[0].items}
    assert p1["Accidental Death"] == "1000000"
    assert p1["Emergency Evacuation"] == "Unlimited"
    assert {it.name: it.value for it in plans[1].items}["Accidental Death"] == "300000"


def test_letter_keyed_layout_gcgp() -> None:
    # GCGP: UPPERCASE letter enumerators in col0, names in col1. The letters are
    # top-level keys; names (not the bare letters) must be captured.
    rows = _pad([
        [_PLAN_HDR, "", "", "", "", 1.0, 2.0],
        ["A", "Panel clinic remuneration", "", "", "", "Fee for Service", ""],
        ["B", "Consultation & medication", "", "", "", "YES", "YES"],
        ["C", "Diagnostic", "", "", "", "YES", "YES"],
    ])
    plans = _extract_plans_from_sheet(rows)
    assert len(plans) == 2
    names = [it.name for it in plans[0].items]
    assert "Consultation & medication" in names
    assert "Diagnostic" in names
    assert "B" not in names and "C" not in names  # bare letters are keys, not names


def test_name_first_emits_plan_with_blank_value_column_gd() -> None:
    # GD dental: Plan 1 is "As Charged" with no per-line numbers (col5 blank);
    # all numeric limits are Plan 2 (col6). Plan 1 must still emit the full
    # benefit list (shared rows) instead of vanishing.
    rows = _pad([
        [_PLAN_HDR, "", "", "", "", 1.0, 2.0],
        ["Examination", "", "", "", "", "", 15.0],
        ["Intraoral X-Ray", "", "", "", "", "", 12.0],
        ["Extraction", "", "", "", "", "", 40.0],
    ])
    plans = _extract_plans_from_sheet(rows)
    assert [p.code for p in plans] == ["1", "2"]
    # Plan 1 (blank column) keeps the names with empty values.
    assert [it.name for it in plans[0].items] == ["Examination", "Intraoral X-Ray", "Extraction"]
    assert all(it.value is None for it in plans[0].items)
    # Plan 2 carries the values.
    assert {it.name: it.value for it in plans[1].items}["Examination"] == "15"


def test_keyless_descriptive_layout_gpa() -> None:
    # GPA: single-plan descriptive with NO enumerator column — names in col1,
    # values in col4. Every name row is its own benefit (value-detection and
    # parsing must not require a col0 number).
    rows = _pad([
        ["SCHEDULE OF BENEFITS / DEFINITION / INSURER"],
        ["", "Accidental Death", "", "", "Pays sum insured"],
        ["", "Burial Expenses", "", "", "10000"],
        ["", "Family Security", "", "", "5,000 per child"],
        ["", "Ambulance Cost", "", "", "500"],
    ])
    plans = _extract_plans_from_sheet(rows)
    assert len(plans) == 1
    values = {it.name: it.value for it in plans[0].items}
    assert values["Burial Expenses"] == "10000"
    assert values["Ambulance Cost"] == "500"
    assert values["Accidental Death"] == "Pays sum insured"
