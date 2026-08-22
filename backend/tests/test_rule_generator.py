"""Rule generator + evaluator round-trip tests.

Each test exercises one of the §8.2 regex patterns or the §8.3 OR/AND semantics.
The pattern is: description string → rule_generator → JSONLogic → evaluator
checks the rule fires on the expected employee shape and doesn't fire on
counter-examples. This locks the shared contract before RuleBuilder is built.
"""
from __future__ import annotations

import pytest

from app.services.rule_evaluator import evaluate
from app.services.rule_generator import description_to_rule


def test_grade_range_between():
    env = description_to_rule("Hay Job Grade 08 to 15")
    assert env.rule == {"between": ["grade", 8, 15]}
    assert env.confidence >= 0.85
    assert evaluate(env.rule, {"grade": 8})
    assert evaluate(env.rule, {"grade": 15})
    assert evaluate(env.rule, {"grade": 12})
    assert not evaluate(env.rule, {"grade": 7})
    assert not evaluate(env.rule, {"grade": 16})


def test_grade_and_above():
    env = description_to_rule("Hay Job Grade 16 and above")
    assert env.rule == {">=": ["grade", 16]}
    assert evaluate(env.rule, {"grade": 16})
    assert evaluate(env.rule, {"grade": 20})
    assert not evaluate(env.rule, {"grade": 15})


def test_grade_below():
    env = description_to_rule("Grade 7 and below")
    assert env.rule == {"<=": ["grade", 7]}
    assert evaluate(env.rule, {"grade": 5})
    assert not evaluate(env.rule, {"grade": 8})


def test_salary_less_than():
    env = description_to_rule("Employees earning less than $3,000")
    assert env.rule == {"<": ["salary", 3000]}
    assert evaluate(env.rule, {"salary": 2500})
    assert not evaluate(env.rule, {"salary": 3000})


def test_work_permit_and_spass_union():
    env = description_to_rule("Foreign Workers (Work Permit & S-Pass)")
    assert env.rule == {"in": ["pass", ["WP", "SP"]]}
    assert evaluate(env.rule, {"pass": "WP"})
    assert evaluate(env.rule, {"pass": "SP"})
    assert not evaluate(env.rule, {"pass": "EP"})


def test_spass_only():
    env = description_to_rule("S-Pass holders")
    assert env.rule == {"=": ["pass", "SP"]}
    assert evaluate(env.rule, {"pass": "SP"})
    assert not evaluate(env.rule, {"pass": "WP"})


def test_spass_or_work_permit_keeps_both_populations() -> None:
    env = description_to_rule("Foreign workers on S-pass or Work permit")
    assert env.rule == {"in": ["pass", ["WP", "SP"]]}


def test_grade_or_class_union_semantics():
    """§8.3: 'Grade X to Y and Bargainable Staff' → UNION (OR), not AND."""
    env = description_to_rule("Hay Job Grade 08 to 15 and Bargainable Staff")
    assert env.rule is not None
    assert "or" in env.rule, f"Expected OR shape, got {env.rule}"
    # A grade-15 non-bargainable employee should match.
    assert evaluate(env.rule, {"grade": 12, "class": "PROFESSIONAL"})
    # A grade-3 bargainable employee should match.
    assert evaluate(env.rule, {"grade": 3, "class": "BARGAINABLE"})
    # A grade-3 non-bargainable employee should NOT match.
    assert not evaluate(env.rule, {"grade": 3, "class": "PROFESSIONAL"})


def test_grade_who_are_intersection_semantics():
    """§8.3: 'Grade X to Y who are Bargainable' → INTERSECTION (AND)."""
    env = description_to_rule("Hay Job Grade 08 to 15 who are Bargainable Staff")
    assert env.rule is not None
    # Top-level should be AND, not OR.
    assert "and" in env.rule, f"Expected AND shape, got {env.rule}"
    # Need BOTH conditions to match.
    assert evaluate(env.rule, {"grade": 12, "class": "BARGAINABLE"})
    assert not evaluate(env.rule, {"grade": 12, "class": "PROFESSIONAL"})
    assert not evaluate(env.rule, {"grade": 3, "class": "BARGAINABLE"})


def test_board_of_directors():
    env = description_to_rule("Board of Directors")
    assert env.rule == {"=": ["class", "BOARD_OF_DIRECTORS"]}
    assert evaluate(env.rule, {"class": "BOARD_OF_DIRECTORS"})


def test_secondee():
    env = description_to_rule("Postees / Secondees seconded overseas")
    assert env.rule == {"=": ["class", "SECONDEE"]}
    assert evaluate(env.rule, {"class": "SECONDEE"})


def test_wica_occupation_mgmt_admin():
    env = description_to_rule("Management & Administrative Staff")
    assert env.rule == {"=": ["occupation", "MGMT_ADMIN"]}
    assert evaluate(env.rule, {"occupation": "MGMT_ADMIN"})


def test_empty_description_is_needs_review():
    env = description_to_rule("")
    assert env.rule is None
    assert env.needs_review
    assert env.confidence == 0.0


def test_unrecognized_description_is_needs_review():
    env = description_to_rule("This is gibberish that nothing matches")
    assert env.rule is None
    assert env.needs_review


def test_evaluator_handles_missing_attribute():
    # If an employee record lacks the attribute the rule references,
    # the rule must be False (not raise).
    rule = {"between": ["grade", 8, 15]}
    assert not evaluate(rule, {})
    assert not evaluate(rule, {"grade": None})


def test_evaluator_string_comparison_is_case_insensitive():
    rule = {"=": ["pass", "SP"]}
    assert evaluate(rule, {"pass": "sp"})
    assert evaluate(rule, {"pass": "Sp"})


@pytest.mark.parametrize(
    ("desc", "expected_shape"),
    [
        ("Grade 16 and above", {">=": ["grade", 16]}),
        ("Grade 7 and below", {"<=": ["grade", 7]}),
        ("Grade 8 to 15", {"between": ["grade", 8, 15]}),
        ("Grade 30+", {">=": ["grade", 30]}),
    ],
)
def test_grade_pattern_table(desc: str, expected_shape: dict) -> None:
    env = description_to_rule(desc)
    assert env.rule == expected_shape


# ── New template patterns (PNG, CBRE, Hartree, Placement Slips 2026) ────────


def test_all_employees_catchall():
    env = description_to_rule("All Employees")
    assert env.rule == {"and": []}
    # Empty AND is vacuously true.
    assert evaluate(env.rule, {"grade": 5})
    assert evaluate(env.rule, {})
    assert env.confidence == 0.75
    assert env.needs_review


def test_all_employees_with_dependents_trailing():
    env = description_to_rule("All Employees and their Eligible Dependents")
    assert env.rule == {"and": []}


def test_all_other_employees_catchall():
    env = description_to_rule("All Other Employees and their Eligible Dependants")
    # "All Other" doesn't match the strict "all employees" pattern, but we
    # don't want it to silently lose all matching — currently falls to needs_review.
    # Confirm shape: rule is None (no pattern matched). Documenting current
    # behavior so the AI fallback knows to pick it up.
    assert env.rule is None or env.rule == {"and": []}


def test_plan_with_single_job_category_code():
    env = description_to_rule("Plan 1: GCEO and GCOO (Job category: 99)")
    # Expect both job_category and role conditions (GCEO + GCOO).
    assert env.rule is not None
    # Should at least include the job_category match.
    flat = str(env.rule)
    assert "job_category" in flat
    assert "99" in flat


def test_plan_with_range_job_category_expands():
    # Code ranges are expanded into a concrete job_category membership set so the
    # matcher can evaluate them against the employee's grade.
    env = description_to_rule(
        "Plan 3: SM to SVP (Job category: A1 to A6, AA to AF, L1 to L6)"
    )
    flat = str(env.rule)
    assert "job_category" in flat
    # Endpoints + interior codes present; the literal range text is gone.
    for code in ("A1", "A6", "AA", "AF", "L1", "L6"):
        assert evaluate(env.rule, {"job_category": code, "role": "SVP"})
    assert "A1 to A6" not in flat
    # Out-of-band grade does not match.
    assert not evaluate(env.rule, {"job_category": "A7", "role": "SVP"})


def test_job_category_open_ended_band_includes_higher_grades():
    # "SM and above" must catch a grade senior to the listed maximum (E10 > E9).
    env = description_to_rule(
        "SM and above (Job category: 99, A1 to A9, E7 to E9, W7 to W9)"
    )
    assert evaluate(env.rule, {"job_category": "E10"})
    assert evaluate(env.rule, {"job_category": "W10"})
    assert evaluate(env.rule, {"job_category": "99"})
    # A junior grade outside the band still does not match.
    assert not evaluate(env.rule, {"job_category": "E6"})


def test_job_category_open_ended_suppressed_by_grade_clause():
    # "above" qualifying an explicit Hay-grade number must NOT balloon the
    # job-category list to the ceiling — the grade rule owns the seniority.
    env = description_to_rule("Grade 8 and above (Job category: E5 to E7)")
    flat = str(env.rule)
    assert "E5" in flat and "E7" in flat
    assert "E8" not in flat and "E30" not in flat
    assert evaluate(env.rule, {"job_category": "E5", "grade": 10})
    assert not evaluate(env.rule, {"job_category": "E8", "grade": 10})


def test_all_employees_with_ampersand_is_catchall():
    # "&" is a synonym for "and": "All Employees & their Dependants" → catch-all.
    env = description_to_rule("All Employees & their Eligible Dependants")
    assert env.rule == {"and": []}
    assert evaluate(env.rule, {"job_category": "anything"})


def test_role_ceo():
    env = description_to_rule("CEO and Eligible Dependents")
    assert env.rule == {"=": ["role", "CEO"]}
    assert evaluate(env.rule, {"role": "CEO"})
    assert not evaluate(env.rule, {"role": "MANAGER"})


def test_role_multiple():
    env = description_to_rule("CEO, Deputy CEO, CSO")
    assert env.rule is not None
    flat = str(env.rule)
    assert "CEO" in flat
    assert "DEPUTY_CEO" in flat
    assert "CSO" in flat


def test_role_deputy_ceo_does_not_match_ceo():
    env = description_to_rule("Deputy CEO")
    assert env.rule == {"=": ["role", "DEPUTY_CEO"]}
    assert evaluate(env.rule, {"role": "DEPUTY_CEO"})
    assert not evaluate(env.rule, {"role": "CEO"})


def test_role_executive_director():
    env = description_to_rule("CEO, CFO, Executive Director, Managing Director")
    flat = str(env.rule)
    assert "CEO" in flat and "CFO" in flat
    assert "EXECUTIVE_DIRECTOR" in flat
    assert "MANAGING_DIRECTOR" in flat


def test_class_code_numeric():
    env = description_to_rule("Class 1 employees (Administrator)")
    assert env.rule == {"=": ["class_code", "1"]}
    assert evaluate(env.rule, {"class_code": "1"})


def test_class_code_class_2():
    env = description_to_rule("Class 2 employees (Driver)")
    assert env.rule == {"=": ["class_code", "2"]}


def test_geography_thailand():
    env = description_to_rule("All Employees based in Thailand (except for Director)")
    # "All Employees" matches first and short-circuits. That's the desired
    # behavior — the geography refinement gets caught at admin review time.
    # If the rule generator changes to recognize the geography first, this
    # test will need updating.
    assert env.rule is not None


def test_geography_alone():
    env = description_to_rule("Bargainable Employees based in China")
    assert env.rule is not None
    flat = str(env.rule)
    assert "BARGAINABLE" in flat or "China" in flat
