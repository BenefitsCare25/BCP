"""Decoupled Schedule-of-Benefits column model.

The SOB grid is stored once as ``{columns, items}`` (a shared row skeleton + a
sparse per-column override) instead of replicated into every basis-of-cover
plan. These cover the de-dup (many sum-insured tiers → one column for life/CI,
distinct columns for GHS) and the confirm-time projection back to per-plan
``Plan.benefit_schedule`` (whose shape is unchanged), including the legacy
fallback for pre-redesign drafts.
"""
from __future__ import annotations

from app.api.v1.product_setups import _benefit_schedule
from app.services.sob_columns import (
    NOT_COVERED,
    resolve_plan_schedule,
    sob_from_plan_items,
)


def _plan(code: str, *values: str, selected: bool = True) -> dict:
    """A plan whose benefit_items carry the given per-line values."""
    return {
        "code": code,
        "label": f"Plan {code}",
        "selected": selected,
        "benefit_items": [
            {"number": str(i + 1), "name": f"Item {i + 1}", "kind": "amount",
             "value": v, "sub_items": []}
            for i, v in enumerate(values)
        ],
    }


def test_identical_plans_collapse_to_one_column() -> None:
    # GCI shape: 22 sum-insured tiers, all sharing "Pays sum insured".
    plans = [_plan(str(n), "Pays sum insured", "30 days") for n in range(1, 23)]
    sob = sob_from_plan_items(plans)
    assert len(sob["columns"]) == 1
    assert sob["columns"][0]["label"] == "All plans"
    assert len(sob["columns"][0]["plan_codes"]) == 22
    assert [it["base_value"] for it in sob["items"]] == ["Pays sum insured", "30 days"]
    assert all(not it["overrides"] for it in sob["items"])


def test_distinct_plans_keep_separate_columns() -> None:
    # GHS shape: per-plan values differ → one column per distinct vector.
    plans = [
        _plan("1", "20000", "1 Bed Private"),
        _plan("2", "10000", "1 Bed Restr."),
        _plan("3", "10000", "4 Bed Restr."),
    ]
    sob = sob_from_plan_items(plans)
    assert len(sob["columns"]) == 3
    # First column is the base; the rest carry sparse overrides where they differ.
    icu = sob["items"][0]
    assert icu["base_value"] == "20000"
    col2, col3 = sob["columns"][1]["id"], sob["columns"][2]["id"]
    assert icu["overrides"][col2] == "10000"
    assert icu["overrides"][col3] == "10000"


def test_resolve_round_trips_each_plan() -> None:
    plans = [
        _plan("1", "20000", "1 Bed Private"),
        _plan("2", "10000", "1 Bed Restr."),
    ]
    sob = sob_from_plan_items(plans)
    s1 = resolve_plan_schedule(sob, "1", 200)
    s2 = resolve_plan_schedule(sob, "2", 200)
    assert [it["value"] for it in s1] == ["20000", "1 Bed Private"]
    assert [it["value"] for it in s2] == ["10000", "1 Bed Restr."]


def test_unknown_plan_resolves_to_the_sole_column() -> None:
    # One column covers the whole product, so an unlisted code belongs to it.
    sob = sob_from_plan_items([_plan("1", "x"), _plan("2", "x")])
    assert len(sob["columns"]) == 1
    assert [it["value"] for it in resolve_plan_schedule(sob, "unlisted", 200)] == ["x"]


def test_unknown_plan_gets_no_schedule_when_columns_differ() -> None:
    # With several benefit levels there is no safe guess. Inheriting column 0
    # handed the plan the RICHEST schedule, silently over-stating cover.
    sob = sob_from_plan_items([_plan("1", "x"), _plan("2", "y")])
    assert resolve_plan_schedule(sob, "does-not-exist", 200) == []


def test_column_label_uses_the_slips_own_header() -> None:
    # CDL GHS: one composite header names four codes, fanned to a plan each.
    header = "PLAN 1/U01/U04/U06"
    plans = [
        {**_plan(code, "20000"), "source_label": header}
        for code in ("1", "U01", "U04", "U06")
    ]
    plans.append({**_plan("2", "10000"), "source_label": "PLAN 2/D01"})
    sob = sob_from_plan_items(plans)
    assert sob["columns"][0]["label"] == header
    assert sob["columns"][0]["plan_codes"] == ["1", "U01", "U04", "U06"]
    assert sob["columns"][1]["label"] == "PLAN 2/D01"


def test_column_label_joins_headers_that_share_values() -> None:
    # CDL GMM prices PLAN 3 and PLAN 4 identically, so both headers merge.
    plans = [
        {**_plan("3", "15000"), "source_label": "PLAN 3/D02"},
        {**_plan("4", "15000"), "source_label": "PLAN 4/D03"},
    ]
    sob = sob_from_plan_items(plans)
    assert len(sob["columns"]) == 1
    assert sob["columns"][0]["label"] == "PLAN 3/D02 + PLAN 4/D03"


def test_column_label_ignores_headers_that_miss_a_member() -> None:
    # VDL GCSP groups a B3 its header never names — claiming the header would
    # advertise a narrower code set than the column really covers.
    plans = [
        {**_plan("B2", "500"), "source_label": "Plan B2, B1"},
        {**_plan("B1", "500"), "source_label": "Plan B2, B1"},
        {**_plan("B3", "500")},
    ]
    sob = sob_from_plan_items(plans)
    assert sob["columns"][0]["label"] == "All plans"


def test_not_covered_sentinel_is_per_column() -> None:
    # A per-plan exclusion: plan 2 declines the line.
    plans = [_plan("1", "3000"), _plan("2", NOT_COVERED)]
    sob = sob_from_plan_items(plans)
    s2 = resolve_plan_schedule(sob, "2", 200)
    assert s2[0]["value"] == NOT_COVERED


def test_benefit_schedule_projects_from_sob() -> None:
    plans = [_plan("1", "20000"), _plan("2", "10000")]
    answers = {"sob": sob_from_plan_items(plans), "plans": plans}
    sched1 = _benefit_schedule(answers, {"code": "1"})
    sched2 = _benefit_schedule(answers, {"code": "2"})
    assert sched1["items"][0]["value"] == "20000"
    assert sched2["items"][0]["value"] == "10000"


def test_benefit_schedule_legacy_fallback() -> None:
    # A pre-redesign draft (no `sob`) still projects from plan.benefit_items.
    plan = _plan("1", "7777")
    sched = _benefit_schedule({"plans": [plan]}, plan)
    assert sched["items"][0]["value"] == "7777"


def test_copay_properties_resolve_per_column() -> None:
    plans = [
        {"code": "1", "label": "P1", "selected": True, "benefit_items": [
            {"number": "1", "name": "GP", "kind": "copay", "value": "",
             "properties": {"per_visit": "50"}, "sub_items": []}]},
        {"code": "2", "label": "P2", "selected": True, "benefit_items": [
            {"number": "1", "name": "GP", "kind": "copay", "value": "",
             "properties": {"per_visit": "30"}, "sub_items": []}]},
    ]
    sob = sob_from_plan_items(plans)
    # Copay differs per plan → two columns, each carrying its own properties.
    assert len(sob["columns"]) == 2
    s1 = resolve_plan_schedule(sob, "1", 200)
    s2 = resolve_plan_schedule(sob, "2", 200)
    assert s1[0]["properties"]["per_visit"] == "50"
    assert s2[0]["properties"]["per_visit"] == "30"
