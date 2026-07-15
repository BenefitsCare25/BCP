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
