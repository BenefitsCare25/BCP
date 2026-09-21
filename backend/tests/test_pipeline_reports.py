from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from app.models import Claim, PolicyYear
from app.services import claim_placement, premium_breakdown


def test_filing_snapshot_survives_policy_number_edit_and_preserves_other_metadata(monkeypatch):
    claim = Claim(claim_kind="insured", product_code="GHS", intake_meta={"received_via": "email"})
    current = {
        "product_code": "GHS",
        "policy_number": "OLD",
        "insurer": "A",
        "product_name": "Hospital",
    }
    monkeypatch.setattr(claim_placement, "current_placement", lambda *_: dict(current))
    claim_placement.capture_claim_placement(None, claim)
    current["policy_number"] = "NEW"
    claim_placement.capture_claim_placement(None, claim)
    assert claim_placement.placement_cells(None, claim) == ["OLD", "A", "Hospital", "At filing"]
    assert claim.intake_meta["received_via"] == "email"
    legacy = Claim(claim_kind="insured", product_code="GHS")
    assert claim_placement.placement_cells(None, legacy)[0] == "NEW"
    assert "no filing snapshot" in claim_placement.placement_cells(None, legacy)[3]


def test_changed_product_does_not_use_previous_product_snapshot(monkeypatch):
    claim = Claim(
        claim_kind="insured",
        product_code="GP",
        intake_meta={
            "placement_snapshot": {"product_code": "GHS", "policy_number": "HOSPITAL"},
        },
    )
    monkeypatch.setattr(
        claim_placement,
        "current_placement",
        lambda *_: {"product_code": "GP", "policy_number": "CLINIC"},
    )
    assert claim_placement.placement_cells(None, claim)[0] == "CLINIC"


def test_legacy_resubmission_does_not_invent_filing_snapshot(monkeypatch):
    claim = Claim(claim_kind="insured", product_code="GHS", status="needs_info")
    monkeypatch.setattr(claim_placement, "current_placement", lambda *_: {"policy_number": "NEW"})
    claim_placement.capture_claim_placement(None, claim)
    assert claim.intake_meta is None
    assert (
        claim_placement.placement_cells(None, claim)[-1]
        == "Current configuration; no filing snapshot"
    )


def test_premium_breakdown_gst_unknown_prices_and_formula_safety(monkeypatch):
    year = PolicyYear(
        id="year",
        client_id="client",
        year=2026,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    term = NS(
        code="GHS",
        product_id="product",
        policy_number="P123",
        gst_included=True,
        gst_rate=9,
        coverage_start=year.start_date,
        coverage_end=year.end_date,
    )
    employee = NS(
        id="ee",
        staff_id="=evil()",
        employee_name="Synthetic employee",
        attribute_values={"cost_centre": "Operations"},
    )
    db = MagicMock()
    member_result = MagicMock()
    member_result.all.return_value = [employee]
    db.scalars.side_effect = [[NS(id="product", code="GHS")], member_result]
    monkeypatch.setattr(premium_breakdown, "resolve_terms", lambda *_: [term])
    monkeypatch.setattr(premium_breakdown, "insurer_map", lambda *_: {"product": "Insurer"})
    coverage = [
        NS(
            product_code="GHS",
            product_name="Hospital",
            plan_code="A",
            covered_dependants=[],
            financials=NS(annual_premium=109, rate_basis="fixed"),
        ),
        NS(
            product_code="GP",
            product_name="Clinic",
            plan_code="B",
            covered_dependants=[],
            financials=None,
        ),
    ]
    monkeypatch.setattr(
        premium_breakdown, "build_benefit_statement", lambda *_: NS(coverage=coverage, flex=None)
    )
    workbook = premium_breakdown.build_premium_breakdown(db, year)
    rows = list(workbook["Member Premiums"].values)
    assert rows[1][0] == "'=evil()"
    assert rows[1][12:15] == (100, 9, 109)
    assert rows[2][12:15] == (None, None, None)
    assert rows[2][16] == "Per-member premium unavailable"
    assert workbook["Premium Summary"].max_row == 3
    assert "Read Me" in workbook.sheetnames
