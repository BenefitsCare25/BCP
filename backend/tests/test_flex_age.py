from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models import Claim, Dependant, Employee, PolicyYear
from app.services.flex_age import age_rule_errors, assert_flex_age_eligible, employee_age_eligible
from app.services.flex_membership import resolve_employee


@pytest.mark.parametrize(
    "dob,eligible", [("1960-01-01", False), ("1961-01-02", True), (None, True)]
)
def test_employee_inclusive_next_birthday_at_renewal(dob, eligible):
    employee = Employee(attribute_values={"dob": dob})
    assert (
        employee_age_eligible(employee, {"employee_age_limits": {"max": 65}}, date(2026, 1, 1))
        is eligible
    )


@pytest.mark.parametrize(
    "window", [{"max": True}, {"min": -1}, {"min": 70, "max": 20}, {"max": 20.5}, "65"]
)
def test_invalid_employee_age_windows_rejected(window):
    assert age_rule_errors({"meta": {"employee_age_limits": window}})


def test_out_of_age_employee_has_no_tier_or_wallet():
    employee = Employee(id="ee", staff_id="one", attribute_values={"dob": "1960-01-01"})
    resolved = resolve_employee(
        employee,
        [],
        {},
        [{"name": "Everyone", "allowance": 100}],
        {"employee_age_limits": {"max": 65}},
        ref=date(2026, 1, 1),
    )
    assert resolved.tier_idx is None
    assert resolved.wallet_amount is None


def test_dependant_age_blocks_new_submission_even_with_wallet_block_off():
    employee = Employee(id="ee", client_id="client", policy_year_id="year")
    claim = Claim(dependant_id="child", policy_year_id="year")
    year = PolicyYear(id="year", start_date=date(2026, 1, 1))
    child = Dependant(
        id="child",
        employee_id="ee",
        client_id="client",
        policy_year_id="year",
        attribute_values={"relationship": "child", "dob": "1995-01-01"},
    )
    db = MagicMock()
    db.get.side_effect = lambda model, _: year if model is PolicyYear else child
    with pytest.raises(HTTPException, match=r"dependant.*age limits"):
        assert_flex_age_eligible(db, claim, employee, {})
    child.attribute_values = {"relationship": "child", "dob": "2010-01-01"}
    assert_flex_age_eligible(db, claim, employee, {})


def test_stale_assigned_wallet_does_not_restore_age_ineligible_cover():
    from app.services.benefit_statement import _build_flex_coverage

    employee = Employee(
        policy_year_id="year", flex_tier_name="Everyone", attribute_values={"dob": "1960-01-01"}
    )
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = NS(
        scheme={"meta": {"employee_age_limits": {"max": 65}}}
    )
    db.get.return_value = NS(start_date=date(2026, 1, 1))
    assert _build_flex_coverage(db, employee) is None
