"""Flex scheme — extraction gateway + confirm-time validation."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

TEST_DB = Path(__file__).parent / "_test_flex_scheme.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-fake-key")

from sqlalchemy import select  # noqa: E402

from app.api.v1.flex_schemes import (  # noqa: E402
    _merge_schemes,
    _section_shape_errors,
    validate_scheme,
)
from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import AISpendLog  # noqa: E402
from app.services import ai_breaker, ai_cache  # noqa: E402
from app.services.ai_gateway import extract_flex_scheme  # noqa: E402
from app.services.flex_membership import (  # noqa: E402
    RosterVocab,
    VocabValue,
    classify_relationship,
    employee_designation,
    explicit_match_indices,
    family_status_from_counts,
    match_tier,
    nationality_country,
    resolve_family_status,
    tier_wallet,
)
from app.services.flex_reconcile import seed_tier_match_sets  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_singletons():
    ai_cache.reset_cache_for_tests()
    ai_breaker.reset_breaker_for_tests()


def _valid_scheme() -> dict:
    return {
        "meta": {"scheme_name": "SG Flexi", "currency": "SGD"},
        "tiers": [
            {
                "name": "JG8-17",
                "employee_type": {
                    "raw": "Confirmed staff, JG 8-17",
                    "job_grade_min": 8,
                    "job_grade_max": 17,
                },
                "limits": [
                    {"family_status": "S", "amount": 1100},
                    {"family_status": "M", "amount": 1450},
                    {"family_status": "M1C", "amount": 1800},
                ],
                "cost_sharing": {"employer_pct": 80, "employee_pct": 20, "exceptions": []},
                "benefit_categories": [{"name": "Medical", "claimable": True, "sub_limit": None}],
            }
        ],
        "dependant_def": {
            "spouse": {"eligible": True, "documentation": ["marriage_cert"]},
            "child": {
                "eligible": True,
                "age_limit": 19,
                "tertiary_age_limit": 25,
                "conditions": ["unmarried", "non_working"],
                "documentation": ["tertiary_proof_yearly"],
            },
            "verification": {"children_required": True},
        },
    }


# ── validate_scheme ─────────────────────────────────────────────────────────
def test_valid_scheme_passes():
    assert validate_scheme(_valid_scheme()) == []


def test_empty_tiers_fails():
    s = _valid_scheme()
    s["tiers"] = []
    assert any("at least one eligibility tier" in e for e in validate_scheme(s))


def test_missing_currency_defaults_to_platform_currency():
    # No default currency and no per-tier currency is VALID — it resolves to the
    # platform default (SGD); currency is never "required".
    s = _valid_scheme()
    s["meta"] = {}
    assert validate_scheme(s) == []


def test_per_tier_currency_resolves_without_default():
    # Multi-country scheme: no scheme default, each tier carries its own currency.
    s = _valid_scheme()
    s["meta"] = {}
    s["tiers"][0]["currency"] = "THB"
    assert validate_scheme(s) == []


def test_bad_tier_currency_fails():
    s = _valid_scheme()
    s["tiers"][0]["currency"] = "BAHT"
    assert any("not a 3-letter ISO code" in e for e in validate_scheme(s))


def test_legacy_eligibility_keys_are_tolerated():
    # The eligibility/proration UI was removed; any residual values in the bag
    # (from an older extraction) are inert and must NOT block confirm.
    s = _valid_scheme()
    s["eligibility"] = {"entitlement_start": "whenever", "proration": {"basis": "bogus"}}
    assert validate_scheme(s) == []


def test_effective_dates_validated():
    # Valid ISO pair passes; blank inherits the policy year window (valid).
    s = _valid_scheme()
    s["meta"]["effective_start"] = "2027-01-01"
    s["meta"]["effective_end"] = "2027-12-31"
    assert validate_scheme(s) == []
    s["meta"]["effective_end"] = None
    assert validate_scheme(s) == []
    # Junk date rejected; inverted window rejected.
    s["meta"]["effective_end"] = "next year"
    assert any("ISO date" in e for e in validate_scheme(s))
    s["meta"]["effective_end"] = "2026-06-30"
    assert any("on or before" in e for e in validate_scheme(s))


def test_gst_meta_validated():
    s = _valid_scheme()
    s["meta"]["gst_included"] = True
    s["meta"]["gst_rate"] = 9.0
    assert validate_scheme(s) == []
    s["meta"]["gst_rate"] = 120
    assert any("GST rate" in e for e in validate_scheme(s))
    s["meta"]["gst_rate"] = None
    s["meta"]["gst_included"] = "yes"
    assert any("GST included" in e for e in validate_scheme(s))


def test_flex_effective_window_defaults_and_overrides():
    from datetime import date

    from app.models import FlexScheme, PolicyYear
    from app.services.flex_membership import flex_effective_window

    db = SessionLocal()
    try:
        py = db.execute(select(PolicyYear)).scalars().first()
        assert py is not None
        # No scheme (or no meta dates) → the policy year's span.
        assert flex_effective_window(db, py) == (py.start_date, py.end_date)

        row = db.execute(
            select(FlexScheme).where(FlexScheme.policy_year_id == py.id)
        ).scalar_one_or_none()
        if row is None:
            row = FlexScheme(policy_year_id=py.id, scheme={})
            db.add(row)
        # One explicit bound overrides; the other keeps inheriting.
        row.scheme = {"meta": {"effective_start": "2027-03-01"}, "tiers": []}
        db.flush()
        assert flex_effective_window(db, py) == (date(2027, 3, 1), py.end_date)
        # Junk tolerated (legacy data) → falls back rather than crashing.
        row.scheme = {"meta": {"effective_start": "soon"}, "tiers": []}
        db.flush()
        assert flex_effective_window(db, py) == (py.start_date, py.end_date)
    finally:
        db.rollback()
        db.close()


def test_valid_eligibility_passes():
    s = _valid_scheme()
    s["eligibility"] = {
        "entitlement_start": "date_of_hire",
        "proration": {"basis": "months_served", "leaver_recovery": True},
    }
    assert validate_scheme(s) == []


def test_dependant_age_min_above_max_fails():
    s = _valid_scheme()
    s["meta"]["dependant_age_limits"] = {"child": {"min": 30, "max": 25}}
    assert any("age min must be ≤ max" in e for e in validate_scheme(s))


def test_dependant_age_negative_fails():
    s = _valid_scheme()
    s["meta"]["dependant_age_limits"] = {"spouse": {"max": -1}}
    assert any("spouse age max" in e for e in validate_scheme(s))


def test_dependant_age_valid_passes():
    s = _valid_scheme()
    s["meta"]["dependant_age_limits"] = {"spouse": {"max": 70}, "child": {"max": 25}}
    assert validate_scheme(s) == []


def test_bad_family_status_fails():
    s = _valid_scheme()
    s["tiers"][0]["limits"][0]["family_status"] = "WIDOWED"
    assert any("family status" in e for e in validate_scheme(s))


def test_no_limits_without_system_cap_fails():
    s = _valid_scheme()
    s["tiers"][0]["limits"] = []
    assert any("limit row" in e and "flat annual cap" in e for e in validate_scheme(s))


def test_no_limits_with_system_cap_passes():
    s = _valid_scheme()
    s["tiers"][0]["limits"] = []
    s["meta"]["system_cap"] = 10000
    assert validate_scheme(s) == []


def test_no_limits_with_tier_flat_cap_passes():
    # JG18-style tier: a flat annual cap, no family-status rows.
    s = _valid_scheme()
    s["meta"] = {"currency": "SGD"}
    s["tiers"][0]["limits"] = []
    s["tiers"][0]["system_cap"] = 10000
    assert validate_scheme(s) == []


def test_negative_tier_cap_fails():
    s = _valid_scheme()
    s["tiers"][0]["limits"] = []
    s["tiers"][0]["system_cap"] = -5
    assert any("flat annual cap" in e for e in validate_scheme(s))


# ── _merge_schemes (multi-file) ─────────────────────────────────────────────
def _tier(name: str, country: str, currency: str) -> dict:
    return {
        "name": name,
        "country": country,
        "currency": currency,
        "employee_type": {"raw": name},
        "limits": [{"family_status": "S", "amount": 100}],
        "benefit_categories": [{"name": "Medical", "claimable": True}],
    }


def test_merge_accumulates_distinct_tiers():
    existing = {"meta": {"currency": "SGD"}, "tiers": [_tier("JG18", "Singapore", "SGD")]}
    new = [
        {"meta": {}, "tiers": [_tier("TH", "Thailand", "THB")]},
        {"meta": {}, "tiers": [_tier("VN", "Vietnam", "VND")]},
    ]
    merged = _merge_schemes(existing, new)
    countries = {t["country"] for t in merged["tiers"]}
    assert countries == {"Singapore", "Thailand", "Vietnam"}
    assert merged["meta"]["currency"] == "SGD"


def test_merge_dedupes_same_tier():
    existing = {"meta": {}, "tiers": [_tier("JG18", "Singapore", "SGD")]}
    updated = _tier("JG18", "Singapore", "SGD")
    updated["limits"] = [{"family_status": "S", "amount": 999}]
    merged = _merge_schemes(existing, [{"meta": {}, "tiers": [updated]}])
    assert len(merged["tiers"]) == 1
    assert merged["tiers"][0]["limits"][0]["amount"] == 999  # newer wins


def test_legacy_cost_sharing_is_tolerated():
    # Cost sharing was removed from the tier editor; a residual value (even one
    # that doesn't sum to 100) is inert and must NOT block confirm — there is no
    # UI left to fix it.
    s = _valid_scheme()
    s["tiers"][0]["cost_sharing"] = {"employer_pct": 70, "employee_pct": 20}
    assert validate_scheme(s) == []


def test_missing_claimable_fails():
    s = _valid_scheme()
    s["tiers"][0]["benefit_categories"] = [{"name": "Medical"}]  # no claimable
    assert any("claimable" in e for e in validate_scheme(s))


def test_section_shape_errors_flags_non_list_tiers():
    assert _section_shape_errors({"tiers": "oops"})
    assert _section_shape_errors({"meta": 42})
    # Valid shapes (and null where allowed) produce no errors.
    assert _section_shape_errors(
        {"meta": {}, "tiers": [], "eligibility": None, "dependant_def": None}
    ) == []


def test_duplicate_family_status_fails():
    s = _valid_scheme()
    s["tiers"][0]["limits"].append({"family_status": "S", "amount": 999})
    assert any("duplicate family status" in e for e in validate_scheme(s))


def test_empty_benefit_categories_fails():
    s = _valid_scheme()
    s["tiers"][0]["benefit_categories"] = []
    assert any("benefit category" in e for e in validate_scheme(s))


# ── flex_membership (family-status counting) ────────────────────────────────
def test_classify_relationship():
    assert classify_relationship("Spouse") == "spouse"
    assert classify_relationship("WIFE") == "spouse"
    assert classify_relationship("Child") == "child"
    assert classify_relationship("Daughter") == "child"
    assert classify_relationship("Parent") is None
    assert classify_relationship("") is None


def test_family_status_from_counts():
    assert family_status_from_counts(False, 0) == "S"
    assert family_status_from_counts(True, 0) == "M"
    assert family_status_from_counts(True, 1) == "M1C"
    assert family_status_from_counts(True, 2) == "M2C"
    assert family_status_from_counts(True, 3) == "M3C"
    assert family_status_from_counts(True, 5) == "M3C"  # capped at 3+


def test_resolve_family_status_prefers_dependants():
    # A linked spouse + 2 children → M2C, sourced from the dependant listing,
    # even if the roster category says something else.
    code, source = resolve_family_status({"family_status": "S"}, {}, 1, 2, True)
    assert (code, source) == ("M2C", "dependants")


def test_resolve_family_status_unclassified_deps_fall_back():
    # has_deps=True but no spouse/child recognized (e.g. only a "parent" record):
    # must NOT silently become "S" — fall through to the roster signal instead.
    code, source = resolve_family_status({"family_status": "M2C"}, {}, 0, 0, True)
    assert (code, source) == ("M2C", "roster")
    code, source = resolve_family_status({}, {"marital_status": "Married"}, 0, 0, True)
    assert (code, source) == ("M", "roster")


def test_resolve_family_status_roster_fallback():
    code, source = resolve_family_status({"family_status": "M1C"}, {}, 0, 0, False)
    assert (code, source) == ("M1C", "roster")
    code, source = resolve_family_status({}, {"marital_status": "Married"}, 0, 0, False)
    assert (code, source) == ("M", "roster")
    code, source = resolve_family_status({}, {}, 0, 0, False)
    assert (code, source) == (None, "none")


def test_nationality_country():
    assert nationality_country("Singaporean") == "singapore"
    assert nationality_country("Thai") == "thailand"
    assert nationality_country("Vietnamese") == "vietnam"
    assert nationality_country("") is None


def test_match_tier_by_country_then_grade():
    tiers = [
        {"name": "SG JG8-17", "country": "Singapore",
         "employee_type": {"job_grade_min": 8, "job_grade_max": 17}},
        {"name": "SG JG18+", "country": "Singapore",
         "employee_type": {"job_grade_min": 18, "job_grade_max": 30}},
        {"name": "Thailand", "country": "Thailand", "employee_type": {}},
    ]
    assert match_tier(12, "singapore", tiers) == 0
    assert match_tier(20, "singapore", tiers) == 1
    assert match_tier(12, "thailand", tiers) == 2  # country wins over grade band


def test_match_tier_known_out_of_band_is_ineligible():
    # A known grade outside every banded tier (no band-less catch-all) → None,
    # so no wallet is assigned. An unknown grade still defaults to the first tier.
    tiers = [
        {"name": "JG8-17", "country": "Singapore",
         "employee_type": {"job_grade_min": 8, "job_grade_max": 17}},
        {"name": "JG18-30", "country": "Singapore",
         "employee_type": {"job_grade_min": 18, "job_grade_max": 30}},
    ]
    assert match_tier(5, "singapore", tiers) is None       # below every band
    assert match_tier(40, "singapore", tiers) is None      # above every band
    assert match_tier(None, "singapore", tiers) == 0       # unknown → first tier


def test_match_tier_falls_back_to_default_tier_when_out_of_band():
    # Country tier matches by country but the grade is out of its band; a
    # no-country band-less default tier should catch the employee.
    tiers = [
        {"name": "SG Exec", "country": "Singapore",
         "employee_type": {"job_grade_min": 18, "job_grade_max": 30}},
        {"name": "Global Default", "employee_type": {}},  # no country, no band
    ]
    assert match_tier(20, "singapore", tiers) == 0   # in band → country tier
    assert match_tier(5, "singapore", tiers) == 1    # out of band → default tier


def _designation_tiers() -> list[dict]:
    # Designation-labeled tiers (no grade band, no country) — the eligibility
    # criterion is the job-title itself. Mirrors the CDL Flexi scheme.
    return [
        {"name": "GCEO", "employee_type": {"raw": "GCEO"}, "system_cap": 9245},
        {"name": "EVP and Above", "employee_type": {"raw": "EVP and Above"},
         "system_cap": 3800},
        {"name": "Executive to AM & Secretary",
         "employee_type": {"raw": "Executive to AM & Secretary"}, "system_cap": 2680},
        {"name": "Clerical and General Employees",
         "employee_type": {"raw": "Clerical and General Employees"}, "system_cap": 2040},
    ]


def test_match_tier_by_designation():
    # Band-less, job-title tiers match on the employee's designation — not the
    # first tier (the bug: everyone collapsing onto tier 0).
    tiers = _designation_tiers()
    assert match_tier(None, None, tiers, designation="GCEO") == 0
    assert match_tier(None, None, tiers, designation="EVP and Above") == 1
    # Normalization: "&"→"and", punctuation/case-insensitive.
    assert match_tier(None, None, tiers, designation="Executive to AM and Secretary") == 2
    assert match_tier(None, None, tiers, designation="clerical and general employees") == 3


def test_match_tier_designation_ignores_spurious_grade():
    # A garbage grade coerced from an alphanumeric job code (e.g. "E2"→2) must not
    # steal the match away from the designation when no tier carries a band.
    tiers = _designation_tiers()
    assert match_tier(2, None, tiers, designation="EVP and Above") == 1


def test_match_tier_unknown_designation_is_ineligible():
    # A designation that matches no job-title tier yields None — it must NOT
    # collapse onto the first tier (which mislabeled the whole roster).
    tiers = _designation_tiers()
    assert match_tier(None, None, tiers, designation="Director, Thailand Branch") is None
    # No grade and no designation at all → best-effort first tier.
    assert match_tier(None, None, tiers, designation=None) == 0


def test_match_tier_designation_catch_all_fallback():
    # A band-less generic catch-all still absorbs an unmatched designation.
    tiers = [
        {"name": "Manager", "employee_type": {"raw": "Manager"}, "system_cap": 3000},
        {"name": "All Other Employees", "employee_type": {"raw": "All other employees"},
         "system_cap": 1000},
    ]
    assert match_tier(None, None, tiers, designation="Manager") == 0
    assert match_tier(None, None, tiers, designation="Random Title") == 1


def test_employee_designation_prefers_specific_keys():
    assert employee_designation({}, {"category": "Manager"}) == "Manager"
    assert employee_designation({}, {"job_title": "VP", "category": "Manager"}) == "VP"
    assert employee_designation({"designation": "CEO"}, {"category": "Manager"}) == "CEO"
    assert employee_designation({}, {}) is None


def test_tier_wallet_resolution():
    tier = {
        "limits": [{"family_status": "S", "amount": 1100},
                   {"family_status": "M", "amount": 1450}],
        "system_cap": None,
    }
    assert tier_wallet(tier, "S", {}) == 1100.0
    assert tier_wallet(tier, "M", {}) == 1450.0
    # No family row → falls back to a flat cap.
    flat = {"limits": [], "system_cap": 10000}
    assert tier_wallet(flat, "S", {}) == 10000.0
    assert tier_wallet({"limits": []}, "S", {"system_cap": 9000}) == 9000.0


# ── Roster-anchored match sets (reconciliation model) ────────────────────────
def _match_set_tiers() -> list[dict]:
    return [
        {"name": "Managers", "employee_type": {
            "match_designations": ["Manager", "Snr Manager"], "match_grades": ["M1"]}},
        {"name": "Executives", "employee_type": {
            "match_designations": ["Executive"], "match_grades": ["E1", "E2"]}},
    ]


def test_match_tier_by_explicit_designation_set():
    tiers = _match_set_tiers()
    # Union: designation hit regardless of grade. Normalized (case-insensitive).
    assert match_tier(None, None, tiers, designation="manager") == 0
    assert match_tier(None, None, tiers, designation="Executive") == 1
    # A designation in no set is ineligible (not collapsed onto tier 0).
    assert match_tier(None, None, tiers, designation="Director") is None


def test_match_tier_by_explicit_grade_set():
    tiers = _match_set_tiers()
    # Grade matched on the RAW string — no numeric coercion, so codes work.
    assert match_tier(None, None, tiers, grade_str="M1", designation=None) == 0
    assert match_tier(None, None, tiers, grade_str="E2", designation=None) == 1


def test_match_tier_explicit_union_either_axis():
    tiers = _match_set_tiers()
    # Grade points at Executives, designation at Managers — either axis matches;
    # ambiguity is surfaced separately (see explicit_match_indices).
    assert match_tier(None, None, tiers, designation="Manager", grade_str="X9") == 0


def test_match_tier_explicit_wins_over_legacy_band():
    tiers = [
        {"name": "Band", "employee_type": {"job_grade_min": 1, "job_grade_max": 30}},
        {"name": "Exec set", "employee_type": {"match_designations": ["Executive"]}},
    ]
    # Grade 5 is in tier 0's band, but the designation explicitly matches tier 1 —
    # reconciled sets take priority over the legacy numeric band.
    assert match_tier(5, None, tiers, designation="Executive", grade_str="5") == 1


def test_match_tier_coded_grade_not_lost():
    # Regression: a coded grade ("JG08") coerces to a stray int under the legacy
    # path; the explicit set matches it exactly instead.
    tiers = [{"name": "T", "employee_type": {"match_grades": ["JG08"]}}]
    assert match_tier(8, None, tiers, designation=None, grade_str="JG08") == 0
    assert match_tier(8, None, tiers, designation=None, grade_str="JG09") is None


def test_explicit_match_indices_flags_ambiguity():
    tiers = [
        {"name": "A", "employee_type": {"match_designations": ["Manager"]}},
        {"name": "B", "employee_type": {"match_grades": ["M1"]}},
    ]
    # An employee who is a "Manager" AND grade "M1" satisfies both.
    idxs = explicit_match_indices("M1", "Manager", None, tiers)
    assert idxs == [0, 1]
    # Only one axis → single match, not ambiguous.
    assert explicit_match_indices("M1", "Director", None, tiers) == [1]


def test_seed_tier_match_sets_from_band_and_text():
    vocab = RosterVocab(
        employees_total=5,
        designations=[
            VocabValue(value="Manager", count=3, claimed=False),
            VocabValue(value="Executive", count=2, claimed=False),
        ],
        grades=[
            VocabValue(value="8", count=2, claimed=False),
            VocabValue(value="12", count=1, claimed=False),
            VocabValue(value="20", count=2, claimed=False),
        ],
    )
    scheme = {
        "tiers": [
            # Grade band 8-17 → selects roster grades 8 and 12 (not 20).
            {"name": "JG8-17", "employee_type": {
                "raw": "Job Grade 8-17", "job_grade_min": 8, "job_grade_max": 17}},
            # Designation text → exact token match on "Manager".
            {"name": "Mgmt", "employee_type": {"raw": "Manager and Director"}},
        ]
    }
    seed_tier_match_sets(scheme, vocab)
    band_et = scheme["tiers"][0]["employee_type"]
    assert sorted(band_et["match_grades"]) == ["12", "8"]
    mgmt_et = scheme["tiers"][1]["employee_type"]
    assert mgmt_et["match_designations"] == ["Manager"]
    # "Director" maps to no roster designation → surfaced as unresolved even
    # though "Manager" resolved (it's a genuinely unmapped designation).
    assert mgmt_et["unresolved"] == ["Director"]
    # A tier that resolves to NOTHING keeps its unmapped term.
    scheme2 = {"tiers": [{"name": "X", "employee_type": {"raw": "Director"}}]}
    seed_tier_match_sets(scheme2, vocab)
    assert scheme2["tiers"][0]["employee_type"]["unresolved"] == ["Director"]


def test_seed_multiword_designation_matches_whole_value():
    # A multi-word roster title must seed as the whole value, not be split into
    # non-matching tokens; a shorter title it contains is dropped as subsumed.
    vocab = RosterVocab(
        employees_total=3,
        designations=[
            VocabValue(value="Sales and Marketing Manager", count=2, claimed=False),
            VocabValue(value="Manager", count=1, claimed=False),
        ],
        grades=[],
    )
    scheme = {"tiers": [{"name": "T", "employee_type": {
        "raw": "Sales and Marketing Manager"}}]}
    seed_tier_match_sets(scheme, vocab)
    et = scheme["tiers"][0]["employee_type"]
    assert et["match_designations"] == ["Sales and Marketing Manager"]
    assert "unresolved" not in et


def test_employee_signals_canonicalizes_grade():
    from app.services.flex_membership import employee_signals
    grade, gstr, _ = employee_signals({"grade": 18.0}, {})
    assert grade == 18 and gstr == "18"  # integral float collapses, no "18.0"
    grade, gstr, _ = employee_signals({"grade": 0}, {"grade": 5})
    assert grade == 0 and gstr == "0"  # integer 0 survives (not falsy-skipped)
    _, gstr, _ = employee_signals({}, {"grade": "M1"})
    assert gstr == "M1"  # falls back to the raw attribute


def test_explicit_match_indices_spans_country_and_default_pools():
    tiers = [
        {"name": "Default Mgr", "employee_type": {"match_designations": ["Manager"]}},
        {"name": "SG", "country": "Singapore",
         "employee_type": {"match_grades": ["M1"]}},
    ]
    # A Singaporean 'Manager' at grade M1 satisfies the SG tier (country pool) AND
    # the no-country default Manager tier — both must be reported as an overlap.
    assert set(explicit_match_indices("M1", "Manager", "singapore", tiers)) == {0, 1}


def test_seed_skips_already_reconciled_tiers():
    vocab = RosterVocab(
        employees_total=1,
        designations=[VocabValue(value="Manager", count=1, claimed=True)],
        grades=[],
    )
    scheme = {"tiers": [{"name": "T", "employee_type": {
        "raw": "Manager", "match_designations": ["Custom Value"]}}]}
    seed_tier_match_sets(scheme, vocab)
    # Broker's existing selection is preserved, not overwritten by seeding.
    assert scheme["tiers"][0]["employee_type"]["match_designations"] == ["Custom Value"]


def test_eligibility_requires_a_signal():
    base = {
        "meta": {"currency": "SGD"},
        "tiers": [{
            "name": "T", "employee_type": {},
            "system_cap": 1000,
            "benefit_categories": [{"name": "Flex", "claimable": True}],
        }],
    }
    errs = validate_scheme(base)
    assert any("at least one job title or job grade" in e for e in errs)
    # Adding a match set satisfies eligibility.
    base["tiers"][0]["employee_type"] = {"match_grades": ["M1"]}
    assert not any("at least one job title or job grade" in e for e in validate_scheme(base))
    assert tier_wallet(None, "S", {}) is None


# ── extract_flex_scheme gateway ─────────────────────────────────────────────
def _fake_extract(text, images, config):
    scheme = {
        "meta": {"currency": "SGD"},
        "tiers": [
            {"name": "T1", "employee_type": {"raw": "x"}, "limits": [], "benefit_categories": []}
        ],
        "dependant_def": None,
    }
    meta = {
        "provider": "anthropic",
        "model": "claude-test",
        "input_tokens": 200,
        "output_tokens": 80,
        "confidence": 0.7,
        "reasoning": "",
    }
    return {"scheme": scheme}, meta


def test_extract_flex_scheme_records_spend_and_caches():
    with patch(
        "app.services.ai_gateway.extract_flex_scheme_via_ai", side_effect=_fake_extract
    ) as mock:
        db = SessionLocal()
        try:
            r1 = extract_flex_scheme(
                db, client_id=DEMO_CLIENT_ID, policy_year_id=None,
                text="hello", images=[],
            )
            assert r1.cache_hit is False
            assert r1.scheme["meta"]["currency"] == "SGD"
            assert r1.metadata["confidence"] == 0.7

            # Second identical call hits the cache — no second provider call.
            r2 = extract_flex_scheme(
                db, client_id=DEMO_CLIENT_ID, policy_year_id=None,
                text="hello", images=[],
            )
            assert r2.cache_hit is True
            assert mock.call_count == 1
            db.commit()

            rows = db.execute(
                select(AISpendLog).where(AISpendLog.operation == "ai_extract_flex")
            ).scalars().all()
            assert len(rows) == 2
            assert sum(1 for r in rows if r.cache_hit) == 1
        finally:
            db.close()
