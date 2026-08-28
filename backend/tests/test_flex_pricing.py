"""Unit tests for the flex price-tag resolver + config validation.

Covers the pure resolution logic (age banding, matrix lookup, DOB→age with the
Excel midnight-string gotcha) and the write-boundary validation — no DB needed.
"""
from __future__ import annotations

from datetime import date

from app.api.v1.flex_pricing import _financial_pricing_mode
from app.models import Dependant, Product
from app.schemas.api import PlanFinancials, VoluntaryRateBand
from app.services.flex_pricing_resolver import (
    _dependant_eligible,
    _effective_dependant_mode,
    _per_member_slip_premium,
    _slip_dependant_for_tier,
    _slip_dependant_shape,
    age_band_label,
    dependant_age_limits,
    dependant_pricing_breakdown,
    dependant_tag,
    effective_dependant_participation,
    family_role,
    family_slip_incr,
    member_coverage_tag,
    member_price_tag,
    price_tag_for,
    slip_family_increments,
    slip_premium_for,
    slip_premium_index_from,
    validate_pricing_shape,
)
from app.services.roster_attributes import age_as_of, parse_dob

_BANDS = [
    {"label": "<30", "min": 0, "max": 29},
    {"label": "30-49", "min": 30, "max": 49},
    {"label": "50+", "min": 50, "max": 200},
]
_PRICING = {
    "products": {
        "prodA": {
            "age_bands": _BANDS,
            "price_tags": {
                "cat1::GOLD": {"<30": 1000, "30-49": 1500, "50+": 2200},
                "cat1::SILVER": {"<30": 600, "30-49": 900, "50+": 0},
            },
        }
    }
}


def test_age_band_label_first_match_and_open_ended():
    assert age_band_label(_BANDS, 25) == "<30"
    assert age_band_label(_BANDS, 30) == "30-49"
    assert age_band_label(_BANDS, 49) == "30-49"
    assert age_band_label(_BANDS, 80) == "50+"
    assert age_band_label(_BANDS, None) is None
    # An age outside every band → no label.
    assert age_band_label([{"label": "x", "min": 18, "max": 25}], 40) is None


def test_price_tag_for_resolves_tier_and_band():
    assert price_tag_for(_PRICING, "prodA", "cat1::GOLD", 25) == 1000.0
    assert price_tag_for(_PRICING, "prodA", "cat1::GOLD", 55) == 2200.0
    # A real 0.0 price is a valid configured value, not "missing".
    assert price_tag_for(_PRICING, "prodA", "cat1::SILVER", 60) == 0.0


def test_price_tag_for_missing_returns_none():
    assert price_tag_for(_PRICING, "prodA", "cat1::UNKNOWN", 25) is None  # no plan match
    assert price_tag_for(_PRICING, "other", "cat1::GOLD", 25) is None     # no product
    assert price_tag_for(_PRICING, "prodA", "cat1::GOLD", None) is None   # no age
    assert price_tag_for(None, "prodA", "cat1::GOLD", 25) is None         # no matrix


def test_price_tag_for_requires_the_exact_category_tier():
    assert price_tag_for(_PRICING, "prodA", "OTHERCAT::GOLD", 25) is None
    assert price_tag_for(_PRICING, "prodA", "None::SILVER", 35) is None


def test_price_tag_for_ambiguous_plan_code_does_not_guess():
    # Two tiers share plan_code 'P' with different prices → a drifted category key
    # must NOT silently pick one; exact match only.
    pricing = {
        "products": {
            "p": {
                "age_bands": _BANDS,
                "price_tags": {
                    "catA::P": {"<30": 100, "30-49": 100, "50+": 100},
                    "catB::P": {"<30": 999, "30-49": 999, "50+": 999},
                },
            }
        }
    }
    assert price_tag_for(pricing, "p", "catA::P", 25) == 100.0  # exact still works
    assert price_tag_for(pricing, "p", "catZ::P", 25) is None   # ambiguous → None


# ── Price-tag source (slip vs manual) + drawdown rule (full vs on_change) ─────

# GTL-style matrix: default plan P300 costs 300, the P400 upgrade costs 400.
def test_category_price_never_propagates_to_a_sibling_cohort_or_option():
    pricing = {
        "products": {
            "p": {
                "age_bands": _BANDS,
                "price_tags": {
                    "exec-option-1::P": {
                        "<30": 100,
                        "30-49": 100,
                        "50+": 100,
                    },
                },
            }
        }
    }
    assert price_tag_for(pricing, "p", "staff-option-1::P", 25) is None
    assert price_tag_for(pricing, "p", "exec-option-2::P", 25) is None


_DRAW = {
    "products": {
        "gtl": {
            "age_bands": _BANDS,
            "price_tags": {
                "catG::P300": {"<30": 300, "30-49": 300, "50+": 300},
                "catG::P400": {"<30": 400, "30-49": 400, "50+": 400},
                "catG::P200": {"<30": 200, "30-49": 200, "50+": 200},
            },
        }
    }
}
# Slip-premium index (annual premium per tier) for the same product.
_SLIP = {"gtl": {"catG::P300": 312.0, "catG::P400": 420.0, "catG::P200": 180.0}}


def _draw(rule, *, plan, source="manual", source_map=None, pricing=_DRAW):
    """Resolve a member's GTL flex draw-down for an elected plan under a rule."""
    return member_price_tag(
        source_map=source_map if source_map is not None else {"gtl": source},
        rule=rule,
        pricing=pricing,
        slip_idx=_SLIP,
        product_id="gtl",
        age=35,
        declined=False,
        tier_category_id="catG",
        plan_code=plan,
        default_tier_category_id="catG",
        default_plan="P300",
    )


def test_full_rule_manual_source_is_the_whole_price_tag():
    # "full" + manual matrix == prior behavior: the elected tier's whole tag.
    assert _draw("full", plan="P300") == 300.0  # default plan kept
    assert _draw("full", plan="P400") == 400.0  # upgrade draws the full 400


def test_on_change_rule_draws_only_the_difference():
    # The user's example: default GTL 300 → upgrade to 400 draws 100 (400-300).
    assert _draw("on_change", plan="P400") == 100.0
    # Keeping the default plan draws nothing.
    assert _draw("on_change", plan="P300") == 0.0
    # A downgrade is a credit (negative draw-down).
    assert _draw("on_change", plan="P200") == -100.0


def test_on_change_unpriced_default_treats_baseline_as_zero():
    # Contract: an unpriced default plan counts as $0 baseline, so an upgrade off
    # it draws the full elected tag (the window form surfaces unpriced products).
    assert (
        member_price_tag(
            source_map={}, rule="on_change", pricing=_DRAW, slip_idx=None,
            product_id="gtl", age=35, declined=False,
            tier_category_id="catG", plan_code="P400",
            default_tier_category_id="catG", default_plan="PX",  # absent from matrix
        )
        == 400.0
    )


_EMPTY = {"products": {"gtl": {"age_bands": _BANDS, "price_tags": {}}}}


def test_slip_source_uses_premium_when_no_override():
    # "slip" source prices off the slip premium when the matrix has no override.
    assert _draw("full", plan="P400", source="slip", pricing=_EMPTY) == 420.0
    # on_change against the slip default (312): 420 - 312 = 108.
    assert _draw("on_change", plan="P400", source="slip", pricing=_EMPTY) == 108.0


def test_matrix_override_wins_over_slip():
    # A broker correction (matrix value) overrides a wrong slip extraction even under
    # the "slip" source, so the From-slip view is editable. _DRAW prices P400 at 400,
    # the slip at 420 → the override (400) wins.
    assert _draw("full", plan="P400", source="slip") == 400.0
    # Tiers WITHOUT an override still fall back to the slip premium.
    sparse = {"products": {"gtl": {"age_bands": _BANDS, "price_tags": {
        "catG::P400": {"<30": 999, "30-49": 999, "50+": 999},
    }}}}
    assert _draw("full", plan="P400", source="slip", pricing=sparse) == 999.0
    assert _draw("full", plan="P300", source="slip", pricing=sparse) == 312.0


def test_source_defaults_to_slip_per_product():
    # Price tags come from the placement slip by default (no override): a product
    # absent from the source map resolves to the slip premium.
    assert _draw("full", plan="P400", source_map={}, pricing=_EMPTY) == 420.0
    assert _draw("full", plan="P400", source_map={"other": "slip"}, pricing=_EMPTY) == 420.0
    # An explicit "manual" with no matrix value leaves the tier unpriced...
    assert _draw("full", plan="P400", source_map={"gtl": "manual"}, pricing=_EMPTY) is None
    # ...but a manual matrix value prices it.
    assert _draw("full", plan="P400", source_map={"gtl": "manual"}) == 400.0


def test_declined_costs_no_flex():
    assert (
        member_price_tag(
            source_map={}, rule="full", pricing=_DRAW, slip_idx=None,
            product_id="gtl", age=35, declined=True,
            tier_category_id="catG", plan_code="P400",
            default_tier_category_id="catG", default_plan="P300",
        )
        is None
    )


def test_slip_source_falls_back_to_matrix_when_premium_missing():
    # A tier with no slip premium still gets priced from the matrix (never unpriced).
    assert (
        member_price_tag(
            source_map={"gtl": "slip"}, rule="full", pricing=_DRAW, slip_idx={"gtl": {}},
            product_id="gtl", age=35, declined=False,
            tier_category_id="catG", plan_code="P400",
            default_tier_category_id="catG", default_plan="P300",
        )
        == 400.0
    )


def test_per_member_slip_premium_reduces_group_to_member():
    # flat: the rate IS the per-employee premium (annual_premium is the group total).
    assert _per_member_slip_premium(
        PlanFinancials(rate_basis="flat", premium_rate=378.0, annual_premium=186732.0)
    ) == 378.0
    # per_1000_si: _member_financials already expressed annual_premium per member.
    assert _per_member_slip_premium(
        PlanFinancials(rate_basis="per_1000_si", annual_premium=405.0)
    ) == 405.0
    # group total + headcount → average per member.
    assert _per_member_slip_premium(
        PlanFinancials(rate_basis="per_1000_si", annual_premium=150.0, num_employees=2)
    ) == 75.0
    # tiered with an EO/ES/EC/EF table → the EMPLOYEE-ONLY (EO) rate is the
    # per-member employee premium (GHS Plan 1 EO = $1,200).
    assert _per_member_slip_premium(
        PlanFinancials(
            rate_basis="tiered",
            annual_premium=170400.0,
            rate_tiers={
                "EO": {"rate": 1200.0, "premium": 170400.0},
                "ES": {"rate": 3000.0, "premium": 0.0},
                "EC": {"rate": 3000.0, "premium": 0.0},
                "EF": {"rate": 4800.0, "premium": 0.0},
            },
        )
    ) == 1200.0
    # dependant-only tier table (no EO column) → not an employee premium → None.
    assert _per_member_slip_premium(
        PlanFinancials(
            rate_basis="tiered",
            rate_tiers={"SO": {"rate": 500.0, "premium": 0.0}},
        )
    ) is None
    # tiered with a bare group total and no tier breakdown → not reducible → None.
    assert _per_member_slip_premium(
        PlanFinancials(rate_basis="tiered", annual_premium=55547.0)
    ) is None
    assert _per_member_slip_premium(None) is None


def test_product_flex_pricing_mode_is_line_driven():
    # Data-driven from the insurance line — no hardcoded code list.
    assert Product(code="GTL").flex_pricing_mode == "age_banded"  # life
    assert Product(code="GCI").flex_pricing_mode == "age_banded"  # life
    assert Product(code="GHS").flex_pricing_mode == "plan_type"   # medical
    assert Product(code="GD").flex_pricing_mode == "plan_type"    # medical
    # A metadata line override flips it.
    assert (
        Product(code="GHS", product_metadata={"line": "life"}).flex_pricing_mode
        == "age_banded"
    )


# ── Dependant coverage pricing (additive over Employee-Only) ─────────────────

# A medical product priced family_group: manual family_tags = incremental over EO,
# keyed per tier (the dependant amount differs per plan).
_DEP_MANUAL = {
    "products": {
        "ghs": {
            "age_bands": [{"label": "All ages", "min": None, "max": None}],
            "price_tags": {"catH::P1": {"All ages": 1200}},
            "dependant": {
                "mode": "family_group",
                "scheme": "ec_es_ef",
                "family_tags": {
                    "catH::P1": {"spouse": 1800, "child": 1000, "both": 2600}
                },
            },
        },
        "gpa": {
            "age_bands": [{"label": "All ages", "min": None, "max": None}],
            "price_tags": {},
            "dependant": {"mode": "per_pax", "per_pax": {"catP::P1": {"flat": 150}}},
        },
    }
}
# Slip family index: EO baseline rate 1200, ES 3000 → spouse increment 1800, etc.
_FAM_SLIP = {"ghs": {"catH::P1": {"spouse": 1800.0, "child": 1000.0, "both": 2600.0}}}


def test_editor_pricing_mode_follows_tier_financial_mechanics():
    assert _financial_pricing_mode(None) == "plan_type"
    assert (
        _financial_pricing_mode(
            PlanFinancials(rate_basis="per_1000_si", premium_rate=0.072)
        )
        == "plan_type"
    )
    # A missing extracted table is still an age-banded data-quality issue when
    # the tier explicitly declares the age-banded rate basis.
    assert (
        _financial_pricing_mode(PlanFinancials(rate_basis="age_banded"))
        == "age_banded"
    )
    assert (
        _financial_pricing_mode(
            PlanFinancials(
                rate_basis="per_1000_si",
                voluntary_rates=[
                    VoluntaryRateBand(label="All ages", min=None, max=None, rate=1.0)
                ],
            )
        )
        == "age_banded"
    )


def test_family_role_from_counts():
    assert family_role(0, 0) is None  # Employee-Only
    assert family_role(1, 0) == "spouse"
    assert family_role(0, 2) == "child"
    assert family_role(1, 3) == "both"


def test_dependant_tag_family_group_manual_is_incremental_over_eo():
    base = dict(
        source="manual", pricing=_DEP_MANUAL, family_slip_idx=None,
        product_id="ghs", tier_category_id="catH", plan_code="P1",
    )
    # Employee-Only → covered, no dependant cost.
    assert dependant_tag(**base, spouse_count=0, child_count=0) == 0.0
    assert dependant_tag(**base, spouse_count=1, child_count=0) == 1800.0  # +spouse
    assert dependant_tag(**base, spouse_count=0, child_count=2) == 1000.0  # +children
    assert dependant_tag(**base, spouse_count=1, child_count=1) == 2600.0  # family


def test_dependant_tag_family_group_slip_reads_the_rate_table():
    # "slip" source prices the family increment off the slip's EO/ES/EC/EF rates.
    assert dependant_tag(
        source="slip", pricing={"products": {"ghs": {"dependant": {"mode": "family_group"}}}},
        family_slip_idx=_FAM_SLIP, product_id="ghs",
        tier_category_id="catH", plan_code="P1", spouse_count=1, child_count=0,
    ) == 1800.0


def test_dependant_tag_per_pax_is_flat_times_head_count():
    base = dict(
        source="manual", pricing=_DEP_MANUAL, family_slip_idx=None, product_id="gpa",
        tier_category_id="catP", plan_code="P1",
    )
    assert dependant_tag(**base, spouse_count=0, child_count=0) == 0.0
    assert dependant_tag(**base, spouse_count=1, child_count=2) == 450.0  # 3 x 150


def test_dependant_tag_unconfigured_product_is_none():
    assert dependant_tag(
        source="manual", pricing={"products": {}}, family_slip_idx=None,
        product_id="x", tier_category_id="c", plan_code="P", spouse_count=1, child_count=0,
    ) is None


def test_dependant_defaults_to_family_from_slip_when_unconfigured():
    # No dependant config at all, but the slip carries family rates AND the product
    # is funded from the slip → default to family_group priced off the slip.
    fam = {"ghs": {"catH::P1": {"spouse": 1800.0, "both": 2600.0}}}
    base = dict(pricing={"products": {}}, family_slip_idx=fam, product_id="ghs",
                tier_category_id="catH", plan_code="P1")
    assert dependant_tag(source="slip", **base, spouse_count=1, child_count=0) == 1800.0
    assert dependant_tag(source="slip", **base, spouse_count=0, child_count=0) == 0.0  # EO
    # Same product on the MANUAL source (no matrix tags) → unpriced, no default.
    assert dependant_tag(source="manual", **base, spouse_count=1, child_count=0) is None


def test_explicit_none_disables_dependant_pricing_even_with_slip():
    # A broker can turn dependant pricing OFF (explicit "none") despite slip rates.
    fam = {"ghs": {"catH::P1": {"spouse": 1800.0}}}
    assert dependant_tag(
        source="slip",
        pricing={"products": {"ghs": {"dependant": {"mode": "none"}}}},
        family_slip_idx=fam, product_id="ghs", tier_category_id="catH",
        plan_code="P1", spouse_count=1, child_count=0,
    ) is None


def test_member_coverage_tag_sums_employee_and_dependant():
    # Employee plan tag (1200, full rule) + family spouse increment (1800) = 3000.
    assert member_coverage_tag(
        source_map={}, rule="full", pricing=_DEP_MANUAL, slip_idx=None,
        family_slip_idx=None, product_id="ghs", age=40, declined=False,
        tier_category_id="catH", plan_code="P1",
        default_tier_category_id="catH", default_plan="P1",
        spouse_count=1, child_count=0,
    ) == 3000.0
    # Declined coverage (employee + dependants) costs no flex.
    assert member_coverage_tag(
        source_map={}, rule="full", pricing=_DEP_MANUAL, slip_idx=None,
        family_slip_idx=None, product_id="ghs", age=40, declined=True,
        tier_category_id="catH", plan_code="P1",
        default_tier_category_id="catH", default_plan="P1",
        spouse_count=1, child_count=0,
    ) is None


def test_member_coverage_tag_on_change_only_nets_the_employee_plan():
    # Employee keeps default plan (on_change → 0) but adds a spouse → just the
    # dependant increment (1800); the rule never nets the dependant portion.
    assert member_coverage_tag(
        source_map={}, rule="on_change", pricing=_DEP_MANUAL, slip_idx=None,
        family_slip_idx=None, product_id="ghs", age=40, declined=False,
        tier_category_id="catH", plan_code="P1",
        default_tier_category_id="catH", default_plan="P1",
        spouse_count=1, child_count=0,
    ) == 1800.0


def test_slip_family_increments_skip_unpriced_zero_columns():
    # Real slips often price only EO + EF (spouse/child columns left 0.0). A 0.0 is
    # an unpriced column, NOT a free tier — only the real EF increment survives.
    rt = {
        "EO": {"rate": 353.0, "premium": 1059.0},
        "ES": {"rate": 0.0, "premium": 0.0},
        "EC": {"rate": 0.0, "premium": 0.0},
        "EF": {"rate": 1112.0, "premium": 54488.0},
    }
    assert slip_family_increments(rt) == {"both": 759.0}
    # A genuinely-priced spouse tier that equals EO is a real $0 increment (kept).
    rt2 = {"EO": {"rate": 300.0}, "ES": {"rate": 300.0}, "EF": {"rate": 500.0}}
    assert slip_family_increments(rt2) == {"spouse": 0.0, "both": 200.0}
    # No EO baseline → nothing derivable.
    assert slip_family_increments({"EF": {"rate": 500.0}}) == {}
    assert slip_family_increments(None) == {}


def test_family_slip_incr_plan_code_fallback():
    assert family_slip_incr(_FAM_SLIP, "ghs", "catH::P1", "spouse") == 1800.0
    # Drifted category half, unambiguous plan_code still resolves.
    assert family_slip_incr(_FAM_SLIP, "ghs", "OTHER::P1", "child") == 1000.0
    assert family_slip_incr(_FAM_SLIP, "ghs", "catH::P1", "both") == 2600.0
    assert family_slip_incr(None, "ghs", "catH::P1", "spouse") is None


def test_dependant_pricing_breakdown_shapes_family_and_per_pax():
    fam = dependant_pricing_breakdown(
        pricing=_DEP_MANUAL, family_slip_idx=None, source="manual",
        product_id="ghs", tier_category_id="catH", plan_code="P1",
    )
    assert fam["mode"] == "family_group"
    assert fam["scheme"] == "ec_es_ef"
    assert {f["role"]: f["amount"] for f in fam["family"]} == {
        "spouse": 1800.0, "child": 1000.0, "both": 2600.0
    }
    pax = dependant_pricing_breakdown(
        pricing=_DEP_MANUAL, family_slip_idx=None, source="manual",
        product_id="gpa", tier_category_id="catP", plan_code="P1",
    )
    assert pax["mode"] == "per_pax" and pax["per_pax_rate"] == 150


def test_dependant_override_never_propagates_to_a_sibling_cohort() -> None:
    pricing = {
        "products": {
            "p": {
                "dependant": {
                    "mode": "per_pax",
                    "per_pax": {"executives::P1": {"flat": 150}},
                }
            }
        }
    }
    common = dict(
        source="manual",
        pricing=pricing,
        family_slip_idx=None,
        product_id="p",
        plan_code="P1",
        spouse_count=1,
        child_count=0,
    )
    assert dependant_tag(**common, tier_category_id="executives") == 150.0
    assert dependant_tag(**common, tier_category_id="staff") is None


def test_validate_pricing_shape_flags_bad_dependant_block():
    bad = {
        "products": {
            "p": {
                "age_bands": [],
                "price_tags": {},
                "dependant": {
                    "mode": "bogus",
                    "participation": {"catX::P1": "sometimes"},
                    "scheme": "nope",
                    "family_tags": {"catX::P1": {"spouse": -5, "cousin": 10}},
                    "per_pax": {"catX::P1": {"flat": -1}},
                },
            }
        }
    }
    errs = validate_pricing_shape(bad)
    assert any("dependant mode 'bogus'" in e for e in errs)
    assert any("dependant participation 'sometimes'" in e for e in errs)
    assert any("dependant scheme 'nope'" in e for e in errs)
    assert any("family_tags 'catX::P1/spouse' must be ≥ 0" in e for e in errs)
    assert any("family_tags role 'cousin'" in e for e in errs)
    assert any("per_pax 'catX::P1' flat must be ≥ 0" in e for e in errs)
    # A well-formed dependant block passes.
    assert validate_pricing_shape(_DEP_MANUAL) == []


def test_tier_scoped_dependant_participation_is_exact_and_removable():
    pricing = {
        "products": {
            "p": {
                "dependant": {
                    "participation": {
                        "executives::P1": "compulsory",
                        "staff::P1": "none",
                    }
                }
            }
        }
    }
    assert effective_dependant_participation(
        pricing, "p", "executives::P1", "voluntary"
    ) == "compulsory"
    assert effective_dependant_participation(
        pricing, "p", "staff::P1", "compulsory"
    ) is None
    # No plan-code fallback: an edit to executives must not leak to management.
    assert effective_dependant_participation(
        pricing, "p", "management::P1", "voluntary"
    ) == "voluntary"


def test_tier_scoped_dependant_mode_overrides_only_that_tier():
    slip = {
        "p": {
            "cat-a::1": {"per_pax": 100.0},
            "cat-b::2": {"spouse": 50.0},
        }
    }
    pricing = {
        "products": {
            "p": {
                "dependant": {
                    "modes": {"cat-a::1": "none"},
                }
            }
        }
    }
    assert validate_pricing_shape(pricing) == []
    assert _effective_dependant_mode(
        pricing, "p", "slip", slip, "cat-a::1"
    ) == "none"
    assert _effective_dependant_mode(
        pricing, "p", "slip", slip, "cat-b::2"
    ) == "family_group"


def test_dependant_pricing_out_honors_slip_default():
    # The options-endpoint helper must price an UNCONFIGURED product from the slip
    # (default source = slip, effective mode = family_group) — not read it as 'none'
    # — so the elections UI matches the snapshot/benefit-statement charge.
    from app.services.cohort_tiers import CohortTier, ProductTierSet
    from app.services.enrollment_elections import _dependant_pricing_out

    ts = ProductTierSet(
        product_id="ghs", product_code="GHS",
        employee_participation="compulsory", dependant_participation=None,
        baseline_tier_category_id="catH", baseline_plan_code="P1",
        allow_plan_change=False, can_decline=False,
        tiers=[CohortTier(
            tier_category_id="catH", plan_code="P1", label="Plan 1",
            participation="compulsory", direction="same", is_baseline=True,
            financials=None,
        )],
    )
    fam = {"ghs": {"catH::P1": {"spouse": 1800.0, "both": 2600.0}}}
    # No config bag, empty source map → product defaults to slip → family_group.
    out = _dependant_pricing_out({"products": {}}, fam, {}, ts)
    assert out is not None
    assert out.mode == "family_group"
    by = out.by_tier["catH::P1"]
    assert by.mode == "family_group"
    amounts = {f.role: f.amount for f in by.family}
    assert amounts["spouse"] == 1800.0 and amounts["both"] == 2600.0
    # A product with no slip family rates and no config → None (nothing to price).
    out_none = _dependant_pricing_out({"products": {}}, {}, {}, ts)
    assert out_none is None


def test_dependant_pricing_out_preserves_each_tier_mode() -> None:
    from app.services.cohort_tiers import CohortTier, ProductTierSet
    from app.services.enrollment_elections import _dependant_pricing_out

    ts = ProductTierSet(
        product_id="mixed",
        product_code="MIXED",
        employee_participation="compulsory",
        dependant_participation="voluntary",
        baseline_tier_category_id="cat",
        baseline_plan_code="1",
        allow_plan_change=True,
        can_decline=False,
        tiers=[
            CohortTier(
                tier_category_id="cat",
                plan_code=plan,
                label=f"Plan {plan}",
                participation="voluntary",
                direction="same",
                is_baseline=plan == "1",
                financials=None,
            )
            for plan in ("1", "2", "3")
        ],
    )
    pricing = {
        "products": {
            "mixed": {
                "dependant": {
                    "modes": {
                        "cat::1": "family_group",
                        "cat::2": "per_pax",
                        "cat::3": "none",
                    },
                    "family_tags": {
                        "cat::1": {"spouse": 10, "child": 8, "both": 15}
                    },
                    "per_pax": {"cat::2": {"flat": 5}},
                }
            }
        }
    }
    out = _dependant_pricing_out(pricing, None, {}, ts)
    assert out is not None
    assert {key: row.mode for key, row in out.by_tier.items()} == {
        "cat::1": "family_group",
        "cat::2": "per_pax",
        "cat::3": "none",
    }
    assert out.by_tier["cat::1"].family[0].amount is not None
    assert out.by_tier["cat::2"].per_pax_rate == 5.0


def test_slip_dependant_for_tier_picks_per_pax_vs_family():
    # GCGP-style flat per-member table with a separate Dependents rate → the
    # dependant's FULL per-dependant rate (not an increment).
    assert _slip_dependant_for_tier(
        PlanFinancials(rate_basis="flat", premium_rate=378.0, dependant_rate=396.9)
    ) == {"per_pax": 396.9}
    # A combined "Employees / Dependents" rate → dependant rate equals it.
    assert _slip_dependant_for_tier(
        PlanFinancials(rate_basis="flat", premium_rate=454.0, dependant_rate=454.0)
    ) == {"per_pax": 454.0}
    # No dependant rate → no dependant pricing.
    assert _slip_dependant_for_tier(
        PlanFinancials(rate_basis="flat", premium_rate=454.0)
    ) == {}
    # An EO/ES/EC/EF rate table → family increments (not per_pax).
    fin = PlanFinancials(rate_basis="tiered", premium_rate=None, rate_tiers={
        "EO": {"rate": 100.0}, "ES": {"rate": 150.0},
        "EC": {"rate": 140.0}, "EF": {"rate": 200.0},
    })
    assert _slip_dependant_for_tier(fin) == {"spouse": 50.0, "child": 40.0, "both": 100.0}


def test_per_dependant_mode_from_slip_charges_per_head():
    # GCGP: slip lists a per-dependant rate → per_pax mode by default, drawn per
    # covered dependant ON TOP of the employee tag (additive).
    slip = {"gcgp": {"catG::1": {"per_pax": 396.9}}}
    assert _slip_dependant_shape(slip, "gcgp", "catG::1") == "per_pax"
    assert _effective_dependant_mode(None, "gcgp", "slip", slip, "catG::1") == "per_pax"

    base = dict(
        source="slip", pricing=None, family_slip_idx=slip, product_id="gcgp",
        tier_category_id="catG", plan_code="1",
    )
    # Employee-Only → no dependant cost.
    assert dependant_tag(**base, spouse_count=0, child_count=0) == 0.0
    # Spouse + 1 child = 396.90 x 2 (the +396.90 +396.90 on top of the 378 tag).
    assert dependant_tag(**base, spouse_count=1, child_count=1) == 793.8

    bd = dependant_pricing_breakdown(
        source="slip", pricing=None, family_slip_idx=slip, product_id="gcgp",
        tier_category_id="catG", plan_code="1",
    )
    assert bd["mode"] == "per_pax" and bd["per_pax_rate"] == 396.9


def test_dependant_override_corrects_a_wrong_slip_rate():
    # Under the "slip" source, a manual per_pax value overrides the slip's Dependents
    # rate (correcting a wrong extraction); a blank tier falls back to the slip.
    slip = {"gcgp": {"catG::1": {"per_pax": 396.9}, "catG::2": {"per_pax": 454.0}}}
    pricing = {"products": {"gcgp": {"dependant": {
        "mode": "per_pax", "per_pax": {"catG::1": {"flat": 250.0}},
    }}}}
    base = dict(
        source="slip", pricing=pricing, family_slip_idx=slip, product_id="gcgp",
        spouse_count=1, child_count=0,
    )
    # Plan 1 overridden to 250 (was 396.90 on the slip).
    assert dependant_tag(**base, tier_category_id="catG", plan_code="1") == 250.0
    # Plan 2 has no override → still prices from the slip (454).
    assert dependant_tag(**base, tier_category_id="catG", plan_code="2") == 454.0


def test_family_rates_still_default_to_family_group():
    # An EO/ES/EC/EF slip table is NOT per_pax → family_group default.
    fam = {"ghs": {"catH::P1": {"spouse": 1800.0, "child": 1500.0, "both": 2600.0}}}
    assert _slip_dependant_shape(fam, "ghs", "catH::P1") == "family_group"
    assert _effective_dependant_mode(None, "ghs", "slip", fam, "catH::P1") == "family_group"


def test_mixed_mode_product_prices_each_tier_by_its_own_shape():
    # A product whose tiers MIX a per-dependant rate (tier1) and an EO/ES/EC/EF table
    # (tier2): the mode must be resolved per tier, not forced to one mode for the
    # whole product (which would strand the family tier at $0).
    mixed = {
        "prodX": {
            "catX::1": {"per_pax": 396.9},
            "catX::2": {"spouse": 50.0, "child": 40.0, "both": 100.0},
        }
    }
    assert _slip_dependant_shape(mixed, "prodX", "catX::1") == "per_pax"
    assert _slip_dependant_shape(mixed, "prodX", "catX::2") == "family_group"

    # tier1 prices per dependant (396.90 x 2 covered).
    assert (
        dependant_tag(
            source="slip", pricing=None, family_slip_idx=mixed, product_id="prodX",
            tier_category_id="catX", plan_code="1", spouse_count=1, child_count=1,
        )
        == 793.8
    )
    # tier2 prices off its family table (a spouse → +50), NOT $0/None.
    assert (
        dependant_tag(
            source="slip", pricing=None, family_slip_idx=mixed, product_id="prodX",
            tier_category_id="catX", plan_code="2", spouse_count=1, child_count=0,
        )
        == 50.0
    )


def test_slip_premium_for_plan_code_fallback():
    assert slip_premium_for(_SLIP, "gtl", "catG::P400") == 420.0
    # Drifted category half, unambiguous plan_code still resolves.
    assert slip_premium_for(_SLIP, "gtl", "OTHER::P400") == 420.0
    assert slip_premium_for(_SLIP, "gtl", "catG::NOPE") is None
    assert slip_premium_for(None, "gtl", "catG::P400") is None


def test_first_category_per_product_is_deterministic():
    from app.services.cohort_tiers import first_category_per_product

    matched = [
        {"category_id": "c1"},
        {"category_id": "c2"},  # second match for the same product
        {"category_id": "c3"},
    ]
    product_of = {"c1": "prodA", "c2": "prodA", "c3": "prodB"}
    # First matched per product wins, stable by matched-list order.
    assert first_category_per_product(matched, product_of) == {
        "prodA": "c1",
        "prodB": "c3",
    }
    # Entries with no resolvable product (or missing id) are skipped.
    assert first_category_per_product([{"category_id": "x"}], {}) == {}
    assert first_category_per_product([{}], {"c1": "p"}) == {}


def test_parse_dob_handles_excel_midnight_string():
    # Excel rosters store DOB as "YYYY-MM-DD 00:00:00" — must parse, not drop.
    assert parse_dob("1958-02-19 00:00:00") == date(1958, 2, 19)
    assert parse_dob("1990-06-15") == date(1990, 6, 15)
    assert parse_dob("15/06/1990") == date(1990, 6, 15)
    assert parse_dob("") is None
    assert parse_dob(None) is None
    assert parse_dob("not a date") is None


def test_age_as_of_birthday_boundary():
    dob = date(1990, 6, 15)
    assert age_as_of(dob, date(2026, 6, 14)) == 35   # day before birthday
    assert age_as_of(dob, date(2026, 6, 15)) == 36   # on birthday
    assert age_as_of(dob, date(2026, 6, 16)) == 36
    # A future DOB clamps to 0 rather than going negative.
    assert age_as_of(date(2030, 1, 1), date(2026, 1, 1)) == 0


def test_validate_pricing_shape_accepts_well_formed():
    assert validate_pricing_shape(_PRICING) == []
    assert validate_pricing_shape({"products": {}}) == []


def test_validate_pricing_shape_flags_malformed():
    bad = {
        "products": {
            "p": {
                "age_bands": [{"label": "x", "min": 50, "max": 30}],  # min > max
                "price_tags": {"k": {"x": -5}},  # negative amount
            }
        }
    }
    errs = validate_pricing_shape(bad)
    assert any("min > max" in e for e in errs)
    assert any("must be ≥ 0" in e for e in errs)
    # A non-dict products bag is rejected outright.
    assert validate_pricing_shape({"products": []})


# ── Life voluntary: age-banded slip price tag + dependant eligibility ─────────

_GTL_BANDS = [
    VoluntaryRateBand(label="<=34", min=None, max=34, rate=0.88),
    VoluntaryRateBand(label="45-49", min=45, max=49, rate=1.65),
    VoluntaryRateBand(label="50-54", min=50, max=54, rate=2.04),
]


class _Tier:
    def __init__(self, tcid, plan, fin):
        self.tier_category_id, self.plan_code, self.financials = tcid, plan, fin


class _TierSet:
    def __init__(self, pid, tiers):
        self.product_id, self.tiers = pid, tiers


def test_voluntary_life_slip_price_tag_is_age_banded() -> None:
    # A voluntary life tier carries an age-banded spec; the price tag is computed
    # from the member's age (SI/1000 x rate[band]), while flat tiers stay numbers.
    vol = _Tier("a", "10", PlanFinancials(
        sum_insured=500_000.0, rate_basis="per_1000_si", voluntary_rates=_GTL_BANDS))
    flat = _Tier("b", "2", PlanFinancials(
        annual_premium=454.0, rate_basis="flat", premium_rate=454.0))
    idx = slip_premium_index_from(
        {"p1": _TierSet("p1", [vol]), "p2": _TierSet("p2", [flat])})
    assert isinstance(idx["p1"]["a::10"], dict)  # age-banded spec, not a number
    assert slip_premium_for(idx, "p1", "a::10", 47) == 825.0  # 500000/1000*1.65
    assert slip_premium_for(idx, "p1", "a::10", 30) == 440.0
    assert slip_premium_for(idx, "p1", "a::10", None) is None  # needs an age
    assert slip_premium_for(idx, "p2", "b::2", 40) == 454.0  # flat unchanged


def test_dependant_age_limits_config_over_defaults() -> None:
    # Unset → defaults; a partial override replaces only the bounds it sets.
    assert dependant_age_limits(None, "p1")["child"] == {"min": 0, "max": 25}
    pricing = {"products": {"p1": {"dependant": {"age_limits": {"child": {"max": 21}}}}}}
    limits = dependant_age_limits(pricing, "p1")
    assert limits["child"] == {"min": 0, "max": 21}  # max overridden, min default
    assert limits["spouse"] == {"min": 18, "max": 70}  # untouched role keeps default


def test_dependant_age_limits_scheme_default_then_product_override() -> None:
    # The __dep_age__ stamp (scheme-level default) overlays the hardcoded default;
    # a product's own age_limits override wins over both (most-specific-wins).
    scheme_default = {"child": {"max": 22}, "spouse": {"max": 65}}
    stamped = {"__dep_age__": scheme_default}
    limits = dependant_age_limits(stamped, "p1")
    assert limits["child"] == {"min": 0, "max": 22}  # scheme default over hardcoded
    assert limits["spouse"] == {"min": 18, "max": 65}
    # Product override beats the scheme-level default for that role only.
    stamped_with_product = {
        "__dep_age__": scheme_default,
        "products": {"p1": {"dependant": {"age_limits": {"child": {"max": 18}}}}},
    }
    limits = dependant_age_limits(stamped_with_product, "p1")
    assert limits["child"] == {"min": 0, "max": 18}  # product wins
    assert limits["spouse"] == {"min": 18, "max": 65}  # scheme default still applies


def _dep(relationship: str, dob: str) -> Dependant:
    return Dependant(attribute_values={"relationship": relationship, "dob": dob})


def test_dependant_eligibility_by_age_window() -> None:
    ref = date(2026, 1, 1)
    limits = {"spouse": {"min": 18, "max": 70}, "child": {"min": 0, "max": 25}}
    # In-window spouse + child are eligible.
    assert _dependant_eligible(_dep("spouse", "1980-06-01"), limits, ref) is True
    assert _dependant_eligible(_dep("child", "2024-03-01"), limits, ref) is True
    # An over-age "child" (ANB 32) falls outside 0-25 → not eligible.
    assert _dependant_eligible(_dep("child", "1995-01-01"), limits, ref) is False
    # No DOB / unclassifiable role → kept (can't prove ineligibility).
    assert _dependant_eligible(_dep("child", ""), limits, ref) is True
    assert _dependant_eligible(_dep("cousin", "1995-01-01"), limits, ref) is True


def test_dependant_eligibility_window_is_anb_of_renewal() -> None:
    # Limits are age-NEXT-birthday as of the renewal date: a child who is 25
    # (last birthday) at renewal is ANB 26 → outside a max-25 window, while a
    # 24-year-old (ANB 25) is exactly at the bound and stays covered.
    ref = date(2026, 1, 1)
    limits = {"child": {"min": 0, "max": 25}}
    assert _dependant_eligible(_dep("child", "2000-06-01"), limits, ref) is False  # 25 → ANB 26
    assert _dependant_eligible(_dep("child", "2001-06-01"), limits, ref) is True  # 24 → ANB 25


def test_validate_pricing_age_limits_shape() -> None:
    bad = {"products": {"p1": {"age_bands": [], "price_tags": {}, "dependant": {
        "age_limits": {"child": {"min": 30, "max": 10}, "uncle": {"max": 5}}}}}}
    errs = validate_pricing_shape(bad)
    assert any("min > max" in e for e in errs)
    assert any("role 'uncle'" in e for e in errs)


def test_voluntary_rates_round_trip_through_member_financials_no_crash() -> None:
    # Regression for the model_copy/model_dump crash: bands arrive as raw JSON
    # dicts from plan_assignments; member_financials must coerce them to
    # VoluntaryRateBand so slip_premium_index_from's b.model_dump() doesn't blow up.
    from app.services.plan_hydration import member_financials

    pa = {"plan_code": "10", "basis": "500000.0", "rate_basis": "per_1000_si",
          "voluntary_rates": [{"label": "45-49", "min": 45, "max": 49, "rate": 1.65}]}
    fin = member_financials(pa, None)  # age None: the index is built age-agnostic
    assert all(isinstance(b, VoluntaryRateBand) for b in fin.voluntary_rates)
    idx = slip_premium_index_from({"p": _TierSet("p", [_Tier("a", "10", fin)])})
    # No AttributeError, and the stored spec carries the dumped bands.
    assert idx["p"]["a::10"]["voluntary_rates"][0]["rate"] == 1.65
    assert slip_premium_for(idx, "p", "a::10", 47) == 825.0


def test_saved_voluntary_rates_override_system_recommendation() -> None:
    slip_idx = {
        "p": {
            "a::10": {
                "basis": 500_000.0,
                "voluntary_rates": [
                    {"label": "45-49", "min": 45, "max": 49, "rate": 1.65}
                ],
            }
        }
    }
    pricing = {
        "products": {
            "p": {
                "age_bands": [],
                "price_tags": {},
                "voluntary_rates": [
                    {"label": "45-49", "min": 45, "max": 49, "rate": 2.0}
                ],
            }
        }
    }
    tag = member_price_tag(
        source_map=None,
        rule="full",
        pricing=pricing,
        slip_idx=slip_idx,
        product_id="p",
        age=47,
        declined=False,
        tier_category_id="a",
        plan_code="10",
        default_tier_category_id="a",
        default_plan="10",
    )
    assert tag == 1000.0


def test_tier_voluntary_rates_override_shared_schedule_only_for_that_tier() -> None:
    slip_idx = {
        "p": {
            key: {
                "basis": 500_000.0,
                "voluntary_rates": [
                    {"label": "45-49", "min": 45, "max": 49, "rate": 1.65}
                ],
            }
            for key in ("a::10", "b::10")
        }
    }
    pricing = {
        "products": {
            "p": {
                "voluntary_rates": [
                    {"label": "45-49", "min": 45, "max": 49, "rate": 2.0}
                ],
                "voluntary_rates_by_tier": {
                    "a::10": [
                        {"label": "45-49", "min": 45, "max": 49, "rate": 3.0}
                    ]
                },
            }
        }
    }

    def price(category_id: str) -> float | None:
        return member_price_tag(
            source_map=None,
            rule="full",
            pricing=pricing,
            slip_idx=slip_idx,
            product_id="p",
            age=47,
            declined=False,
            tier_category_id=category_id,
            plan_code="10",
            default_tier_category_id=category_id,
            default_plan="10",
        )

    assert price("a") == 1500.0
    assert price("b") == 1000.0


def test_validate_pricing_rejects_bad_voluntary_rate_override() -> None:
    pricing = {
        "products": {
            "p": {
                "age_bands": [],
                "price_tags": {},
                "voluntary_rates": [
                    {"label": "45-49", "min": 50, "max": 45, "rate": -1}
                ],
            }
        }
    }
    errors = validate_pricing_shape(pricing)
    assert any("non-negative" in error for error in errors)
    assert any("min > max" in error for error in errors)


def test_validate_pricing_checks_tier_specific_voluntary_rates() -> None:
    errors = validate_pricing_shape(
        {
            "products": {
                "p": {
                    "voluntary_rates_by_tier": {
                        "a::10": [
                            {
                                "label": "Invalid",
                                "min": 50,
                                "max": 45,
                                "rate": -1,
                            }
                        ]
                    }
                }
            }
        }
    )
    assert any("tier 'a::10'" in error for error in errors)
    assert any("non-negative" in error for error in errors)
    assert any("min > max" in error for error in errors)


def test_validate_pricing_rejects_voluntary_rate_gaps_and_overlaps() -> None:
    def errors_for(rates: list[dict[str, object]]) -> list[str]:
        return validate_pricing_shape(
            {
                "products": {
                    "p": {
                        "age_bands": [],
                        "price_tags": {},
                        "voluntary_rates": rates,
                    }
                }
            }
        )

    overlap = errors_for(
        [
            {"label": "Under 40", "min": None, "max": 40, "rate": 1.0},
            {"label": "40+", "min": 40, "max": None, "rate": 2.0},
        ]
    )
    gap = errors_for(
        [
            {"label": "Under 40", "min": None, "max": 39, "rate": 1.0},
            {"label": "45+", "min": 45, "max": None, "rate": 2.0},
        ]
    )
    assert any("overlap" in error for error in overlap)
    assert any("40-44 uncovered" in error for error in gap)
    assert any("at least one band" in error for error in errors_for([]))


def test_gst_gross_up_on_price_tags() -> None:
    # The __gst__ stamp (product override or scheme default) grosses matrix and
    # slip tags at the output boundary — raw stored amounts stay exclusive.
    pricing = {**_PRICING, "__gst__": {"default": 1.09, "products": {}}}
    tag = member_price_tag(
        source_map=None, rule="full", pricing=pricing, slip_idx=None,
        product_id="prodA", age=35, declined=False,
        tier_category_id="cat1", plan_code="GOLD",
        default_tier_category_id="cat1", default_plan="GOLD",
    )
    assert tag == 1635.0  # 1500 x 1.09
    # A per-product multiplier beats the scheme default.
    pricing["__gst__"] = {"default": 1.09, "products": {"prodA": 1.08}}
    tag = member_price_tag(
        source_map=None, rule="full", pricing=pricing, slip_idx=None,
        product_id="prodA", age=35, declined=False,
        tier_category_id="cat1", plan_code="GOLD",
        default_tier_category_id="cat1", default_plan="GOLD",
    )
    assert tag == 1620.0  # 1500 x 1.08
    # No stamp → raw exclusive amount (unchanged behavior).
    tag = member_price_tag(
        source_map=None, rule="full", pricing=_PRICING, slip_idx=None,
        product_id="prodA", age=35, declined=False,
        tier_category_id="cat1", plan_code="GOLD",
        default_tier_category_id="cat1", default_plan="GOLD",
    )
    assert tag == 1500.0
    # And the raw price_tag_for lookup itself is never grossed.
    assert price_tag_for(pricing, "prodA", "cat1::GOLD", 35) == 1500.0


def test_gst_explicit_off_overrides_scheme_default_for_flex_tag() -> None:
    from app.services.flex_pricing_resolver import (
        gst_multiplier_for,
        product_premium_multiplier,
    )

    # Scheme default ON (1.09); product prodA has an EXPLICIT "off" → stamped as
    # 1.0 in products. gst_multiplier_for (flex tag) must use the product's 1.0,
    # not the scheme default.
    pricing = {"__gst__": {"default": 1.09, "products": {"prodA": 1.0}}}
    assert gst_multiplier_for(pricing, "prodA") == 1.0
    # A product with no explicit opinion inherits the scheme default for flex.
    assert gst_multiplier_for(pricing, "other") == 1.09

    # product_premium_multiplier NEVER uses the scheme default: the premium of a
    # product with no explicit GST opinion is not grossed, even under a scheme
    # default (so the benefit statement and enrollment options agree).
    assert product_premium_multiplier(pricing, "other") == 1.0
    assert product_premium_multiplier(pricing, "prodA") == 1.0
    pricing_on = {"__gst__": {"default": 1.0, "products": {"prodA": 1.09}}}
    assert product_premium_multiplier(pricing_on, "prodA") == 1.09


def test_gst_multiplier_helper_tri_state() -> None:
    from app.services.product_terms import gst_multiplier

    assert gst_multiplier(None, None) == 1.0   # inherit → no gross at this layer
    assert gst_multiplier(False, None) == 1.0  # explicit off
    assert gst_multiplier(True, None) == 1.09  # on, default 9%
    assert gst_multiplier(True, 8.0) == 1.08   # on, explicit rate


def test_gst_gross_up_applies_to_financials() -> None:
    from app.services.plan_hydration import apply_gst_to_financials

    fin = PlanFinancials(
        sum_insured=500000.0, premium_rate=1.65, annual_premium=825.0,
        dependant_rate=396.9, rate_basis="per_1000_si",
        rate_tiers={"EO": {"rate": 100.0, "premium": 1200.0}},
        voluntary_rates=[VoluntaryRateBand(label="45-49", min=45, max=49, rate=1.65)],
    )
    out = apply_gst_to_financials(fin, 1.09)
    assert out.gst_included is True
    assert out.annual_premium == 899.25
    assert out.premium_rate == 1.8
    assert out.dependant_rate == 432.62
    assert out.rate_tiers["EO"]["rate"] == 109.0
    assert out.voluntary_rates[0].rate == 1.8
    # Coverage amounts are not premiums — untouched.
    assert out.sum_insured == 500000.0
    # 1.0 multiplier is a no-op (same object, no flag).
    same = apply_gst_to_financials(fin, 1.0)
    assert same is fin and same.gst_included is False


def test_age_limits_reject_boolean_bounds() -> None:
    # bool is an int subclass; a JSON `true` must NOT pass as an age bound.
    bad = {"products": {"p": {"age_bands": [], "price_tags": {}, "dependant": {
        "age_limits": {"child": {"max": True}}}}}}
    assert any("child.max" in e for e in validate_pricing_shape(bad))
    # And it isn't silently applied on the read side either → falls back to default.
    assert dependant_age_limits(bad, "p")["child"]["max"] == 25


# ---- Dependant OPTION rows (stick to the elected employee plan) -------------


def test_dependant_option_spec_flat_from_si_and_per_1000_rate():
    from app.services.flex_pricing_resolver import _dependant_option_spec

    # GPA "Spouse (Option 1)": SI 20,000 at 0.072 per S$1,000 -> $1.44/dependant.
    spec = _dependant_option_spec(
        {"plan_code": "23", "sum_insured": 20000.0, "premium_rate": 0.072,
         "rate_basis": "per_1000_si", "member_scope": "dependant"}
    )
    assert spec == 1.44

    # Age-banded row (GTL Spouse): basis + voluntary_rates -> deferred spec.
    banded = _dependant_option_spec(
        {"plan_code": "1", "basis": "40000.0", "rate_basis": "age_banded",
         "voluntary_rates": [{"label": "34 & below", "min": None, "max": 34, "rate": 0.88}],
         "member_scope": "dependant"}
    )
    assert isinstance(banded, dict)
    assert banded["basis"] == 40000.0

    # Nothing priceable -> None (never guess).
    assert _dependant_option_spec({"plan_code": "9"}) is None


def test_option_amount_flat_and_age_banded():
    from app.services.flex_pricing_resolver import option_amount

    assert option_amount(1.44, None) == 1.44
    spec = {"basis": 40000.0, "voluntary_rates": [
        {"label": "34 & below", "min": None, "max": 34, "rate": 0.88},
        {"label": "35 to 44", "min": 35, "max": 44, "rate": 1.32},
    ]}
    assert option_amount(spec, 30) == 35.2   # 40k/1000 x 0.88
    assert option_amount(spec, 40) == 52.8   # 40k/1000 x 1.32
    corrected = [
        {"label": "34 & below", "min": None, "max": 34, "rate": 1.0},
        {"label": "35 to 44", "min": 35, "max": 44, "rate": 2.0},
    ]
    assert option_amount(spec, 40, corrected) == 80.0
    assert option_amount(spec, None) is None  # unknown age -> unpriced
    assert option_amount(None, 30) is None


def test_dependant_tag_slip_options_prices_each_covered_dependant():
    idx = {"prod-1": {"cat-opt2::16": {"options": {
        "spouse": 2.88,
        "child": {"basis": 30000.0, "voluntary_rates": [
            {"label": "34 & below", "min": None, "max": 34, "rate": 0.88},
        ]},
    }}}}
    common = dict(
        source="slip", pricing=None, family_slip_idx=idx, product_id="prod-1",
        tier_category_id="cat-opt2", plan_code="16",
    )
    # spouse (flat) + child aged 8 (banded 30k/1000 x 0.88 = 26.4) = 29.28
    tag = dependant_tag(
        **common, spouse_count=1, child_count=1,
        dep_profiles=[("spouse", 40), ("child", 8)],
    )
    assert tag == 29.28
    # Employee-Only -> 0 (covered, no dependant cost).
    assert dependant_tag(**common, spouse_count=0, child_count=0, dep_profiles=[]) == 0.0
    # A dependant whose age is unknown on an age-banded option -> whole tag
    # unpriced (surfaced by the unpriced-election guard), never a partial sum.
    assert dependant_tag(
        **common, spouse_count=0, child_count=1, dep_profiles=[("child", None)]
    ) is None
    corrected = {
        "products": {
            "prod-1": {
                "voluntary_rates": [
                    {"label": "All ages", "min": None, "max": None, "rate": 2.0}
                ]
            }
        }
    }
    assert dependant_tag(
        **{**common, "pricing": corrected}, spouse_count=0, child_count=1,
        dep_profiles=[("child", 8)],
    ) == 60.0
    # An explicit manual mode still wins over the slip options shape.
    manual = {"products": {"prod-1": {"dependant": {"mode": "per_pax",
              "per_pax": {"cat-opt2::16": {"flat": 10.0}}}}}}
    assert dependant_tag(
        source="slip", pricing=manual, family_slip_idx=idx, product_id="prod-1",
        tier_category_id="cat-opt2", plan_code="16",
        spouse_count=1, child_count=1, dep_profiles=[("spouse", 40), ("child", 8)],
    ) == 20.0


def test_composition_amounts_and_overlay_merge_precedence():
    from app.services.flex_pricing_resolver import (
        _composition_amounts,
        _merge_family_overlay,
    )

    # VDL dependants-sheet row: SO/CO/SC standalone dependant premiums.
    amounts = _composition_amounts({"rate_tiers": {
        "SO": {"rate": 407.0, "premium": 0.0},
        "CO": {"rate": 407.0, "premium": 0.0},
        "SC": {"rate": 678.0, "premium": 0.0},
    }})
    assert amounts == {"spouse": 407.0, "child": 407.0, "both": 678.0}

    # An employee tier's own slip family pricing wins over the overlay.
    base = {"p": {"k1": {"spouse": 100.0}}}
    overlay = {"p": {"k1": {"options": {"spouse": 1.0}}, "k2": {"spouse": 407.0}}}
    merged = _merge_family_overlay(base, overlay)
    assert merged["p"]["k1"] == {"spouse": 100.0}
    assert merged["p"]["k2"] == {"spouse": 407.0}


def test_slip_options_shape_detected_and_breakdown_lists_roles():
    idx = {"prod-1": {"cat-a::1": {"options": {"spouse": 2.88, "child": None}}}}
    assert _slip_dependant_shape(idx, "prod-1", "cat-a::1") == "slip_options"
    bd = dependant_pricing_breakdown(
        pricing=None, family_slip_idx=idx, source="slip",
        product_id="prod-1", tier_category_id="cat-a", plan_code="1",
    )
    assert bd["mode"] == "slip_options"
    assert {f["role"]: f["amount"] for f in bd["family"]} == {"spouse": 2.88}


# ---- Freestanding dependant option LEVELS (rule 4 — member elects one) -------


def _choices_idx():
    """A GTL-shaped index: three unlinked Spouse levels, age-banded pricing."""
    bands = [
        {"label": "34 & below", "min": None, "max": 34, "rate": 0.88},
        {"label": "35 to 44", "min": 35, "max": 44, "rate": 1.32},
    ]
    return {"prod-1": {"cat-mgr::4": {"choices": {"spouse": [
        {"category_id": "dep-s-20k", "label": "Spouse", "sum_insured": 20000.0,
         "spec": {"basis": 20000.0, "voluntary_rates": bands}},
        {"category_id": "dep-s-40k", "label": "Spouse", "sum_insured": 40000.0,
         "spec": {"basis": 40000.0, "voluntary_rates": bands}},
        {"category_id": "dep-s-60k", "label": "Spouse", "sum_insured": 60000.0,
         "spec": {"basis": 60000.0, "voluntary_rates": bands}},
    ]}}}}


def test_dependant_tag_choices_price_the_elected_level():
    idx = _choices_idx()
    assert _slip_dependant_shape(idx, "prod-1", "cat-mgr::4") == "slip_options"
    common = dict(
        source="slip", pricing=None, family_slip_idx=idx, product_id="prod-1",
        tier_category_id="cat-mgr", plan_code="4",
        spouse_count=1, child_count=0, dep_profiles=[("spouse", 40)],
    )
    # Elected S$40k level, spouse aged 40 -> 40k/1000 x 1.32 = 52.8.
    assert dependant_tag(**common, dep_option_ids={"spouse": "dep-s-40k"}) == 52.8
    assert dependant_tag(**common, dep_option_ids={"spouse": "dep-s-20k"}) == 26.4
    # No elected level -> unpriced (never guess a level).
    assert dependant_tag(**common, dep_option_ids=None) is None
    # A stale id (re-parse replaced the categories) -> unpriced, not mislinked.
    assert dependant_tag(**common, dep_option_ids={"spouse": "gone"}) is None
    # Employee-Only stays $0 regardless of any stored choice.
    assert dependant_tag(
        source="slip", pricing=None, family_slip_idx=idx, product_id="prod-1",
        tier_category_id="cat-mgr", plan_code="4",
        spouse_count=0, child_count=0, dep_profiles=[],
        dep_option_ids={"spouse": "dep-s-40k"},
    ) == 0.0


def test_member_coverage_tag_unpriced_dependants_unprice_the_whole_tag():
    """A priced employee component must NOT silently absorb covered dependants
    whose tag can't price (no elected level / unknown age) — the whole coverage
    tag goes None so the unpriced-election guard surfaces it."""
    idx = _choices_idx()
    slip_idx = {"prod-1": {"cat-mgr::4": 500.0}}
    common = dict(
        source_map={}, rule="full", pricing=None, slip_idx=slip_idx,
        family_slip_idx=idx, product_id="prod-1", age=40, declined=False,
        tier_category_id="cat-mgr", plan_code="4",
        default_tier_category_id="cat-mgr", default_plan="4",
    )
    # Employee-Only: the employee tag stands alone.
    assert member_coverage_tag(**common, spouse_count=0, child_count=0) == 500.0
    # Covered spouse with an elected level: employee + dependant.
    assert member_coverage_tag(
        **common, spouse_count=1, child_count=0,
        dep_profiles=[("spouse", 40)], dep_option_ids={"spouse": "dep-s-40k"},
    ) == 552.8
    # Covered spouse with NO elected level: the whole tag is unpriced.
    assert member_coverage_tag(
        **common, spouse_count=1, child_count=0, dep_profiles=[("spouse", 40)],
    ) is None
    # A product with no dependant pricing at all keeps the old behavior:
    # covered dependants don't block the employee tag.
    assert member_coverage_tag(
        source_map={}, rule="full", pricing=None, slip_idx=slip_idx,
        family_slip_idx=None, product_id="prod-1", age=40, declined=False,
        tier_category_id="cat-mgr", plan_code="4",
        default_tier_category_id="cat-mgr", default_plan="4",
        spouse_count=1, child_count=0, dep_profiles=[("spouse", 40)],
    ) == 500.0


def test_breakdown_exposes_option_choices():
    bd = dependant_pricing_breakdown(
        pricing=None, family_slip_idx=_choices_idx(), source="slip",
        product_id="prod-1", tier_category_id="cat-mgr", plan_code="4",
    )
    assert bd["mode"] == "slip_options"
    levels = bd["choices"]["spouse"]
    assert [c["sum_insured"] for c in levels] == [20000.0, 40000.0, 60000.0]
    assert all(c["category_id"] for c in levels)


def test_member_coverage_tag_compulsory_dependants_use_employee_flex():
    """Participation controls selection, not funding. Compulsory dependants are
    automatic, but their charge still comes from employee flex and therefore
    remains unpriced when a required coverage level has not been selected."""
    idx = _choices_idx()
    slip_idx = {"prod-1": {"cat-mgr::4": 500.0}}
    common = dict(
        source_map={}, rule="full", pricing=None, slip_idx=slip_idx,
        family_slip_idx=idx, product_id="prod-1", age=40, declined=False,
        tier_category_id="cat-mgr", plan_code="4",
        default_tier_category_id="cat-mgr", default_plan="4",
        spouse_count=1, child_count=0, dep_profiles=[("spouse", 40)],
    )
    # Covered spouse with no elected level is unpriced regardless of whether
    # participation is voluntary or compulsory.
    assert member_coverage_tag(**common) is None


# ── Pro-ration scales the COVER, not just the allowance ───────────────────────


def test_the_proration_factor_scales_a_price_tag() -> None:
    """`_combine_tags` is the single point every tag producer passes through —
    the election snapshot (`member_coverage_tag`, used by enrolment, bulk and
    revert) and the statement recompute (`_member_flex_line`) both end here.

    Scaling only the wallet would leave an October joiner holding 3/12 of the
    allowance and 12/12 of the cost: overdrawn before electing anything, and
    refused by `enrollment_flex_guard` for a member who has done nothing wrong.
    """
    from app.services.flex_pricing_resolver import _combine_tags

    # Employee tag + dependant tag, halved together.
    assert _combine_tags(1000.0, 200.0, 1, dep_applies=True, factor=0.5) == 600.0
    # Default is unscaled — a scheme with no pro-ration is untouched.
    assert _combine_tags(1000.0, 200.0, 1, dep_applies=True) == 1200.0
    # Rounded ONCE, so a grossed pro-rated tag equals a pro-rated grossed one.
    assert _combine_tags(1635.0, None, 0, dep_applies=False, factor=0.25) == 408.75

    # The factor must never manufacture a price out of "unpriceable". None is
    # the signal the unpriced-election guard reads.
    assert _combine_tags(None, None, 0, dep_applies=False, factor=0.5) is None
    assert _combine_tags(1000.0, None, 1, dep_applies=True, factor=0.5) is None



def test_the_price_tag_scales_by_the_members_proration_factor() -> None:
    """The cover charged against a wallet is scaled by the same factor that
    sized the wallet.

    Pro-rating only the allowance would leave an October joiner holding 3/12 of
    the wallet and 12/12 of the cost — overdrawn before electing anything, and
    tripping the enrolment guard for a member who has done nothing wrong. The
    factor is applied in `_combine_tags`, the ONE point every tag producer
    passes through (the election snapshot, bulk, revert, plan overrides and the
    benefit-statement recompute), so no call site can quietly keep an annual
    figure.
    """
    def tag(factor: float) -> float | None:
        return member_coverage_tag(
            source_map=None, rule="full", pricing=_PRICING, slip_idx=None,
            family_slip_idx=None, product_id="prodA", age=35, declined=False,
            tier_category_id="cat1", plan_code="GOLD",
            default_tier_category_id="cat1", default_plan="GOLD",
            spouse_count=0, child_count=0, factor=factor,
        )

    assert tag(1.0) == 1500.0
    assert tag(0.5) == 750.0
    assert tag(0.25) == 375.0
    # A member covered for none of the period draws nothing.
    assert tag(0.0) == 0.0


def test_proration_and_gst_round_once_together() -> None:
    """Applied AFTER the GST multiplier and rounded ONCE, so a grossed pro-rated
    tag and a pro-rated grossed tag cannot differ by a cent."""
    pricing = {**_PRICING, "__gst__": {"default": 1.09, "products": {}}}
    tag = member_coverage_tag(
        source_map=None, rule="full", pricing=pricing, slip_idx=None,
        family_slip_idx=None, product_id="prodA", age=35, declined=False,
        tier_category_id="cat1", plan_code="GOLD",
        default_tier_category_id="cat1", default_plan="GOLD",
        spouse_count=0, child_count=0, factor=0.25,
    )
    assert tag == round(1500.0 * 1.09 * 0.25, 2) == 408.75


def test_an_unpriceable_tag_stays_none_whatever_the_factor() -> None:
    """The factor scales a number; it must never turn "no price" into 0.00 —
    the unpriced-election guard reads None and a 0 would sail past it."""
    assert member_coverage_tag(
        source_map=None, rule="full", pricing=_PRICING, slip_idx=None,
        family_slip_idx=None, product_id="nosuch", age=35, declined=False,
        tier_category_id="cat1", plan_code="GOLD",
        default_tier_category_id="cat1", default_plan="GOLD",
        spouse_count=0, child_count=0, factor=0.5,
    ) is None
    # Declined coverage costs no flex regardless of the factor.
    assert member_coverage_tag(
        source_map=None, rule="full", pricing=_PRICING, slip_idx=None,
        family_slip_idx=None, product_id="prodA", age=35, declined=True,
        tier_category_id="cat1", plan_code="GOLD",
        default_tier_category_id="cat1", default_plan="GOLD",
        spouse_count=0, child_count=0, factor=0.5,
    ) is None
