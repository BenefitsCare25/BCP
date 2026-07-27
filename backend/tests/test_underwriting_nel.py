"""Unit coverage for the Non-Evidence-Limit building blocks: salary-multiple
SI resolution, NEL footer extraction, and the guaranteed-SI decision table."""
from __future__ import annotations

from app.services.plan_hydration import (
    resolve_basis_amount,
    salary_from_attrs,
    salary_multiple,
)
from app.services.slip_parsing.header import _age_from_birthday, _nel_amount
from app.services.underwriting import _guaranteed_for, _Life

NEL_TEXT = (
    "Non Evidence Limit : Sum insured exceeding S$500,000 or existing FCL "
    "(whichever is higher) or age 69 (age last birthday) requires underwriting"
)


def test_nel_amount_and_age_extraction() -> None:
    assert _nel_amount(NEL_TEXT) == 500000.0
    assert _nel_amount("Free Cover Limit: $1,250,000.50 applies") == 1250000.5
    assert _nel_amount("no amount stated") is None
    assert _nel_amount(None) is None
    # ALB → ANB (+1): "age 69 (age last birthday)" is ANB 70.
    assert _age_from_birthday(NEL_TEXT) == "70"


def test_salary_multiple_parsing() -> None:
    assert salary_multiple({"basis": "36 times basic monthly salary"}) == (36.0, False)
    assert salary_multiple({"basis": "24x basic monthly salary"}) == (24.0, False)
    assert salary_multiple({"basis": "12 X Basic Monthly Salary"}) == (12.0, False)
    assert salary_multiple({"basis": "2 times annual salary"}) == (2.0, True)
    # No salary mention → not a salary multiple (plan labels like "2 x S$50,000").
    assert salary_multiple({"basis": "2 x S$50,000"}) is None
    assert salary_multiple({"basis": "100000"}) is None
    assert salary_multiple({"basis": None}) is None
    # A grouped amount must not be read as the multiple ("000 x" → 0), which
    # would publish a $0 sum insured as though it were a real figure.
    assert salary_multiple(
        {"basis": "S$100,000 x 2 plus 3 times basic monthly salary"}
    ) == (3.0, False)
    # "annual" is read from the phrase THIS multiple qualifies, not from
    # anywhere in the string — else a compound basis grosses the 24x by 12.
    assert salary_multiple(
        {"basis": "24 times basic monthly salary or 2 times annual salary"}
    ) == (24.0, False)


def test_salary_from_attrs_parses_display_strings() -> None:
    assert salary_from_attrs({"salary": 5500}) == 5500.0
    assert salary_from_attrs({"salary": "5,500.50"}) == 5500.5
    assert salary_from_attrs({"salary": "S$4,000"}) == 4000.0
    assert salary_from_attrs({"salary": ""}) is None
    assert salary_from_attrs({"salary": "n/a"}) is None
    assert salary_from_attrs({}) is None
    assert salary_from_attrs(None) is None


def test_resolve_basis_amount() -> None:
    # Plain amounts pass through untouched.
    assert resolve_basis_amount({"basis": 100000}, {}) == 100000.0
    assert resolve_basis_amount({"basis": "100000"}, {}) == 100000.0
    # Salary multiple x monthly salary.
    pa = {"basis": "24 times basic monthly salary"}
    assert resolve_basis_amount(pa, {"salary": "5,000"}) == 120000.0
    # Annual-salary multiples gross the monthly figure by 12.
    annual = {"basis": "2 times annual salary"}
    assert resolve_basis_amount(annual, {"salary": 5000}) == 120000.0
    # No salary on file → unresolvable, never a guess.
    assert resolve_basis_amount(pa, {}) is None
    # Relative bases stay unresolvable.
    assert resolve_basis_amount({"basis": "50% of GTL"}, {"salary": 5000}) is None
    # A compound basis scales the matched multiple only (24 x 5000, NOT x12).
    compound = {"basis": "24 times basic monthly salary or 2 times annual salary"}
    assert resolve_basis_amount(compound, {"salary": 5000}) == 120000.0
    # A grouped amount can't degrade into a 0x multiple / $0 sum insured.
    grouped = {"basis": "S$100,000 x 2 plus 3 times basic monthly salary"}
    assert resolve_basis_amount(grouped, {"salary": 5000}) == 15000.0


def _life(anb: int | None, new: bool) -> _Life:
    return _Life(subject_id="x", is_employee=True, key=("e", "k"), anb=anb, new_life=new)


def test_guaranteed_decision_table() -> None:
    # SI trigger: guaranteed = max(FCL, last covered), only an increase pends.
    assert _guaranteed_for(100000, 50000, None, _life(None, False), None) == 50000
    assert _guaranteed_for(100000, 50000, None, _life(None, False), 80000) == 80000
    # At/below the threshold → no case.
    assert _guaranteed_for(50000, 50000, None, _life(None, False), None) is None
    assert _guaranteed_for(80000, 50000, None, _life(None, False), 80000) is None
    # No FCL and no age gate → no case regardless of amount.
    assert _guaranteed_for(1_000_000, None, None, _life(None, False), None) is None

    # Age trigger, new hire → guaranteed 0 (whole SI underwritten).
    assert _guaranteed_for(40000, 50000, 70, _life(70, True), None) == 0.0
    # Age trigger, existing life → last covered SI.
    assert _guaranteed_for(100000, 50000, 70, _life(75, False), 80000) == 80000
    # Existing at an unchanged SI → nothing to underwrite.
    assert _guaranteed_for(80000, 50000, 70, _life(75, False), 80000) is None
    # Existing with unknown history → FCL fallback.
    assert _guaranteed_for(100000, 50000, 70, _life(75, False), None) == 50000
    # Under the age gate → plain SI trigger applies.
    assert _guaranteed_for(40000, 50000, 70, _life(69, False), None) is None
    # Unknown age (no DOB) never age-triggers.
    assert _guaranteed_for(40000, 50000, 70, _life(None, True), None) is None
