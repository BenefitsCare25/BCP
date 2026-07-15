"""Fact-Find form field-population unit tests (pure helpers, no DB)."""
from __future__ import annotations

from datetime import date, datetime

from app.models import Plan
from app.services.fact_find_form import (
    AGE_BANDS,
    _age_next_birthday,
    _band_for,
    _clean_designation,
    _fmt_basis,
    _parse_dob,
    _plan_label,
)


def test_clean_designation_strips_internal_qualifiers_keeps_tiers() -> None:
    # The "(Job category: …)" grade-code map and "/ … dependants" tail are
    # internal and dropped; "(Option N)" / "(except for Director)" are kept.
    assert _clean_designation("SM to SVP (Job category: A1 to A6, AA to AF)") == "SM to SVP"
    assert (
        _clean_designation(
            "Manager, Executive to AM and Secretary "
            "(Job category: E1 to E6) / All Eligible Dependants on Voluntary basis"
        )
        == "Manager, Executive to AM and Secretary"
    )
    assert (
        _clean_designation("Officer (except for Director) (Job category: J1 to J3)")
        == "Officer (except for Director)"
    )
    assert _clean_designation("SM to SVP (Option 1)") == "SM to SVP (Option 1)"
    assert _clean_designation("Executive to AM") == "Executive to AM"
    assert _clean_designation(None) == ""


def _plan(name: str) -> Plan:
    return Plan(id="p", product_id="pr", policy_year_id="py", code="1", display_name=name)


def test_plan_label_suppresses_generic_placeholder() -> None:
    # GPA-style single-schedule products carry the generic "Schedule of Benefits"
    # plan; the sum-insured column is the real differentiator, so the label is
    # blanked rather than repeated on every row.
    assert _plan_label(_plan("Schedule of Benefits"), "1") == ""
    assert _plan_label(_plan("Plan 3"), "3") == "Plan 3"  # real name kept
    assert _plan_label(None, "7") == "7"  # falls back to the code


def test_parse_dob_handles_excel_datetime_string() -> None:
    # Excel dates reach the roster as "1958-02-19 00:00:00"; the age tables were
    # silently blank because the date-only formats couldn't parse the time tail.
    assert _parse_dob("1958-02-19 00:00:00") == date(1958, 2, 19)


def test_parse_dob_handles_objects_and_formats() -> None:
    assert _parse_dob(datetime(1990, 5, 1, 0, 0)) == date(1990, 5, 1)
    assert _parse_dob(date(1990, 5, 1)) == date(1990, 5, 1)
    assert _parse_dob("1990-05-01") == date(1990, 5, 1)
    assert _parse_dob("01/05/1990") == date(1990, 5, 1)  # dd/mm/yyyy


def test_parse_dob_rejects_garbage_and_empty() -> None:
    assert _parse_dob(None) is None
    assert _parse_dob("") is None
    assert _parse_dob("not a date") is None


def test_age_next_birthday_and_band() -> None:
    # Born 1958-02-19, quote effective 2026-01-01 → turns 68 that year → ANB 68.
    anb = _age_next_birthday(date(1958, 2, 19), date(2026, 1, 1))
    assert anb == 68
    assert _band_for(anb) == AGE_BANDS[-1][0]  # "66 & above"
    assert _band_for(_age_next_birthday(date(2000, 6, 1), date(2026, 1, 1))) == AGE_BANDS[0][0]


def test_fmt_basis_strips_float_artifact_keeps_text() -> None:
    assert _fmt_basis(250000.0) == "250,000"
    assert _fmt_basis("250000.0") == "250,000"  # stored as a numeric string
    assert _fmt_basis(60000) == "60,000"
    assert _fmt_basis(1234.5) == "1,234.50"
    assert _fmt_basis("12 times basic monthly salary") == "12 times basic monthly salary"
    assert _fmt_basis(None) == ""
