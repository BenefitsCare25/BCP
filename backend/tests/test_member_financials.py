"""Unit tests for plan_hydration.member_financials premium reduction.

The per-employee read paths (employees endpoint, benefit statement, election
options) must show a MEMBER's premium, never a group/policy aggregate. These
guard the reduction rules per rate basis.
"""
from __future__ import annotations

from app.services.plan_hydration import member_financials


def test_annual_flat_premium_not_surfaced_per_member() -> None:
    # GBT: one premium for the WHOLE policy, stored on one category. A member must
    # not see the policy total as their personal premium.
    pa = {"plan_code": "1", "rate_basis": "annual_flat", "annual_premium": 3169.8}
    fin = member_financials(pa)
    assert fin is not None
    assert fin.annual_premium is None


def test_earnings_based_premium_not_surfaced_but_inputs_kept() -> None:
    # WICA: annual_premium is a per-entity total rated on whole payroll. Drop it
    # per member, but keep the rate + earnings (informational).
    pa = {
        "plan_code": "1",
        "rate_basis": "earnings_based",
        "premium_rate": 0.00033,
        "annual_premium": 23746.95609,
        "estimated_annual_earnings": 71960473.0,
        "num_employees": 427,
    }
    fin = member_financials(pa)
    assert fin is not None
    assert fin.annual_premium is None
    assert fin.estimated_annual_earnings == 71960473.0
    assert fin.premium_rate == 0.00033


def test_per_1000_si_still_reduces_to_member_premium() -> None:
    # Regression guard: sum-assured products still compute basis/1000 * rate.
    pa = {
        "plan_code": "1",
        "rate_basis": "per_1000_si",
        "premium_rate": 1.62,
        "basis": "2000000",
        "annual_premium": 6480,
    }
    fin = member_financials(pa)
    assert fin is not None
    assert fin.annual_premium == 3240.0
    assert fin.sum_insured == 2000000.0


def test_flat_and_tiered_rates_price_the_member_and_covered_family() -> None:
    from app.schemas.api import PlanFinancials
    from app.services.member_premium import member_premium

    gp = PlanFinancials(rate_basis="flat", premium_rate=378.0, dependant_rate=396.9)
    assert member_premium(gp).amount == 378.0
    assert member_premium(gp, spouses=1).amount == 774.9

    ghs = PlanFinancials(
        rate_basis="tiered",
        rate_tiers={
            "EO": {"rate": 1041.0, "premium": 262332.0},
            "ES": {"rate": 2602.5, "premium": 0.0},
            "EF": {"rate": 4164.0, "premium": 0.0},
        },
    )
    assert member_premium(ghs).amount == 1041.0
    assert member_premium(ghs, spouses=1).amount == 2602.5
    assert member_premium(ghs, spouses=1, children=2).amount == 4164.0
    # No EC rate on the slip: unknown, never a neighbouring tier's figure.
    assert member_premium(ghs, children=1) is None


def test_voluntary_dependant_cover_is_eligibility_not_cover() -> None:
    from app.services.dependant_coverage import (
        category_covers_dependants,
        category_dependant_mode,
    )

    voluntary = {"dependant": "voluntary"}
    assert category_dependant_mode(True, None, voluntary) == "voluntary"
    assert category_covers_dependants(True, None, voluntary) is False
    assert category_covers_dependants(True, None, {"dependant": "compulsory"}) is True
    legacy_text = "Manager / All Eligible Dependants on Voluntary basis"
    assert category_dependant_mode(True, None, None, legacy_text) == "voluntary"


def test_dependants_without_a_dependant_rate_leave_the_premium_unknown() -> None:
    from app.schemas.api import PlanFinancials
    from app.services.member_premium import member_premium

    flat = PlanFinancials(rate_basis="flat", premium_rate=378.0)
    assert member_premium(flat).amount == 378.0
    assert member_premium(flat, spouses=1) is None


def test_dependant_rate_outranks_an_exclusion_phrase() -> None:
    from app.services.dependant_coverage import category_dependant_mode

    text = "Employees & eligible dependants (excluding dependants above age 70)"
    assert category_dependant_mode(True, {"dependant_rate": 396.9}, None, text) == "compulsory"
    assert category_dependant_mode(True, {}, None, text) is None
