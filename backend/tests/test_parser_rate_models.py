"""Synthetic, file-free tests for the parser's rate-section extraction.

These exercise the per-member and per-$1,000-SI rate shapes added so the parser
works across the different placement-slip templates (GP/SP/GCGP/GCSP/GD key the
rate row on a Plan column with a "Per Insured / Member Rate" column, which the
older Category-only flat parser missed). Driven by in-memory row lists so they
run in CI without the real (PII) workbooks.
"""
from __future__ import annotations

from app.services.placement_slip_parser import (
    ExtractedCategory,
    _currency_amount,
    _enrich_with_rates,
    _extract_rate_data,
    _extract_voluntary_rates,
    _parse_age_band,
)


def test_currency_amount_parses_complete_amounts_only() -> None:
    # Currency-prefixed and paren-annotated amounts parse; a number embedded in
    # prose ("33 travellers @ $96") must NOT yield a bogus figure.
    assert _currency_amount(["$3,169.80 (Subject to Minimum …)"], 0) == 3169.80
    assert _currency_amount(["S$71,960,473 (estimated)"], 0) == 71960473.0
    assert _currency_amount(["3,169.80 (est)"], 0) == 3169.80
    assert _currency_amount([3169.8], 0) == 3169.8  # plain numeric passes through
    assert _currency_amount(["33 travellers @ $96 each"], 0) is None
    assert _currency_amount(["1 month free then $500"], 0) is None
    assert _currency_amount(["N/A"], 0) is None


def _cat(plan_code: str, category: str = "Some eligible category") -> ExtractedCategory:
    return ExtractedCategory(
        insured="ACME",
        category=category,
        participation="Compulsory",
        plan_code=plan_code,
        source_row=1,
    )


def test_per_member_rate_with_plan_key_and_member_rate_label() -> None:
    # CBRE GP layout: Plan | Number | Per Insured (Member Rate) | Premium,
    # with "Member Rate" on the row below "Per Insured".
    rows = [
        ["Rate :", "Insured", "", "Plan", "Number", "Per Insured", "", "Premium"],
        ["", "", "", "", "", "Member Rate", "", ""],
        ["", "ACME", "", "1A/1B", 150, 308, "", 46200],
        ["", "", "", "2A/2B/3", 2076, 280, "", 581280],
        ["Annual Premium (sbj to GST) :", "", 627480],
    ]
    rate_data = _extract_rate_data(rows)
    keys = {rd.key for rd in rate_data}
    assert keys == {"1A/1B", "2A/2B/3"}
    by_key = {rd.key: rd for rd in rate_data}
    assert by_key["1A/1B"].rate == 308
    assert by_key["1A/1B"].annual_premium == 46200

    # A category whose plan_code is a single bundled code matches via expansion.
    enriched = _enrich_with_rates((_cat("1A"), _cat("2B")), rate_data)
    assert enriched[0].annual_premium == 46200
    assert enriched[1].annual_premium == 581280


def test_per_member_rate_with_member_type_suffix() -> None:
    # CDL GCGP layout: Plan rows like "1 - Employees" / "1 - Dependents".
    rows = [
        ["Rate :"],
        ["", "Insured", "", "Plan", "Rate", "Premium"],
        ["", "CDL", "", "1 - Employees", 378, 186732],
        ["", "", "", "1 - Dependents", 396.9, None],
        ["", "", "", "2 - Employees / Dependents", 454, None],
        ["Annual Premium (sbj to GST) :", "", 186732],
    ]
    rate_data = _extract_rate_data(rows)
    assert len(rate_data) == 3
    # The member-type of each per-member row is classified from its suffix.
    assert [r.member_type for r in rate_data] == ["employee", "dependent", "both"]
    enriched = _enrich_with_rates((_cat("1"), _cat("2")), rate_data)
    # plan_code "1" matches the first "1 - …" row (Employees), and its separate
    # "1 - Dependents" row is captured as the per-dependant rate.
    assert enriched[0].premium_rate == 378
    assert enriched[0].annual_premium == 186732
    assert enriched[0].dependant_rate == 396.9
    # Plan 2's combined "Employees / Dependents" row → both rates are $454.
    assert enriched[1].premium_rate == 454
    assert enriched[1].dependant_rate == 454


def test_per_1000_si_rate_still_parses() -> None:
    # Regression guard: the sum-assured (GTL) layout must keep working.
    rows = [
        ["Rate :", "Insured", "", "Category", "Sum Insured ( SI )",
         "Rate per S$1,000 sum insured", "Annual Premium"],
        ["", "ACME", "", "Plan 1: GCEO", 4_000_000, 1.62, 6480],
        ["Annual Premium (sbj to GST) :", "", 6480],
    ]
    rate_data = _extract_rate_data(rows)
    assert len(rate_data) == 1
    assert rate_data[0].rate_basis == "per_1000_si"
    assert rate_data[0].rate == 1.62
    assert rate_data[0].sum_insured == 4_000_000


def _cat_si(
    category: str, sum_insured: float, *, basis: float | str | None = None,
    plan_code: str = "",
) -> ExtractedCategory:
    return ExtractedCategory(
        insured="ACME",
        category=category,
        participation="Compulsory",
        plan_code=plan_code,
        source_row=1,
        sum_insured=sum_insured,
        basis=basis,
    )


def test_blended_per_1000_rate_propagates_to_every_category() -> None:
    # GPA layout: ONE blended "All Employees" rate on the TOTAL sum insured, no
    # per-category rate. The blended rate must apply to every grade so each
    # member's premium computes from their own basis downstream.
    rows = [
        ["Rate :", "Insured", "", "Category", "Sum Insured ( SI )",
         "Rate per S$1000 sum insured", "Annual Premium"],
        ["", "ACME", "", "All Employees", 80_000_000, 0.072, 5760],
        ["Annual Premium (sbj to GST):", "", 5760],
    ]
    rate_data = _extract_rate_data(rows)
    assert len(rate_data) == 1 and rate_data[0].rate == 0.072

    cats = (
        _cat_si("CEO", 2_000_000.0, basis=2_000_000.0),
        _cat_si("Executive", 100_000.0, basis=100_000.0),
    )
    enriched = _enrich_with_rates(cats, rate_data)
    # Every category inherits the blended rate + basis (per_1000_si)…
    for e in enriched:
        assert e.premium_rate == 0.072
        assert e.rate_basis == "per_1000_si"
        # …but NOT the group total premium (it's the whole product's, not a line's).
        assert e.annual_premium is None
    # Each keeps its own sum insured, so basis/1000*rate is correct per member.
    assert enriched[0].sum_insured == 2_000_000.0
    assert enriched[1].sum_insured == 100_000.0


def test_single_category_per_1000_is_not_treated_as_blended() -> None:
    # Guard: a lone rate row whose SI equals the category's own SI is that
    # category's real rate — keep normal matching (and its group annual premium),
    # don't misfire the blended-rate path.
    rows = [
        ["Rate :", "Insured", "", "Category", "Sum Insured ( SI )",
         "Rate per S$1,000 sum insured", "Annual Premium"],
        ["", "ACME", "", "All Employees", 4_000_000, 1.62, 6480],
        ["Annual Premium :", "", 6480],
    ]
    rate_data = _extract_rate_data(rows)
    enriched = _enrich_with_rates((_cat_si("All Employees", 4_000_000.0),), rate_data)
    assert enriched[0].premium_rate == 1.62
    assert enriched[0].annual_premium == 6480  # normal match — group annual kept


def test_parse_age_band_shapes() -> None:
    assert _parse_age_band("35 to 44") == (35, 44)
    assert _parse_age_band("45 - 49") == (45, 49)
    assert _parse_age_band("34 years old & below") == (None, 34)  # "& below" inclusive
    assert _parse_age_band("below 35") == (None, 34)  # "below N" exclusive ⇒ ≤N-1
    assert _parse_age_band("under 35") == (None, 34)
    assert _parse_age_band("65 & above") == (65, None)  # "& above" inclusive
    assert _parse_age_band("over 64") == (65, None)  # "over N" exclusive ⇒ ≥N+1
    assert _parse_age_band("70 to 74 (renewal only)") == (70, 74)
    assert _parse_age_band("All ages") is None


def test_voluntary_rate_table_parses_age_bands() -> None:
    # GTL/GCI life layout: a "Voluntary Rates / Based on Age Last Birthday" table
    # with a per-S$1000 rate per age band, used for voluntary employee + dependant.
    rows = [
        ["", "Voluntary Rates"],
        ["", "Based on Age Last Birthday", "Rate per 1,000 Sum assured (S$)"],
        ["", "34 years old & below", 0.88],
        ["", "35 to 44", 1.32],
        ["", "45 to 49", 1.65],
        ["", "70 to 74 (renewal only)", 49.67],
        ["Non Evidence Limit :", "", "Sum insured exceeding S$500,000 ..."],
    ]
    bands = _extract_voluntary_rates(rows)
    assert [b["label"] for b in bands] == [
        "34 years old & below", "35 to 44", "45 to 49", "70 to 74 (renewal only)",
    ]
    assert bands[0] == {"label": "34 years old & below", "min": None, "max": 34, "rate": 0.88}
    assert bands[1]["min"] == 35 and bands[1]["max"] == 44 and bands[1]["rate"] == 1.32
    # Stops at the non-band row that follows.
    assert all("non evidence" not in b["label"].lower() for b in bands)


def test_voluntary_rate_table_absent_returns_empty() -> None:
    # GPA / medical sheets have no voluntary age-band table → ().
    rows = [
        ["Rate :", "Insured", "Category", "Sum Insured ( SI )",
         "Rate per S$1000 sum insured", "Annual Premium"],
        ["", "ACME", "All Employees", 80_000_000, 0.072, 5760],
    ]
    assert _extract_voluntary_rates(rows) == ()


def test_tiered_rate_still_parses() -> None:
    # Regression guard: the GHS EO/ES/EC/EF tiered layout must keep working.
    rows = [
        ["Rate :", "", "", "", "EO", "", "ES", "", "EC", "", "EF"],
        ["", "Insured", "", "Plan", "Rate", "Premium", "Rate", "Premium",
         "Rate", "Premium", "Rate", "Premium"],
        ["", "ACME", "", "1", 100, 1000, 150, 500, 150, 500, 200, 200],
        ["Annual Premium (sbj to GST) :", "", 2200],
    ]
    rate_data = _extract_rate_data(rows)
    assert len(rate_data) == 1
    rd = rate_data[0]
    assert rd.rate_basis == "tiered"
    assert set(rd.rate_tiers or {}) == {"EO", "ES", "EC", "EF"}
    assert rd.annual_premium == 2200


def test_earnings_based_rate_captures_earnings_wica() -> None:
    # WICA (statutory): Category | Estimated annual earnings | Rate | Annual
    # Premium. The premium = earnings x rate; the earnings amount must be carried
    # so the card can show it, not just the derived premium.
    rows = [
        ["Rate :", "Insured", "", "Category", "", "* Estimated annual earnings",
         "Rate", "Annual Premium"],
        ["", "ACME", "", "Non-Manual Staffs", "", 71960473.0, 0.00033, 23746.95609],
        ["", "", "", "All Others", "", 402135.0, 0.00825, 3317.61375],
        ["Annual Premium (sbj to GST):", "", 27064],
    ]
    rate_data = _extract_rate_data(rows)
    assert len(rate_data) == 2
    assert all(rd.rate_basis == "earnings_based" for rd in rate_data)
    assert rate_data[0].estimated_annual_earnings == 71960473.0
    assert rate_data[0].rate == 0.00033
    assert rate_data[0].annual_premium == 23746.95609
    assert rate_data[1].estimated_annual_earnings == 402135.0


def test_flat_annual_premium_gbt() -> None:
    # GBT (travel): the Rate section has only an Annual Premium column (no rate,
    # SI, or earnings), and the single policy premium is printed once against the
    # first category, often annotated ("$3,169.80 (Subject to Minimum …)"). It
    # must parse as one annual_flat row. Note "employees" in the header (contains
    # "es") must NOT be misread as an ES tier column.
    rows = [
        ["Rate :", "Insured", "", "Category / Name",
         "", "*Total No. of employees per policy year", "Annual Premium"],
        ["", "ACME", "", "Senior Management on authorised Journey",
         "", "Travellers: 33", "$3,169.80 (Subject to Minimum Policy Premium of S$500)"],
        ["", "", "", "All Other Employees on authorised Journey"],
        ["Annual Premium (GST exempt) :", "", 3169.8],
    ]
    rate_data = _extract_rate_data(rows)
    assert len(rate_data) == 1
    rd = rate_data[0]
    assert rd.rate_basis == "annual_flat"
    assert rd.annual_premium == 3169.8
    assert rd.rate is None


def test_tiered_rate_with_plan_on_tier_header_row_osi() -> None:
    # OSI (secondment) puts "Insured" + "Plan / Region" on the tier-header row,
    # with only Rate/Premium labels on the sub-header. The key column must still
    # be found there — otherwise every data row is skipped (no rate extracted).
    rows = [
        ["Rate :", "Insured", "", "Plan / Region", "EO", "", "ES", "", "EC"],
        ["", "", "", "", "Rate", "Premium", "Rate", "Premium", "Rate"],
        ["", "ACME", "", "1", 2430, 2430],
        ["Annual Premium:", "", 2430],
    ]
    rate_data = _extract_rate_data(rows)
    assert len(rate_data) == 1
    rd = rate_data[0]
    assert rd.key == "1"
    assert rd.rate_basis == "tiered"
    assert rd.rate_tiers == {"EO": {"rate": 2430.0, "premium": 2430.0}}
    assert rd.annual_premium == 2430
