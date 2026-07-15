"""AI fallback orchestration for unreadable placement-slip sheets.

The AI provider itself is mocked — these assert the orchestration contract:
no-op without a configured provider, payload → canonical dataclasses, and that
an AI-augmented product becomes sound (and is marked ``used_ai``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ai_gateway import SlipExtractionResult
from app.services.ai_slip_extractor import (
    _build_product_slip,
    maybe_ai_augment,
)
from app.services.placement_slip_parser import (
    PolicyHeader,
    ProductSlip,
    parse_placement_slip,
)
from app.services.slip_reconcile import reconcile_slip

STM = (
    Path(__file__).parent / "fixtures" / "placement_slips"
    / "STMicroelectronics - Placement Slips 2026_workingfile (1).xls"
)


def _template_product(code: str = "GBT") -> ProductSlip:
    return ProductSlip(
        sheet="sheet", product_code=code, policy_header=PolicyHeader(),
        categories=(), plans=(),
    )


def test_build_product_slip_maps_payload_to_dataclasses() -> None:
    payload = SlipExtractionResult(
        categories=[{"category": "All staff on travel", "plan_code": "1",
                     "insured": "ACME", "participation": "Compulsory"}],
        plans=[{"code": "1", "display_name": "Plan 1",
                "items": [{"number": "1", "name": "Medical expenses",
                           "value": "As charged", "note": None}]}],
        metadata={}, cache_hit=False,
    )
    ps = _build_product_slip(_template_product(), payload)
    assert ps is not None
    assert ps.categories[0].category == "All staff on travel"
    assert ps.categories[0].plan_code == "1"
    assert ps.plans[0].code == "1"
    assert ps.plans[0].items[0].name == "Medical expenses"


def test_build_product_slip_returns_none_when_empty() -> None:
    payload = SlipExtractionResult(categories=[], plans=[], metadata={}, cache_hit=False)
    assert _build_product_slip(_template_product(), payload) is None


def test_build_product_slip_coerces_financial_fields() -> None:
    payload = SlipExtractionResult(
        categories=[{
            "category": "SM and above", "plan_code": "1", "insured": "ACME",
            "participation": "Compulsory",
            "num_employees": 132.0,
            "basis": "36 x basic monthly salary",
            "sum_insured": "33,000,000",         # string with separators → float
            "premium_rate": 1.62,
            "annual_premium": "$53,460.00",       # currency-prefixed → float
            "rate_basis": "tiered",
            "rate_tiers": {
                "eo": {"rate": 1200, "premium": 170400},
                "ES": {"rate": "3,000"},          # partial tier still kept
                "EC": {"rate": None, "premium": None},  # empty tier dropped
                "EF": "junk",                     # non-dict dropped
            },
            "dependant_rate": "not a number",     # junk → None, no crash
            "estimated_annual_earnings": None,
        }],
        plans=[{"code": "1", "display_name": "Plan 1",
                "items": [{"name": "Room & Board", "value": "As charged"}]}],
        metadata={}, cache_hit=False,
    )
    ps = _build_product_slip(_template_product("GHS"), payload)
    assert ps is not None
    cat = ps.categories[0]
    assert cat.num_employees == 132
    assert cat.sum_insured == 33_000_000.0
    assert cat.annual_premium == 53_460.0
    assert cat.rate_basis == "tiered"  # allowed for GHS
    assert cat.rate_tiers == {
        "EO": {"rate": 1200.0, "premium": 170400.0},
        "ES": {"rate": 3000.0, "premium": 0.0},
    }
    assert cat.dependant_rate is None


def test_build_product_slip_rejects_rate_basis_foreign_to_product() -> None:
    # GHS (tiered medical) can never persist earnings_based — a hallucinated
    # basis is dropped rather than trusted.
    payload = SlipExtractionResult(
        categories=[{"category": "All employees", "plan_code": "1",
                     "rate_basis": "earnings_based", "premium_rate": 0.007}],
        plans=[{"code": "1"}],
        metadata={}, cache_hit=False,
    )
    ps = _build_product_slip(_template_product("GHS"), payload)
    assert ps is not None
    assert ps.categories[0].rate_basis is None
    assert ps.categories[0].premium_rate == 0.007

    # ...but it is valid for a statutory product.
    ps2 = _build_product_slip(_template_product("WICA"), payload)
    assert ps2 is not None
    assert ps2.categories[0].rate_basis == "earnings_based"


def test_augment_is_noop_without_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.ai_slip_extractor.load_ai_config", lambda db, client_id: None
    )
    # A reconciled slip with a flagged product but no provider → unchanged.
    prod = ProductSlip(
        sheet="GBT", product_code="GBT", policy_header=PolicyHeader(),
        categories=(), plans=(),
    )
    from app.services.placement_slip_parser import PlacementSlip
    rec = reconcile_slip(PlacementSlip(client="t", products=(prod,)))
    out = maybe_ai_augment(None, "c", "p", "x", rec)
    assert out is rec


@pytest.mark.skipif(not STM.exists(), reason="STM fixture absent")
def test_augment_makes_flagged_product_sound(monkeypatch) -> None:
    raw = parse_placement_slip(str(STM), client_label="t")
    rec = reconcile_slip(raw)
    flagged = [d for d in rec.diagnostics if d.needs_attention]
    assert flagged, "expected at least one flagged product in STM (e.g. GBT)"
    target = flagged[0]

    monkeypatch.setattr(
        "app.services.ai_slip_extractor.load_ai_config",
        lambda db, client_id: object(),
    )

    def fake_extract(db, *, client_id, policy_year_id, product_code, grid):
        return SlipExtractionResult(
            categories=[{"category": "All staff on authorised travel",
                         "plan_code": "1"}],
            plans=[{"code": "1", "display_name": "Plan 1",
                    "items": [{"name": "Medical expenses", "value": "As charged"}]}],
            metadata={}, cache_hit=False,
        )

    monkeypatch.setattr(
        "app.services.ai_slip_extractor.extract_product_structure_for_slip",
        fake_extract,
    )

    out = maybe_ai_augment(None, "c", "p", str(STM), rec)
    out_diag = {d.sheet: d for d in out.diagnostics}[target.sheet]
    assert out_diag.used_ai is True
    assert out_diag.reconciliation in {"consistent", "assign_default", "fan_out"}
    assert out_diag.n_plans >= 1
