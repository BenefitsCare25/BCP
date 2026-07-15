"""Confirm-time materialization of Basis-of-Cover rows into plan_assignments.

Covers the three rate models the dynamic form emits: tiered (EO/ES/EC/EF),
per_member (member rate x headcount) and per_1000_si (rate per S$1,000 x SI).
These map onto Category.plan_assignments in the parser-compatible shape the
financials view reads, so figures must round-trip exactly.
"""
from __future__ import annotations

from app.api.v1.product_setups import _category_plan_assignments, _coerce_money


def test_per_1000_si_computes_premium_from_sum_insured() -> None:
    # GTL: SI 4,000,000 @ 1.62 per S$1,000 => 6,480 annual premium.
    row = {
        "plan_code": "1", "insured": "ACME", "tiers": {},
        "num_employees": 2, "sum_insured": 4_000_000, "basis": "Flat sum",
    }
    rate_table = {"1": {"flat": {"rate": 1.62, "premium": 0}}}
    pa = _category_plan_assignments(row, rate_table, "per_1000_si", "sum_assured")
    assert pa["rate_basis"] == "per_1000_si"
    assert pa["sum_insured"] == 4_000_000
    assert pa["basis"] == "Flat sum"
    assert pa["premium_rate"] == 1.62
    assert pa["annual_premium"] == 6480.0


def test_per_1000_si_prefers_supplied_premium_over_computed() -> None:
    row = {"plan_code": "1", "tiers": {}, "sum_insured": 1_000_000}
    rate_table = {"1": {"flat": {"rate": 1.5, "premium": 1490}}}
    pa = _category_plan_assignments(row, rate_table, "per_1000_si", "sum_assured")
    # Supplied premium (1490) wins over the computed 1500 (handles slip rounding).
    assert pa["annual_premium"] == 1490


def test_per_member_computes_premium_from_headcount() -> None:
    # GCGP: 494 members @ 378 each => 186,732.
    row = {"plan_code": "1", "insured": "CDL", "tiers": {}, "num_employees": 494}
    rate_table = {"1": {"flat": {"rate": 378, "premium": 0}}}
    pa = _category_plan_assignments(row, rate_table, "per_member", "per_member")
    assert pa["rate_basis"] == "per_member"
    assert pa["premium_rate"] == 378
    assert pa["annual_premium"] == 186732.0
    # Per-member products carry no sum_insured.
    assert "sum_insured" not in pa


def test_tiered_unchanged() -> None:
    # GHS tiered path must be byte-for-byte what it was before the refactor.
    row = {"plan_code": "1", "insured": "X", "tiers": {"EO": 10, "ES": 5}}
    rate_table = {
        "1": {
            "EO": {"rate": 100, "premium": 1000},
            "ES": {"rate": 200, "premium": 1000},
        }
    }
    pa = _category_plan_assignments(row, rate_table, "tiered", "tiered")
    assert pa["rate_basis"] == "tiered"
    assert pa["num_employees"] == 15
    assert pa["tier_counts"] == {"EO": 10, "ES": 5}
    assert pa["annual_premium"] == 2000.0


def test_sum_insured_carried_even_without_rate() -> None:
    # A category with SI but no rate yet still records the cover amount.
    row = {"plan_code": "1", "tiers": {}, "sum_insured": 250_000, "basis": "Flat"}
    pa = _category_plan_assignments(row, {}, "per_1000_si", "sum_assured")
    assert pa["sum_insured"] == 250_000
    assert pa["basis"] == "Flat"
    assert "annual_premium" not in pa


def test_per_member_ignores_stale_tier_keys() -> None:
    # A draft saved while the product was tiered_medical leaves EO/ES keys in
    # rate_table; after reclassification (SP/GP → outpatient/per_member) confirm
    # must still read the flat cell, not fall into the tiered branch (finding #2).
    row = {"plan_code": "1", "tiers": {}, "num_employees": 10}
    rate_table = {
        "1": {"EO": {"rate": 5, "premium": 50}, "flat": {"rate": 100, "premium": 0}}
    }
    pa = _category_plan_assignments(row, rate_table, "per_member", "per_member")
    assert pa["rate_basis"] == "per_member"
    assert pa["premium_rate"] == 100
    assert pa["annual_premium"] == 1000  # 100 x 10, not the stale EO tier


def test_flat_rate_key_contract() -> None:
    # The single-rate sentinel the form writes, the slip prefill emits, and
    # confirm reads must all agree (and match the frontend FLAT_TIER mirror).
    from app.api.v1.product_setups import _FLAT_RATE_KEY as ps_key
    from app.services.slip_to_setup import _FLAT_RATE_KEY as sts_key

    assert ps_key == sts_key == "flat"


def test_coerce_money_tolerates_strings_and_blanks() -> None:
    assert _coerce_money("4,000,000") == 4_000_000.0
    assert _coerce_money(250000) == 250000.0
    assert _coerce_money("") is None
    assert _coerce_money(None) is None
    assert _coerce_money("junk") is None
