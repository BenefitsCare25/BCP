"""Form-profile inference + synthesized-template shape + plan-label fix."""
from __future__ import annotations

from app.services.dynamic_template import (
    _plan_label,
    _plan_specs,
    merge_file_overlay,
)
from app.services.form_profiles import (
    basis_model_for,
    infer_profile,
    rate_model_for,
    sections_for,
)
from app.services.product_templates import (
    ProductTemplate,
    TemplateBenefitItem,
    TemplatePlan,
    TemplateTier,
    get_template,
)


def test_infer_profile_by_code() -> None:
    assert infer_profile("GHS") == "tiered_medical"
    assert infer_profile("GTL") == "sum_assured"
    assert infer_profile("GPA") == "accident"
    assert infer_profile("GBT") == "travel"
    assert infer_profile("WICA") == "statutory"
    # Unknown code falls back to the default.
    assert infer_profile("ZZZ") == "tiered_medical"
    # Explicit override wins over inference.
    assert infer_profile("GHS", override="travel") == "travel"
    # Garbage override is coerced to the default, not propagated.
    assert infer_profile("GBT", override="nonsense") == "travel"


def test_corrected_classifications() -> None:
    # GP/SP and the clinic codes are per-member outpatient products, not
    # life (GP) or EO/ES/EC/EF tiered (SP) — corrected 2026-06-02.
    for code in ("GP", "SP", "GCGP", "GCSP", "GOSP", "GOGP"):
        assert infer_profile(code) == "outpatient", code
    # GCI mirrors GTL (sum-assured + condition list), not the accident shape.
    assert infer_profile("GCI") == "sum_assured"
    # Dental uses the Panel/Non-Panel dental profile.
    assert infer_profile("GD") == "dental"
    assert infer_profile("DENTAL") == "dental"


def test_basis_and_rate_models_per_profile() -> None:
    expected = {
        "tiered_medical": ("tiered", "tiered"),
        "outpatient": ("per_member", "per_member"),
        "dental": ("per_member", "per_member"),
        "sum_assured": ("sum_assured", "per_1000_si"),
        "accident": ("sum_assured", "per_1000_si"),
    }
    for profile, (basis, rate) in expected.items():
        assert basis_model_for(profile) == basis, profile
        assert rate_model_for(profile) == rate, profile
    # Unknown profile coerces to the tiered default rather than erroring.
    assert basis_model_for("nonsense") == "tiered"
    assert rate_model_for("nonsense") == "tiered"


def test_all_profiles_share_unified_sections() -> None:
    # Every product family now renders the same five-section layout (the old
    # standalone Plans/Cover-details/Arrangements sections were folded into
    # Basis of Cover + Schedule of Benefits).
    expected = [
        "header",
        "eligibility",
        "basis_of_cover",
        "rate_table",
        "schedule_of_benefits",
    ]
    for profile in (
        "tiered_medical",
        "outpatient",
        "dental",
        "sum_assured",
        "accident",
        "travel",
        "statutory",
    ):
        assert sections_for(profile) == expected
    # Folded-away sections are no longer section ids.
    for dropped in ("plans", "arrangements"):
        assert dropped not in sections_for("travel")


def test_template_validator_fills_sections() -> None:
    tpl = ProductTemplate(
        code="GBT", version=1, display_name="Group Business Travel",
        form_profile="travel",
    )
    assert tpl.sections == sections_for("travel")
    # `profile_fields` (the old "Cover details" block) is gone — it was a
    # write-only capture surface nothing ever read. See form_profiles.py.
    assert not hasattr(tpl, "profile_fields")


def test_template_validator_fills_basis_and_rate_models() -> None:
    # Left unset, the validator fills basis/rate from the form profile.
    life = ProductTemplate(
        code="GTL", version=1, display_name="Group Term Life",
        form_profile="sum_assured",
    )
    assert life.basis_model == "sum_assured"
    assert life.rate_model == "per_1000_si"
    # An explicit override on the template is respected (not overwritten).
    custom = ProductTemplate(
        code="X", version=1, display_name="Custom",
        form_profile="tiered_medical", rate_model="per_member",
    )
    assert custom.basis_model == "tiered"  # filled from profile
    assert custom.rate_model == "per_member"  # explicit wins


def test_merge_overlay_prefers_complete_slip_sob_keeps_file_presentation() -> None:
    # The slip's real plans always win; its benefit lines win too WHEN the parser
    # extracted at least as many as the template (well-extracted GHS/GMM). STM GHS
    # has 6 plans + dozens of lines; ghs.v1.json has 4 plans (review finding #1).
    file_tpl = get_template("GHS")
    assert file_tpl is not None
    rich = [
        TemplateBenefitItem(number=str(i), name=f"Line {i}")
        for i in range(1, len(file_tpl.benefit_items) + 4)
    ]
    synth = ProductTemplate(
        code="GHS", version=99, display_name="synth",
        form_profile="tiered_medical",
        plans=[TemplatePlan(code=str(i), label=f"Plan {i}") for i in range(1, 7)],
        tiers=[TemplateTier(code="EO", label="EO")],
        benefit_items=rich,
    )
    merged = merge_file_overlay(synth, file_tpl)
    assert len(merged.plans) == 6  # slip structure wins over the file's 4
    assert len(merged.benefit_items) == len(rich)  # complete slip SOB wins
    assert merged.basis_model == file_tpl.basis_model  # file presentation
    assert merged.version == file_tpl.version
    # Kinds overlaid from the file by number (GHS line 1 is "text").
    assert {b.number: b.kind for b in merged.benefit_items}["1"] == "text"


def test_curated_sob_kinds_partition_is_exhaustive() -> None:
    # Guard against drift: every BenefitKind must be classified as either
    # 'curated' (file template wins — the line parser can't reproduce it) or
    # 'plain' (slip lines drive the form). Adding a new kind to BenefitKind
    # without classifying it here fails this test, preventing merge_file_overlay
    # from silently mis-routing the new kind.
    from typing import get_args

    from app.services.dynamic_template import _CURATED_SOB_KINDS
    from app.services.product_templates import BenefitKind

    all_kinds = set(get_args(BenefitKind))
    plain_kinds = {"amount", "currency", "percent", "text", "days", "group"}
    assert _CURATED_SOB_KINDS.isdisjoint(plain_kinds)
    assert _CURATED_SOB_KINDS | plain_kinds == all_kinds, (
        f"Unclassified BenefitKind(s): {all_kinds - _CURATED_SOB_KINDS - plain_kinds}"
    )


def test_merge_overlay_prefers_slip_sob_even_with_fewer_lines_plain_medical() -> None:
    # Regression: STM's GMM slip has 5 real benefit lines; gmm.v1.json has 6.
    # A line-count gate (slip >= file) kept the generic template and overlaid slip
    # values under the wrong names. GMM is a plain amount/text SOB (no boolean/
    # copay/list/scale, no column axis), so the slip's real lines must win even
    # though there are fewer of them.
    file_tpl = get_template("GMM")
    assert file_tpl is not None
    assert not any(
        bi.kind in {"boolean", "copay", "list", "scale"}
        for bi in file_tpl.benefit_items
    )
    slip_lines = [
        TemplateBenefitItem(number="1", name="Daily Room & Board"),
        TemplateBenefitItem(number="2", name="Inpatient benefits"),
        TemplateBenefitItem(number="3", name="Surgical Implants per disability"),
        TemplateBenefitItem(number="4", name="Maximum Benefit incl Room & Board"),
        TemplateBenefitItem(number="5", name="Extension to cover GST"),
    ]
    assert len(slip_lines) < len(file_tpl.benefit_items)
    synth = ProductTemplate(
        code="GMM", version=99, display_name="synth",
        form_profile="tiered_medical",
        plans=[TemplatePlan(code=str(i), label=f"Plan {i}") for i in range(1, 4)],
        tiers=[TemplateTier(code="EO", label="EO")],
        benefit_items=slip_lines,
    )
    merged = merge_file_overlay(synth, file_tpl)
    # Slip lines win — the broker sees the real schedule, not generic template names.
    assert [b.name for b in merged.benefit_items] == [b.name for b in slip_lines]


def test_merge_overlay_uses_file_sob_when_slip_under_extracts() -> None:
    # GCGP's A-G + panel matrix under-extracts (2 lines), so the curated 10-line
    # template structure (boolean/copay kinds) must win — never a broken partial.
    file_tpl = get_template("GCGP")
    assert file_tpl is not None and len(file_tpl.benefit_items) > 2
    synth = ProductTemplate(
        code="GCGP", version=99, display_name="synth", form_profile="outpatient",
        plans=[TemplatePlan(code="1", label="Plan 1")],
        benefit_items=[TemplateBenefitItem(number="7", name="Stray line")],
    )
    merged = merge_file_overlay(synth, file_tpl)
    assert [p.code for p in merged.plans] == ["1"]  # slip plans still kept
    assert len(merged.benefit_items) == len(file_tpl.benefit_items)  # template SOB
    assert {"boolean", "copay"} <= {b.kind for b in merged.benefit_items}


def test_merge_overlay_dental_keeps_column_axis_structure() -> None:
    # Dental flattens to one column when parsed; the column-axis template wins so
    # Panel/Non-Panel is preserved even if the slip yielded more raw lines.
    file_tpl = get_template("GD")
    assert file_tpl is not None and file_tpl.column_axis == ["Panel", "Non-Panel"]
    synth = ProductTemplate(
        code="GD", version=99, display_name="synth", form_profile="dental",
        plans=[TemplatePlan(code="1", label="Plan 1")],
        benefit_items=[
            TemplateBenefitItem(number=str(i), name=f"Proc {i}") for i in range(1, 30)
        ],
    )
    merged = merge_file_overlay(synth, file_tpl)
    assert merged.column_axis == ["Panel", "Non-Panel"]
    assert len(merged.benefit_items) == len(file_tpl.benefit_items)  # template wins


def test_merge_overlay_uses_file_benefit_lines_when_slip_has_none() -> None:
    # GTL slips carry no Schedule of Benefits, so the file's sum-assured lines
    # are the fallback while the slip's plans are still preferred.
    synth = ProductTemplate(
        code="GTL", version=99, display_name="synth",
        form_profile="sum_assured",
        plans=[TemplatePlan(code="A", label="Exec")],
        benefit_items=[],
    )
    file_tpl = get_template("GTL")
    assert file_tpl is not None
    merged = merge_file_overlay(synth, file_tpl)
    assert [p.code for p in merged.plans] == ["A"]  # slip plans kept
    assert len(merged.benefit_items) == len(file_tpl.benefit_items)  # file fallback
    assert merged.rate_model == "per_1000_si"


def test_merge_overlay_no_file_returns_base() -> None:
    synth = ProductTemplate(code="ZZZ", version=1, display_name="z")
    assert merge_file_overlay(synth, None) is synth


def test_plan_label_no_double_prefix() -> None:
    # The bug: codes that already start with "Plan" got "Plan Plan A …".
    assert _plan_label("Plan A - International / Asia", None) == "Plan A - International / Asia"
    assert _plan_label("1 / International", None) == "Plan 1 / International"
    # Display name wins verbatim when present.
    assert _plan_label("1", "International Plan") == "International Plan"


class _FakeCat:
    def __init__(self, plan_code: str) -> None:
        self.plan_assignments = {"plan_code": plan_code}


def test_plan_specs_category_fallback_no_double_prefix() -> None:
    cats = [_FakeCat("Plan A - International / Asia"), _FakeCat("1 / International")]
    specs = _plan_specs([], cats)  # type: ignore[arg-type]
    labels = [label for _, label in specs]
    assert "Plan Plan A - International / Asia" not in labels
    assert "Plan A - International / Asia" in labels
    assert "Plan 1 / International" in labels
