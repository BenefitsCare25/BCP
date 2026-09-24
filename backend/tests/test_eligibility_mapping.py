"""Company-aware eligibility mapping proposals and validation."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    DEMO_BROKER_FIRM_ID,
    CurrentUser,
    get_current_user,
)
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Category,
    Client,
    EligibilityMappingProfile,
    Employee,
    EmployeeAttributeSchema,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus
from app.models.policy_year import PolicyYearStatus
from app.schemas.rule import RuleEnvelope
from app.services.ai_gateway import AICallResult
from app.services.eligibility_mapping import (
    AttributeValueCatalog,
    CategoryConfirmationBatch,
    _assignment_counts,
    _exclude_separate_location_cohorts,
    _rule_without_product_location_context,
    _separate_location_cohorts,
    auto_map_policy_year,
    build_ai_eligibility_inputs,
    build_attribute_catalog,
    category_signature,
    confirm_category_mapping,
    current_category_overlaps,
    normalize_ai_matching_rule,
    propose_category_rule,
    validate_ai_matching_rule,
    validate_matching_rule,
)
from app.services.rule_evaluator import evaluate
from scripts.seed_demo import seed

CLIENT_ID = "00000000-0000-0000-0000-00000000e101"
PY_2026 = "00000000-0000-0000-0000-00000000e126"
PY_2027 = "00000000-0000-0000-0000-00000000e127"
GRADE_DESCRIPTION = (
    "SM and above (Job Category:99, A1 to A9, AA to AG, L1 to L6, E7 to E9, EG to EI, W7 to W9)"
)
GRADE_ORDER = [
    "99",
    *[f"A{number}" for number in range(1, 10)],
    *[f"A{letter}" for letter in "ABCDEFG"],
    *[f"L{number}" for number in range(1, 7)],
    *[f"E{number}" for number in range(7, 10)],
    "EG",
    "EH",
    "EI",
    *[f"W{number}" for number in range(7, 10)],
]


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000e1aa",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID,
        role="broker_admin",
    )


def _catalog(**values: list[str]) -> AttributeValueCatalog:
    return AttributeValueCatalog(
        values=values,
        data_types={key: "string" for key in values},
        populated={key: sum(1 for value in vals if value) for key, vals in values.items()},
        employee_count=max((len(vals) for vals in values.values()), default=0),
    )


@pytest.fixture(scope="module", autouse=True)
def _seed_mapping_data() -> None:
    seed()
    with SessionLocal() as db:
        db.add(
            Client(
                id=CLIENT_ID,
                name="Eligibility Mapping Test",
                broker_firm_id=DEMO_BROKER_FIRM_ID,
            )
        )
        for policy_year_id, year in ((PY_2026, 2026), (PY_2027, 2027)):
            db.add(
                PolicyYear(
                    id=policy_year_id,
                    client_id=CLIENT_ID,
                    year=year,
                    start_date=date(year, 1, 1),
                    end_date=date(year, 12, 31),
                    status=PolicyYearStatus.draft,
                )
            )
        db.add(
            EmployeeAttributeSchema(
                client_id=CLIENT_ID,
                attribute_id="employment_type",
                display_name="Employment Type",
                data_type="enum",
                enum_values=["MANUAL", "NON-MANUAL", "DRIVER"],
                is_required=False,
                is_pii=False,
            )
        )
        db.commit()


@pytest.fixture(scope="module")
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_category_signature_strips_plan_and_dependant_noise() -> None:
    left = category_signature(
        "Plan 1 - Senior Vice President / General Manager and their Eligible Dependants"
    )
    right = category_signature("Senior Vice President / General Manager")
    assert left == right


def test_mcil_executive_band_uses_company_designation_values() -> None:
    proposal = propose_category_rule(
        "Senior Vice President & Above / Vice President / General Managers",
        _catalog(
            designation=[
                "Senior Vice President",
                "Vice President",
                "General Manager",
                "Manager",
                "Trainee",
            ]
        ),
    )

    assert proposal.rule == {
        "in": [
            "designation",
            ["Senior Vice President", "Vice President", "General Manager"],
        ]
    }
    # "and above" cannot be converted into an open-ended title hierarchy from
    # the slip alone, so the useful rule is proposed but not auto-confirmable.
    assert "above" in proposal.unresolved_clauses
    assert proposal.validation_state == "needs_review"


def test_mcil_plan_descriptions_map_against_the_company_title_vocabulary() -> None:
    catalog = _catalog(
        designation=[
            "Senior Vice President",
            "Vice President",
            "General Manager",
            "Director",
            "Assistant Director",
            "EXCO",
            "Head of Department",
            "Assistant Head of Department",
            "Senior Manager",
            "Manager",
            "Assistant Manager",
            "Senior Executive",
            "Executive",
            "Ala Carte Telesales",
            "Rank & File",
            "Trainee",
        ]
    )

    plan_1 = propose_category_rule(
        "Plan 1 - Senior Vice President & above / Vice President / General "
        "Manager/ Director / Asst Director / EXCOs / Head of Department and "
        "their Eligible Dependants",
        catalog,
    )
    plan_2 = propose_category_rule(
        "Plan 2 - Assistant Head of Department / Senior Manager / Manager / "
        "Assistant Manager / Senior Executive / Executive",
        catalog,
    )
    plan_3 = propose_category_rule(
        "Plan 3 - Ala Carte Telesales / Rank & File (including Trainees)",
        catalog,
    )

    assert plan_1.rule == {
        "in": [
            "designation",
            [
                "Senior Vice President",
                "Vice President",
                "General Manager",
                "Director",
                "Assistant Director",
                "EXCO",
                "Head of Department",
            ],
        ]
    }
    # Abbreviations and ordinal hierarchy are never guessed. They remain a
    # visible review item while every exact company title is still usable.
    assert set(plan_1.unresolved_clauses) == {"above"}
    assert plan_2.rule == {
        "in": [
            "designation",
            [
                "Assistant Head of Department",
                "Senior Manager",
                "Manager",
                "Assistant Manager",
                "Senior Executive",
                "Executive",
            ],
        ]
    }
    assert plan_3.rule == {
        "in": [
            "designation",
            ["Ala Carte Telesales", "Rank & File", "Trainee"],
        ]
    }


def test_all_other_employees_is_product_remainder() -> None:
    proposal = propose_category_rule("All Other Employees", _catalog())
    assert proposal.rule == {"and": []}
    assert proposal.relative_remainder is True
    assert proposal.validation_state == "proposed"


def test_all_other_excluding_trainees_keeps_exclusion() -> None:
    proposal = propose_category_rule(
        "All Other Employees (Excluding Trainees)",
        _catalog(designation=["Manager", "Executive", "Trainee"]),
    )
    assert proposal.rule == {"not_in": ["designation", ["Trainee"]]}
    assert proposal.relative_remainder is True
    assert proposal.unresolved_clauses == []


def test_foreign_worker_rule_keeps_spass_and_work_permit_in_either_order() -> None:
    proposal = propose_category_rule(
        "Foreign workers on S-pass or Work permit",
        _catalog(pass_type=["EP", "SP", "WP"]),
    )
    assert proposal.rule == {"in": ["pass_type", ["SP", "WP"]]}
    assert proposal.unresolved_clauses == []


def test_manual_category_does_not_absorb_non_manual_value() -> None:
    catalog = _catalog(
        employment_type=["MANUAL", "NON-MANUAL", "DRIVER"],
    )
    manual = propose_category_rule("MANUAL EMPLOYEES", catalog)
    non_manual = propose_category_rule("NON-MANUAL EMPLOYEES", catalog)
    assert manual.rule == {"=": ["employment_type", "MANUAL"]}
    assert non_manual.rule == {"=": ["employment_type", "NON-MANUAL"]}


def test_currency_marker_does_not_map_to_single_character_roster_value() -> None:
    proposal = propose_category_rule(
        "Non-Manual Employees earning above S$1,600 per month",
        _catalog(family_status=["S", "M"]),
    )

    assert proposal.rule is None
    assert proposal.unresolved_clauses


def test_based_in_country_uses_work_location_not_nationality() -> None:
    proposal = propose_category_rule(
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3, JA to JC)",
        _catalog(
            job_category=["J1", "J2", "J3", "JA", "JB", "JC", "D1"],
            country_of_work=["Singapore", "Thailand"],
            nationality=["Singapore", "Thailand"],
            executive_role=["DIRECTOR", "EMPLOYEE"],
        ),
    )

    assert proposal.rule == {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {
                "and": [
                    {"=": ["country_of_work", "Thailand"]},
                    {"not_in": ["executive_role", ["DIRECTOR"]]},
                ]
            },
        ]
    }
    assert proposal.unresolved_clauses == []


def test_ai_rule_rejects_roster_category_proxy_for_explicit_job_codes() -> None:
    description = (
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3, JA to JC)"
    )
    catalog = _catalog(
        job_category=["J1", "J2", "J3", "JA", "JB", "JC"],
        category=["Officer", "All Employees based in Thailand (except for Director)"],
    )
    broad_rule = {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {"=": ["category", "Officer"]},
        ]
    }

    validation = validate_ai_matching_rule(description, broad_rule, catalog)

    assert not validation.valid
    assert any("roster category text" in error for error in validation.errors)


def test_upload_proposal_joins_explicit_officer_codes_to_exact_location_cohort() -> None:
    description = (
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3, JA to JC)"
    )
    catalog = _catalog(
        job_category=["J1", "J2", "J3", "JA", "JB", "JC", "X1"],
        category=["Officer", "All Employees based in Thailand (except for Director)"],
    )

    proposal = propose_category_rule(description, catalog)

    assert proposal.rule == {"or": [
        {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
        {"=": ["category", "All Employees based in Thailand (except for Director)"]},
    ]}
    assert proposal.unresolved_clauses == []
    assert validate_ai_matching_rule(description, proposal.rule, catalog).valid


def test_location_cohort_requires_same_exclusion_as_slip() -> None:
    description = (
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3)"
    )
    catalog = _catalog(
        job_category=["J1", "J2", "J3"],
        category=["All Employees based in Thailand"],
    )

    proposal = propose_category_rule(description, catalog)

    assert proposal.unresolved_clauses
    assert not validate_ai_matching_rule(
        description,
        {"or": [
            {"in": ["job_category", ["J1", "J2", "J3"]]},
            {"=": ["category", "All Employees based in Thailand"]},
        ]},
        catalog,
    ).valid


def test_explicit_grades_cannot_be_bypassed_by_or_branch() -> None:
    catalog = _catalog(
        job_category=["A1", "E10"],
        category=["SM to SVP", "All Employees based in Thailand"],
    )
    description = "SM to SVP (Job Category: A1)"

    for extra in (
        {"not_in": ["category", ["All Employees based in Thailand"]]},
        {"=": ["category", "All Employees based in Thailand"]},
    ):
        validation = validate_ai_matching_rule(
            description,
            {"or": [{"=": ["job_category", "A1"]}, extra]},
            catalog,
        )
        assert not validation.valid
        assert any("outside the slip" in error for error in validation.errors)


def test_separately_priced_location_cohort_is_excluded_from_grade_bands() -> None:
    location = "All Employees based in Thailand (except for Director)"
    catalog = _catalog(
        job_category=["A1", "X1", "J1"],
        category=["SM to SVP", "Executive", location],
    )
    product_id = "life-product"
    thailand = Category(
        product_id=product_id,
        display_name=location,
        raw_description=location,
    )
    senior = Category(
        product_id=product_id,
        display_name="SM to SVP (Job category: A1)",
        raw_description="SM to SVP (Job category: A1)",
    )
    cohorts = _separate_location_cohorts([thailand, senior], catalog)
    proposal = propose_category_rule(senior.raw_description, catalog)
    narrowed = _exclude_separate_location_cohorts(proposal, senior, cohorts[product_id])

    assert narrowed.rule == {"and": [
        {"=": ["job_category", "A1"]},
        {"not": {"in": ["category", [location]]}},
    ]}
    assert validate_ai_matching_rule(
        senior.raw_description,
        narrowed.rule,
        catalog,
        location_exclusions=cohorts[product_id],
    ).valid
    assert evaluate(narrowed.rule, {"job_category": "A1", "category": "SM to SVP"})
    assert evaluate(narrowed.rule, {"job_category": "A1"})
    assert not evaluate(narrowed.rule, {"job_category": "A1", "category": location})
    assert (
        _exclude_separate_location_cohorts(narrowed, senior, cohorts[product_id]).rule
        == narrowed.rule
    )
    assert _rule_without_product_location_context(
        narrowed.rule, {"product_location_exclusions": cohorts[product_id]}
    ) == proposal.rule


def test_location_exclusion_respects_category_insured_entities() -> None:
    location = "All Employees based in Thailand"
    catalog = _catalog(job_category=["A1"], category=[location])
    thailand = Category(
        product_id="life-product",
        raw_description=location,
        plan_assignments={"insured": ["Entity A"]},
    )
    grade = Category(
        product_id="life-product",
        raw_description="Senior (Job Category: A1)",
        plan_assignments={"insured": ["Entity B"]},
    )
    scoped = _separate_location_cohorts(
        [thailand, grade], catalog, target_category=grade
    )
    proposal = propose_category_rule(grade.raw_description, catalog)

    assert scoped.get(grade.product_id, {}) == {}
    assert _exclude_separate_location_cohorts(
        proposal, grade, scoped.get(grade.product_id, {})
    ).rule == proposal.rule
    assert evaluate(proposal.rule, {"job_category": "A1", "category": location})

    grade.plan_assignments = {"insured": ["Entity A", "Entity B"]}
    overlapping = _separate_location_cohorts(
        [thailand, grade], catalog, target_category=grade
    )
    assert overlapping[grade.product_id] == {"category": [location]}

    grade.plan_assignments = {"insured": ["Entity B"]}
    product_scoped = _separate_location_cohorts(
        [thailand, grade],
        catalog,
        target_category=grade,
        product_gate=frozenset({"entity b"}),
    )
    assert product_scoped[grade.product_id] == {"category": [location]}


def test_upload_proposal_does_not_replace_unmapped_codes_with_text_cohort() -> None:
    description = "Officer (Job Category: J1 to J3)"
    catalog = _catalog(category=["Officer"])

    proposal = propose_category_rule(description, catalog)
    validation = validate_ai_matching_rule(
        description, {"=": ["category", "Officer"]}, catalog
    )

    assert proposal.rule is None
    assert proposal.unresolved_clauses
    assert not validation.valid
    assert any("Could not map explicit employee codes" in error for error in validation.errors)


def test_ai_rule_cannot_widen_explicit_codes_to_other_roster_values() -> None:
    description = "Officer (Job Category: J1 to J3)"
    catalog = _catalog(job_category=["J1", "J2", "J3", "X1"])

    validation = validate_ai_matching_rule(
        description,
        {"in": ["job_category", ["J1", "J2", "J3", "X1"]]},
        catalog,
    )

    assert not validation.valid
    assert any("X1 outside codes stated" in error for error in validation.errors)


def test_ai_rule_rejects_text_proxy_when_job_codes_live_in_job_grade() -> None:
    description = "Officer (Job Category: J1 to J3)"
    catalog = _catalog(job_grade=["J1", "J2", "J3"], category=["Officer"])

    validation = validate_ai_matching_rule(
        description,
        {"or": [{"in": ["job_grade", ["J1", "J2", "J3"]]}, {"=": ["category", "Officer"]}]},
        catalog,
    )

    assert not validation.valid
    assert any("roster category text" in error for error in validation.errors)


def test_based_in_country_uses_reviewed_nationality_proxy_as_last_resort() -> None:
    proposal = propose_category_rule(
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3, JA to JC)",
        _catalog(
            job_category=["J1", "J2", "J3", "JA", "JB", "JC", "D1"],
            nationality=["Singapore", "Thailand"],
            executive_role=["DIRECTOR", "EMPLOYEE"],
        ),
    )

    assert proposal.rule == {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {
                "and": [
                    {"=": ["nationality", "Thailand"]},
                    {"not_in": ["executive_role", ["DIRECTOR"]]},
                ]
            },
        ]
    }
    assert proposal.unresolved_clauses == [
        "based in Thailand mapped through nationality; confirm nationality represents work base"
    ]


def test_based_in_thailand_maps_to_thai_roster_nationality() -> None:
    proposal = propose_category_rule(
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3, JA to JC)",
        _catalog(
            job_category=["J1", "J2", "J3", "JA", "JB", "JC", "D1"],
            nationality=["Singaporean", "Thai"],
            executive_role=["DIRECTOR", "EMPLOYEE"],
        ),
    )

    assert proposal.rule == {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {
                "and": [
                    {"=": ["nationality", "Thai"]},
                    {"not_in": ["executive_role", ["DIRECTOR"]]},
                ]
            },
        ]
    }
    assert proposal.unresolved_clauses == [
        "based in Thailand mapped through nationality; confirm nationality represents work base"
    ]


def test_based_in_multiple_countries_maps_every_exact_nationality_alias() -> None:
    proposal = propose_category_rule(
        "Officer and All Employees based in Thailand and Vietnam (except for Director) "
        "(Job Category: J1 to J3, JA to JC)",
        _catalog(
            job_category=["J1", "J2", "J3", "JA", "JB", "JC", "D1"],
            nationality=["Singaporean", "Thai", "Vietnamese"],
            executive_role=["DIRECTOR", "EMPLOYEE"],
        ),
    )

    assert proposal.rule == {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {
                "and": [
                    {"in": ["nationality", ["Thai", "Vietnamese"]]},
                    {"not_in": ["executive_role", ["DIRECTOR"]]},
                ]
            },
        ]
    }
    assert proposal.unresolved_clauses == [
        "based in Thailand and Vietnam mapped through nationality; "
        "confirm nationality represents work base"
    ]


def test_unsupported_location_does_not_fuzzy_match_short_nationality_alias() -> None:
    proposal = propose_category_rule(
        "Officer and All Employees based in Myanmar (except for Director) "
        "(Job Category: J1 to J3, JA to JC)",
        _catalog(
            job_category=["J1", "J2", "J3", "JA", "JB", "JC", "D1"],
            nationality=["Malaysian"],
            executive_role=["DIRECTOR", "EMPLOYEE"],
        ),
    )

    assert proposal.rule == {
        "in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]
    }
    assert proposal.unresolved_clauses == ["based in Myanmar"]


def test_mixed_supported_and_unknown_locations_do_not_create_a_partial_rule() -> None:
    proposal = propose_category_rule(
        "Officer and All Employees based in Thailand and Myanmar (except for Director) "
        "(Job Category: J1 to J3, JA to JC)",
        _catalog(
            job_category=["J1", "J2", "J3", "JA", "JB", "JC", "D1"],
            nationality=["Thailand"],
            executive_role=["DIRECTOR", "EMPLOYEE"],
        ),
    )

    assert proposal.rule == {
        "in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]
    }
    assert proposal.unresolved_clauses == ["based in Thailand and Myanmar"]


def test_ai_rule_restores_safe_country_branch_when_model_omits_it() -> None:
    description = (
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3, JA to JC)"
    )
    catalog = _catalog(
        job_category=["J2", "J3", "JB", "J1", "JC", "JA", "D1"],
        nationality=["Singapore", "Thailand"],
        executive_role=["DIRECTOR", "EMPLOYEE"],
    )

    normalized, review_clauses = normalize_ai_matching_rule(
        description,
        {"in": ["job_category", ["J2", "J3", "JB", "J1", "JC", "JA"]]},
        catalog,
    )

    assert normalized == {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {
                "and": [
                    {"=": ["nationality", "Thailand"]},
                    {"not_in": ["executive_role", ["DIRECTOR"]]},
                ]
            },
        ]
    }
    assert review_clauses == [
        "based in Thailand mapped through nationality; confirm nationality represents work base"
    ]
    assert validate_ai_matching_rule(description, normalized, catalog).valid


def test_reviewed_nationality_union_can_be_confirmed() -> None:
    description = (
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3)"
    )
    with SessionLocal() as db:
        product = Product(
            client_id=CLIENT_ID,
            code="THAI-PROXY-CONFIRM-QA",
            display_name="Thai proxy confirmation QA",
        )
        db.add(product)
        db.flush()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            display_name="Thailand officers",
            raw_description=description,
            status=CategoryStatus.needs_review.value,
            plan_assignments={"plan_code": "THAI-PROXY"},
        )
        db.add(category)
        db.add_all([
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-THAI-PROXY-OFFICER",
                attribute_values={
                    "job_category": "J1", "nationality": "Singapore",
                    "role": "EMPLOYEE",
                },
            ),
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-THAI-PROXY-THAI",
                attribute_values={
                    "job_category": "D1", "nationality": "Thailand",
                    "role": "EMPLOYEE",
                },
            ),
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-THAI-PROXY-DIRECTOR",
                attribute_values={
                    "job_category": "D1", "nationality": "Thailand", "role": "DIRECTOR",
                },
            ),
        ])
        db.flush()
        catalog, _, _ = build_attribute_catalog(db, PY_2026, CLIENT_ID)
        proposal = propose_category_rule(description, catalog)
        assert proposal.unresolved_clauses == [
            "based in Thailand mapped through nationality; confirm nationality represents work base"
        ]
        category.matching_rule = proposal.rule

        profile = confirm_category_mapping(db, category=category, client_id=CLIENT_ID)

        assert profile.matching_rule == proposal.rule
        assert category.status == CategoryStatus.confirmed.value
        assert evaluate(category.matching_rule, {
            "job_category": "D1", "nationality": "Thailand", "role": "EMPLOYEE"
        })
        db.rollback()


@pytest.mark.parametrize(
    "ai_rule",
    [
        {"in": ["job_category", ["J2", "J3", "JB", "J1", "JC", "JA"]]},
        {
            "or": [
                {"in": ["job_category", ["J2", "J3", "JB", "J1", "JC", "JA"]]},
                {
                    "and": [
                        {"=": ["nationality", "Thailand"]},
                        {"not_in": ["executive_role", ["DIRECTOR"]]},
                    ]
                },
            ]
        },
    ],
)
def test_ai_rule_normalizes_thailand_to_thai_roster_value(ai_rule: dict) -> None:
    description = (
        "Officer and All Employees based in Thailand (except for Director) "
        "(Job Category: J1 to J3, JA to JC)"
    )
    catalog = _catalog(
        job_category=["J2", "J3", "JB", "J1", "JC", "JA", "D1"],
        nationality=["Singaporean", "Thai"],
        executive_role=["DIRECTOR", "EMPLOYEE"],
    )

    normalized, review_clauses = normalize_ai_matching_rule(
        description, ai_rule, catalog
    )

    assert normalized == {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {
                "and": [
                    {"=": ["nationality", "Thai"]},
                    {"not_in": ["executive_role", ["DIRECTOR"]]},
                ]
            },
        ]
    }
    assert review_clauses == [
        "based in Thailand mapped through nationality; confirm nationality represents work base"
    ]


def test_ai_rule_restores_every_country_in_multi_country_location() -> None:
    description = (
        "Officer and All Employees based in Thailand and Vietnam (except for Director) "
        "(Job Category: J1 to J3, JA to JC)"
    )
    catalog = _catalog(
        job_category=["J2", "J3", "JB", "J1", "JC", "JA", "D1"],
        nationality=["Singaporean", "Thai", "Vietnamese"],
        executive_role=["DIRECTOR", "EMPLOYEE"],
    )

    normalized, review_clauses = normalize_ai_matching_rule(
        description,
        {"in": ["job_category", ["J2", "J3", "JB", "J1", "JC", "JA"]]},
        catalog,
    )

    assert normalized == {
        "or": [
            {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]},
            {
                "and": [
                    {"in": ["nationality", ["Thai", "Vietnamese"]]},
                    {"not_in": ["executive_role", ["DIRECTOR"]]},
                ]
            },
        ]
    }
    assert review_clauses == [
        "based in Thailand and Vietnam mapped through nationality; "
        "confirm nationality represents work base"
    ]


def test_ai_location_merge_preserves_every_existing_constraint() -> None:
    description = "Permanent female employees based in Thailand"
    catalog = _catalog(
        employment_type=["Permanent", "Contract"],
        gender=["Female", "Male"],
        nationality=["Singapore", "Thailand"],
    )
    ai_rule = {
        "and": [
            {"=": ["employment_type", "Permanent"]},
            {"=": ["gender", "Female"]},
        ]
    }

    normalized, review_clauses = normalize_ai_matching_rule(
        description, ai_rule, catalog
    )

    assert normalized == {
        "and": [
            {"=": ["employment_type", "Permanent"]},
            {"=": ["gender", "Female"]},
            {"=": ["nationality", "Thailand"]},
        ]
    }
    assert review_clauses == [
        "based in Thailand mapped through nationality; confirm nationality represents work base"
    ]


def test_explicit_job_category_ranges_are_complete_and_source_ordered() -> None:
    observed = [
        "A3",
        "A1",
        "W9",
        "E7",
        "E9",
        "A4",
        "EH",
        "A8",
        "E8",
        "L4",
        "EG",
        "A7",
        "L6",
        "A2",
        "L2",
        "W8",
        "A9",
        "W7",
        "A6",
        "A5",
        "AA",
        "99",
        "L5",
        "L3",
        "L1",
    ]
    catalog = _catalog(job_category=observed)

    proposal = propose_category_rule(GRADE_DESCRIPTION, catalog)

    assert proposal.rule == {"in": ["job_category", GRADE_ORDER]}
    assert proposal.unresolved_clauses == []
    validation = validate_ai_matching_rule(GRADE_DESCRIPTION, proposal.rule, catalog)
    assert validation.valid is True
    assert validation.errors == []
    assert validation.warnings == [
        f"Configured value {value} has no active employees in job_category"
        for value in ["AB", "AC", "AD", "AE", "AF", "AG", "EI"]
    ]


@pytest.mark.parametrize(
    ("range_text", "catalog_values"),
    [
        ("1 to 201", ["1", "201"]),
        ("A to GS", ["A", "GS"]),
    ],
)
def test_explicit_job_category_range_over_limit_stays_unresolved(
    range_text: str, catalog_values: list[str]
) -> None:
    proposal = propose_category_rule(
        f"Employees (Job Category: {range_text})",
        _catalog(job_category=catalog_values),
    )

    assert proposal.rule is None
    assert proposal.unresolved_clauses == [range_text]


def test_ai_job_category_rule_is_completed_and_ordered_from_source() -> None:
    catalog = _catalog(job_category=["A3", "A1", "W9", "EG", "AA", "99"])

    normalized, review_clauses = normalize_ai_matching_rule(
        GRADE_DESCRIPTION,
        {"in": ["job_category", ["A3", "A1", "W9", "EG", "AA", "99"]]},
        catalog,
    )

    assert normalized == {"in": ["job_category", GRADE_ORDER]}
    assert review_clauses == []


def test_location_only_category_prefers_work_location_then_nationality_proxy() -> None:
    mapped = propose_category_rule(
        "All Employees based in Thailand",
        _catalog(
            country_of_work=["Singapore", "Thailand"],
            nationality=["Singapore", "Thailand"],
        ),
    )
    unresolved = propose_category_rule(
        "All Employees based in Thailand",
        _catalog(nationality=["Singapore", "Thailand"]),
    )

    assert mapped.rule == {"=": ["country_of_work", "Thailand"]}
    assert mapped.unresolved_clauses == []
    assert unresolved.rule == {"=": ["nationality", "Thailand"]}
    assert unresolved.unresolved_clauses == [
        "based in Thailand mapped through nationality; confirm nationality represents work base"
    ]


def test_explicit_nationality_uses_only_nationality_field() -> None:
    proposal = propose_category_rule(
        "Thailand nationals",
        _catalog(
            nationality=["Singapore", "Thailand"],
            country_of_work=["Singapore", "Thailand"],
        ),
    )

    assert proposal.rule == {"=": ["nationality", "Thailand"]}
    assert proposal.referenced_attributes == ["nationality"]


def test_explicit_cost_centre_uses_only_cost_centre_field() -> None:
    proposal = propose_category_rule(
        "Employees in Cost Centre TH01",
        _catalog(
            cost_centre=["SG01", "TH01"],
            country_of_work=["SG01", "TH01"],
        ),
    )

    assert proposal.rule == {"=": ["cost_centre", "TH01"]}
    assert proposal.referenced_attributes == ["cost_centre"]


def test_matching_rule_validation_rejects_unknown_or_empty_attributes() -> None:
    catalog = _catalog(designation=["Manager", "Executive"])
    unknown = validate_matching_rule({"=": ["job_band", "M1"]}, catalog)
    empty = validate_matching_rule({"=": ["pass", "SP"]}, _catalog(**{"pass": []}))
    valid = validate_matching_rule({"in": ["designation", ["Manager"]]}, catalog)

    assert unknown.valid is False
    assert unknown.errors == ["Unknown employee attribute: job_band"]
    assert empty.valid is False
    assert empty.errors == ["Employee attribute pass has no values in the employee listing"]
    assert valid.valid is True
    assert valid.errors == []


def test_matching_rule_validation_rejects_hallucinated_company_values() -> None:
    """An AI rule may only use a value known to this company.

    Attribute-only validation used to accept a plausible-looking but invented
    job grade, which then matched nobody after it had already been persisted.
    """

    catalog = _catalog(designation=["Manager", "Executive"])

    result = validate_matching_rule({"in": ["designation", ["Manager", "Chief Wizard"]]}, catalog)

    assert result.valid is False
    assert result.errors == ["Unknown company value for designation: Chief Wizard"]


def test_ai_rule_cannot_silently_expand_specific_wording_to_everyone() -> None:
    catalog = _catalog(designation=["Manager", "Executive"])

    specific = validate_ai_matching_rule("Managers only", {"and": []}, catalog)
    explicit = validate_ai_matching_rule("All employees", {"and": []}, catalog)

    assert specific.valid is False
    assert "only when the eligibility wording says so" in specific.errors[0]
    assert explicit.valid is True


def test_matching_rule_validation_bounds_nesting_depth() -> None:
    rule: dict = {"=": ["designation", "Manager"]}
    for _ in range(20):
        rule = {"not": rule}

    result = validate_matching_rule(rule, _catalog(designation=["Manager", "Executive"]))

    assert result.valid is False
    assert any("levels" in error for error in result.errors)


@pytest.mark.parametrize(
    "rule",
    [
        {"between": ["grade", 1]},
        {"in": ["designation", "Manager"]},
        {"not": ["not-a-rule"]},
        {"and": "not-a-list"},
    ],
)
def test_matching_rule_validation_rejects_malformed_operator_arguments(
    rule: dict,
) -> None:
    catalog = AttributeValueCatalog(
        values={"grade": [1, 2], "designation": ["Manager"]},
        data_types={"grade": "integer", "designation": "string"},
        populated={"grade": 2, "designation": 1},
        employee_count=2,
    )

    result = validate_matching_rule(rule, catalog)

    assert result.valid is False
    assert result.errors


def test_policy_year_mapping_persists_validated_rule_and_profile() -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=1,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "1", "num_employees": 1},
        )
        db.add(category)
        db.add(
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-MANUAL",
                employee_name="Manual Employee",
                attribute_values={"employment_type": "MANUAL"},
                derived_attribute_values={},
            )
        )
        db.flush()

        summary = auto_map_policy_year(db, policy_year_id=PY_2026, client_id=CLIENT_ID)
        item = next(row for row in summary.categories if row.category_id == category.id)

        assert category.matching_rule == {"=": ["employment_type", "MANUAL"]}
        assert item.rule_status == "validated"
        assert item.matched_count == 1
        assert item.expected_count == 1
        assert category.mapping_profile_id is not None

        # Confirmation must not keep an older review result beside "validated".
        category.rule_validation = {
            **(category.rule_validation or {}),
            "warnings": ["1 employees also match an equally specific employee cohort"],
            "overlap_count": 1,
            "unresolved_clauses": ["obsolete suggestion"],
        }
        profile = confirm_category_mapping(db, category=category, client_id=CLIENT_ID)
        assert profile.status == "confirmed"
        assert category.status == CategoryStatus.confirmed.value
        assert category.rule_validation["warnings"] == []
        assert category.rule_validation["overlap_count"] == 0
        assert category.rule_validation["unresolved_clauses"] == []
        db.commit()


def test_manual_confirmation_accepts_equivalent_grade_attribute() -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=202,
            display_name="Manual alternate grade field",
            raw_description="Manual alternate grade field (Job Category:A1)",
            matching_rule={"=": ["job_grade", "A1"]},
            status=CategoryStatus.needs_review.value,
            source="manual",
            human_modified=True,
            plan_assignments={"plan_code": "MANUAL-ALT-GRADE"},
        )
        db.add(category)
        db.add(
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-MANUAL-ALT-GRADE",
                employee_name="Manual Alternate Grade",
                attribute_values={"job_grade": "A1"},
                derived_attribute_values={},
            )
        )
        db.flush()

        profile = confirm_category_mapping(db, category=category, client_id=CLIENT_ID)

        assert profile.status == "confirmed"
        assert category.status == CategoryStatus.confirmed.value
        assert profile.required_attributes == ["job_grade"]
        db.rollback()


def test_confirmation_rejects_rule_that_bypasses_slip_grade_codes() -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            display_name="Grade A1",
            raw_description="Grade A1 (Job Category: A1)",
            matching_rule={"or": [
                {"=": ["job_grade", "A1"]},
                {"!=": ["employment_type", "MANUAL"]},
            ]},
            status=CategoryStatus.needs_review.value,
            source="manual",
            human_modified=True,
            plan_assignments={"plan_code": "GRADE-SOURCE-GUARD"},
        )
        db.add(category)
        db.add(Employee(
            client_id=CLIENT_ID,
            policy_year_id=PY_2026,
            staff_id="E-GRADE-SOURCE-GUARD",
            attribute_values={"job_grade": "A1", "employment_type": "MANUAL"},
            derived_attribute_values={},
        ))
        db.flush()

        with pytest.raises(ValueError, match="outside the slip's explicit codes"):
            confirm_category_mapping(db, category=category, client_id=CLIENT_ID)

        assert category.status == CategoryStatus.needs_review.value
        db.rollback()


def test_confirmed_profile_drops_product_location_guard() -> None:
    location = "All Employees based in Thailand (except for Director)"
    guard = {"category": [location]}
    with SessionLocal() as db:
        product = Product(
            client_id=CLIENT_ID,
            code="LOCATION-PROFILE-QA",
            display_name="Location profile QA",
        )
        db.add(product)
        db.flush()
        db.add(Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            display_name=location,
            raw_description=location,
            matching_rule={"=": ["category", location]},
            status=CategoryStatus.needs_review.value,
            plan_assignments={"plan_code": "LOCATION"},
        ))
        senior = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            display_name="Senior profile guard",
            raw_description="Senior profile guard (Job Category: A1)",
            matching_rule={"and": [
                {"=": ["job_grade", "A1"]},
                {"not_in": ["category", [location]]},
            ]},
            rule_validation={"product_location_exclusions": guard},
            status=CategoryStatus.needs_review.value,
            plan_assignments={"plan_code": "SENIOR"},
        )
        db.add(senior)
        db.add_all([
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-LOCATION-PROFILE-SENIOR",
                attribute_values={"job_grade": "A1", "category": "Senior profile guard"},
            ),
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-LOCATION-PROFILE-THAILAND",
                attribute_values={"job_grade": "A1", "category": location},
            ),
        ])
        db.flush()

        profile = confirm_category_mapping(db, category=senior, client_id=CLIENT_ID)

        assert senior.status == CategoryStatus.confirmed.value
        assert senior.matching_rule == {"and": [
            {"=": ["job_grade", "A1"]},
            {"not_in": ["category", [location]]},
        ]}
        assert profile.matching_rule == {"=": ["job_grade", "A1"]}
        assert profile.required_attributes == ["job_grade"]
        db.rollback()


def test_confirmation_rechecks_real_cohort_overlap() -> None:
    with SessionLocal() as db:
        product = Product(
            id="00000000-0000-0000-0000-00000000e1c3",
            client_id=CLIENT_ID,
            code="OVERLAP-CONFIRM-QA",
            display_name="Overlap confirmation QA",
        )
        db.add(product)
        db.flush()
        first = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=1,
            display_name="First cohort",
            raw_description="First cohort",
            matching_rule={"=": ["employment_type", "MANUAL"]},
            status=CategoryStatus.needs_review.value,
            source="manual",
            plan_assignments={"plan_code": "A"},
        )
        second = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=2,
            display_name="Second cohort",
            raw_description="Second cohort",
            matching_rule={"=": ["employment_type", "MANUAL"]},
            status=CategoryStatus.needs_review.value,
            source="manual",
            plan_assignments={"plan_code": "B"},
        )
        db.add_all([first, second])
        db.add(
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-CONFIRM-OVERLAP",
                employee_name="Overlap Employee",
                attribute_values={"employment_type": "MANUAL"},
                derived_attribute_values={},
            )
        )
        db.flush()

        overlaps = current_category_overlaps(
            db, policy_year_id=PY_2026, client_id=CLIENT_ID
        )
        assert any(
            employee["staff_id"] == "E-CONFIRM-OVERLAP"
            and "Second cohort" in employee["other_categories"]
            for employee in overlaps[first.id]
        )
        confirm_category_mapping(db, category=first, client_id=CLIENT_ID)
        batch = CategoryConfirmationBatch(
            db, policy_year_id=PY_2026, client_id=CLIENT_ID, candidates=[second]
        )
        assert batch.assessment(second)[1] > 0
        with pytest.raises(
            ValueError,
            match=r"\d+ employees? match(?:es)? equally specific employee categories",
        ):
            confirm_category_mapping(db, category=second, client_id=CLIENT_ID)
        assert second.status == CategoryStatus.needs_review.value
        db.rollback()


def test_category_overlap_endpoint_identifies_employee(client: TestClient) -> None:
    product_id = "00000000-0000-0000-0000-00000000e1c4"
    with SessionLocal() as db:
        product = Product(
            id=product_id,
            client_id=CLIENT_ID,
            code="OVERLAP-DETAILS-QA",
            display_name="Overlap details QA",
        )
        first = Category(
            policy_year_id=PY_2026,
            product_id=product_id,
            priority=1,
            display_name="Details first cohort",
            raw_description="Details first cohort",
            matching_rule={"=": ["job_category", "OVERLAP-QA-CODE"]},
            status=CategoryStatus.needs_review.value,
            source="manual",
        )
        second = Category(
            policy_year_id=PY_2026,
            product_id=product_id,
            priority=2,
            display_name="Details second cohort",
            raw_description="Details second cohort",
            matching_rule={"=": ["job_category", "OVERLAP-QA-CODE"]},
            status=CategoryStatus.needs_review.value,
            source="manual",
        )
        employee = Employee(
            client_id=CLIENT_ID,
            policy_year_id=PY_2026,
            staff_id="E-OVERLAP-DETAILS",
            employee_name="Overlap Details Employee",
            attribute_values={"job_category": "OVERLAP-QA-CODE"},
            derived_attribute_values={},
        )
        db.add(product)
        db.flush()
        db.add_all([first, second, employee])
        db.commit()
        first_id, second_id, employee_id = first.id, second.id, employee.id

    try:
        response = client.get(
            f"/api/v1/categories/overlaps?policy_year_id={PY_2026}"
        )
        assert response.status_code == 200
        items = {item["category_id"]: item["employees"] for item in response.json()}
        assert items[first_id] == [
            {
                "employee_id": employee_id,
                "staff_id": "E-OVERLAP-DETAILS",
                "employee_name": "Overlap Details Employee",
                "job_category": "OVERLAP-QA-CODE",
                "other_categories": ["Details second cohort"],
            }
        ]
        assert items[second_id][0]["other_categories"] == ["Details first cohort"]
    finally:
        with SessionLocal() as db:
            for category_id in (first_id, second_id):
                if category := db.get(Category, category_id):
                    db.delete(category)
            if stored_employee := db.get(Employee, employee_id):
                db.delete(stored_employee)
            if stored_product := db.get(Product, product_id):
                db.delete(stored_product)
            db.commit()


def test_disjoint_plan_rules_do_not_report_an_overlap() -> None:
    with SessionLocal() as db:
        product = Product(
            id="00000000-0000-0000-0000-00000000e1c5",
            client_id=CLIENT_ID,
            code="DISJOINT-PLANS-QA",
            display_name="Disjoint plans QA",
        )
        db.add(product)
        db.flush()
        categories = [
            Category(
                policy_year_id=PY_2026,
                product_id=product.id,
                priority=priority,
                display_name=name,
                raw_description=name,
                matching_rule={"in": ["job_category", codes]},
                status=CategoryStatus.needs_review.value,
                source="manual",
                plan_assignments={"plan_code": plan},
            )
            for priority, name, codes, plan in [
                (1, "Manager cohort", ["E1", "E2"], "A"),
                (2, "Manager cohort", ["E1", "E2"], "B"),
                (3, "Thailand cohort", ["J1", "J2"], "A"),
                (4, "Thailand cohort", ["J1", "J2"], "B"),
            ]
        ]
        db.add_all(categories)
        db.add(
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2026,
                staff_id="E-DISJOINT-QA",
                employee_name="Disjoint QA Employee",
                attribute_values={"job_category": "J1"},
                derived_attribute_values={},
            )
        )
        db.flush()

        overlaps = current_category_overlaps(
            db, policy_year_id=PY_2026, client_id=CLIENT_ID
        )
        assert all(category.id not in overlaps for category in categories)

        # A hidden plan row with a broader rule is the only way these cohorts
        # can overlap for a single-valued job category.
        categories[1].matching_rule = {"in": ["job_category", ["E1", "E2", "J1"]]}
        overlaps = current_category_overlaps(
            db, policy_year_id=PY_2026, client_id=CLIENT_ID
        )
        assert categories[0].id not in overlaps
        assert categories[1].id in overlaps
        assert categories[2].id in overlaps
        assert categories[3].id in overlaps

        categories[1].rule_validation = {
            "errors": ["Matching rule omitted explicit employee attribute: job_category"]
        }
        overlaps = current_category_overlaps(
            db, policy_year_id=PY_2026, client_id=CLIENT_ID
        )
        assert all(category.id not in overlaps for category in categories)

        _, employees, views = build_attribute_catalog(db, PY_2026, CLIENT_ID)
        _, reassessed_overlaps = _assignment_counts(
            db=db,
            client_id=CLIENT_ID,
            categories=categories,
            employees=employees,
            views=views,
            validated_category_ids={categories[1].id},
        )
        assert reassessed_overlaps[categories[1].id] == 1
        db.rollback()


def test_plan_tier_siblings_share_one_validated_cohort_count() -> None:
    with SessionLocal() as db:
        product = Product(
            id="00000000-0000-0000-0000-00000000e1c1",
            client_id=CLIENT_ID,
            code="COHORT-QA",
            display_name="Cohort QA",
        )
        db.add(product)
        db.flush()
        first = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=201,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "A", "num_employees": 1},
        )
        second = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=202,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "B", "num_employees": 1},
        )
        db.add_all([first, second])
        db.flush()
        manual_employees = [
            employee
            for employee in db.execute(
                select(Employee).where(Employee.policy_year_id == PY_2026)
            ).scalars()
            if employee.attribute_values.get("employment_type") == "MANUAL"
        ]
        if not manual_employees:
            db.add(
                Employee(
                    client_id=CLIENT_ID,
                    policy_year_id=PY_2026,
                    staff_id="E-COHORT-QA",
                    employee_name="Cohort QA Employee",
                    attribute_values={"employment_type": "MANUAL"},
                    derived_attribute_values={},
                )
            )
            db.flush()

        summary = auto_map_policy_year(db, policy_year_id=PY_2026, client_id=CLIENT_ID)
        items = {
            item.category_id: item
            for item in summary.categories
            if item.category_id in {first.id, second.id}
        }

        assert items[first.id].rule_status == "validated"
        assert items[second.id].rule_status == "validated"
        assert items[first.id].matched_count == 1
        assert items[second.id].matched_count == 1
        assert all("equally specific" not in " ".join(item.warnings) for item in items.values())
        with patch(
            "app.services.eligibility_mapping.build_attribute_catalog",
            wraps=build_attribute_catalog,
        ) as catalog_builder:
            batch = CategoryConfirmationBatch(
                db,
                policy_year_id=PY_2026,
                client_id=CLIENT_ID,
                candidates=[first, second],
            )
            confirm_category_mapping(db, category=first, client_id=CLIENT_ID, batch=batch)
            confirm_category_mapping(db, category=second, client_id=CLIENT_ID, batch=batch)
            assert catalog_builder.call_count == 1
        assert first.rule_validation["overlap_count"] == 0
        assert second.rule_validation["overlap_count"] == 0
        db.rollback()


def test_slip_headcount_drift_is_advisory_not_a_rule_failure() -> None:
    with SessionLocal() as db:
        product = Product(
            id="00000000-0000-0000-0000-00000000e1c2",
            client_id=CLIENT_ID,
            code="DRIFT-QA",
            display_name="Drift QA",
        )
        db.add(product)
        db.flush()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=203,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "1", "num_employees": 999},
        )
        db.add(category)
        db.flush()

        summary = auto_map_policy_year(db, policy_year_id=PY_2026, client_id=CLIENT_ID)
        item = next(row for row in summary.categories if row.category_id == category.id)

        assert item.rule_status == "validated"
        assert item.matched_count == 1
        assert item.expected_count == 999
        assert "placement slip states 999" in " ".join(item.warnings)


def test_confirmed_company_mapping_is_reused_without_a_new_roster() -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        signature = category_signature("MANUAL EMPLOYEES")
        profile = db.execute(
            select(EligibilityMappingProfile).where(
                EligibilityMappingProfile.client_id == CLIENT_ID,
                EligibilityMappingProfile.category_signature == signature,
            )
        ).scalar_one_or_none()
        if profile is None:
            db.add(
                EligibilityMappingProfile(
                    client_id=CLIENT_ID,
                    category_signature=signature,
                    display_name="MANUAL EMPLOYEES",
                    matching_rule={"=": ["employment_type", "MANUAL"]},
                    rule_human_readable="employment_type is MANUAL",
                    required_attributes=["employment_type"],
                    validation={"state": "validated"},
                    source="manual",
                    confidence=0.95,
                    status="confirmed",
                    last_policy_year_id=PY_2026,
                )
            )
        category = Category(
            policy_year_id=PY_2027,
            product_id=product.id,
            priority=1,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "1"},
        )
        db.add(category)
        db.flush()

        summary = auto_map_policy_year(db, policy_year_id=PY_2027, client_id=CLIENT_ID)
        item = next(row for row in summary.categories if row.category_id == category.id)

        assert category.matching_rule == {"=": ["employment_type", "MANUAL"]}
        assert item.reused is True
        assert item.source == "prior_mapping"
        assert summary.reused >= 1
        assert category.mapping_profile_id is not None


def test_confirmed_null_rule_is_returned_to_review() -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "GBT")).scalar_one()
        category = Category(
            policy_year_id=PY_2027,
            product_id=product.id,
            priority=99,
            display_name="Authorised employees on regional business travel",
            raw_description="Authorised employees on regional business travel",
            matching_rule=None,
            status=CategoryStatus.confirmed.value,
            source="manual",
            human_modified=True,
        )
        db.add(category)
        db.flush()

        auto_map_policy_year(db, policy_year_id=PY_2027, client_id=CLIENT_ID)
        assert category.rule_status == "unmapped"
        assert category.status == CategoryStatus.needs_review.value
        assert category.rule_validation is not None
        assert category.rule_validation["unresolved_clauses"]


def test_mapping_profile_unique_per_company_signature() -> None:
    with SessionLocal() as db:
        signature = "unique constraint probe"
        db.add(
            EligibilityMappingProfile(
                client_id=CLIENT_ID,
                category_signature=signature,
                display_name="Constraint probe",
                matching_rule={"and": []},
            )
        )
        db.flush()
        db.add(
            EligibilityMappingProfile(
                client_id=CLIENT_ID,
                category_signature=signature,
                display_name="Duplicate constraint probe",
                matching_rule={"and": []},
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()


def test_confirm_endpoint_rejects_null_rule(client: TestClient) -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "GBT")).scalar_one()
        category = Category(
            policy_year_id=PY_2027,
            product_id=product.id,
            priority=101,
            display_name="Regional authorised travellers",
            raw_description="Regional authorised travellers",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
        )
        db.add(category)
        db.commit()
        category_id = category.id

    response = client.post(f"/api/v1/categories/{category_id}/confirm")
    assert response.status_code == 422
    assert "Matching rule is required" in response.json()["detail"]


def test_mapping_proposal_endpoint_returns_review_matrix(client: TestClient) -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "GBT")).scalar_one()
        db.add(
            Category(
                policy_year_id=PY_2027,
                product_id=product.id,
                priority=102,
                display_name="Endpoint-specific unmapped cohort",
                raw_description="Endpoint-specific unmapped cohort",
                matching_rule=None,
                status=CategoryStatus.needs_review.value,
                source="system_generated",
            )
        )
        db.commit()

    response = client.post(f"/api/v1/policy-years/{PY_2027}/eligibility-mappings/propose")
    assert response.status_code == 200
    payload = response.json()
    assert payload["policy_year_id"] == PY_2027
    assert payload["total"] >= 1
    assert payload["unmapped"] >= 1
    assert all("rule_status" in row for row in payload["categories"])

    stored = client.get(f"/api/v1/policy-years/{PY_2027}/eligibility-mappings")
    assert stored.status_code == 200
    assert stored.json()["total"] == payload["total"]


def _ai_result(rule: dict | None, *, unresolved: list[str] | None = None) -> AICallResult:
    return AICallResult(
        envelope=RuleEnvelope(
            rule=rule,
            human_readable="AI test reading",
            confidence=0.8,
            needs_review=True,
        ),
        metadata={
            "provider": "vertex",
            "model": "gemini-test",
            "reasoning": "Mapped only to supplied company values.",
            "unresolved_clauses": unresolved or [],
            "prompt_version": "rule_generation/v2",
        },
        cache_hit=False,
    )


def test_ai_context_excludes_pii_and_contains_company_vocabulary() -> None:
    with SessionLocal() as db:
        pii = EmployeeAttributeSchema(
            client_id=CLIENT_ID,
            attribute_id="secret_note",
            display_name="Secret Note",
            data_type="string",
            is_required=False,
            is_pii=True,
        )
        employee = Employee(
            client_id=CLIENT_ID,
            policy_year_id=PY_2026,
            staff_id="DO-NOT-SEND-STAFF-ID",
            employee_name="Do Not Send Employee Name",
            attribute_values={
                "employment_type": "MANUAL",
                "secret_note": "DO-NOT-SEND-SECRET",
            },
            derived_attribute_values={},
        )
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=500,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "1"},
        )
        db.add_all([pii, employee, category])
        db.flush()

        schemas, context, _ = build_ai_eligibility_inputs(
            db, category=category, client_id=CLIENT_ID
        )

        serialized = json.dumps(context)
        assert "employment_type" in serialized
        assert "MANUAL" in serialized
        employment_schema = next(
            schema for schema in schemas if schema.attribute_id == "employment_type"
        )
        assert employment_schema.enum_values == ["MANUAL"]
        employment_context = next(
            item
            for item in context["employee_attributes"]
            if item["attribute_id"] == "employment_type"
        )
        assert employment_context["configured_values"] == []
        assert all(schema.attribute_id != "secret_note" for schema in schemas)
        assert "secret_note" not in serialized
        assert "DO-NOT-SEND-SECRET" not in serialized
        assert "DO-NOT-SEND-STAFF-ID" not in serialized
        assert "Do Not Send Employee Name" not in serialized
        db.rollback()


def test_nationality_is_internal_matchable_but_never_ai_context() -> None:
    """PII classification must not make deterministic Thailand rules inert."""
    with SessionLocal() as db:
        employee = Employee(
            client_id=CLIENT_ID,
            policy_year_id=PY_2026,
            staff_id="NATIONALITY-INTERNAL",
            employee_name="Internal Only",
            attribute_values={"nationality": "Thai"},
            derived_attribute_values={},
        )
        db.add(employee)
        db.commit()
        try:
            catalog, _, _ = build_attribute_catalog(db, PY_2026, CLIENT_ID)
            assert "Thai" in catalog.values["nationality"]

            product = db.execute(
                select(Product).where(Product.code == "WICA")
            ).scalar_one()
            category = Category(
                policy_year_id=PY_2026,
                product_id=product.id,
                priority=900,
                display_name="Thailand employees",
                raw_description="Employees based in Thailand",
                plan_assignments={"plan_code": "PLAN-TH"},
            )
            db.add(category)
            db.flush()
            schemas, context, _ = build_ai_eligibility_inputs(
                db, category=category, client_id=CLIENT_ID
            )
            assert "nationality" not in {schema.attribute_id for schema in schemas}
            serialized = json.dumps(context)
            assert '"nationality"' not in serialized
            assert '"Thai"' not in serialized
        finally:
            db.rollback()
            stored_employee = db.get(Employee, employee.id)
            if stored_employee is not None:
                db.delete(stored_employee)
            db.commit()


def test_ai_context_excludes_empty_configured_field_when_listing_exists() -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=503,
            display_name="All Others",
            raw_description="All Others",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "EMPTY-FIELD"},
        )
        db.add(category)
        db.flush()

        schemas, context, _ = build_ai_eligibility_inputs(
            db, category=category, client_id=CLIENT_ID
        )

        schema_ids = {schema.attribute_id for schema in schemas}
        context_ids = {item["attribute_id"] for item in context["employee_attributes"]}
        assert "occupation" not in schema_ids
        assert "occupation" not in context_ids
        assert context["employee_listing_available"] is True
        assert context["deterministic_candidate"]["rule"] is None
        db.rollback()


def test_ai_context_keeps_configured_field_without_employee_listing() -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2027,
            product_id=product.id,
            priority=504,
            display_name="Manual Employees",
            raw_description="Manual Employees",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "NO-LISTING"},
        )
        db.add(category)
        db.flush()

        schemas, context, _ = build_ai_eligibility_inputs(
            db, category=category, client_id=CLIENT_ID
        )

        assert "employment_type" in {schema.attribute_id for schema in schemas}
        assert context["employee_listing_available"] is False
        assert context["employee_count"] == 0
        db.rollback()


def test_ai_suggest_rejects_invalid_rule_without_overwriting_category(
    client: TestClient,
) -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=501,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule={"=": ["employment_type", "MANUAL"]},
            rule_human_readable="Original safe rule",
            status=CategoryStatus.needs_review.value,
            source="manual",
            human_modified=True,
            plan_assignments={"plan_code": "1"},
        )
        db.add(category)
        db.commit()
        category_id = category.id

    with patch(
        "app.api.v1.categories.generate_rule_for_category",
        return_value=_ai_result({"=": ["employment_type", "CHIEF WIZARD"]}),
    ):
        response = client.post(f"/api/v1/categories/{category_id}/ai-suggest")

    assert response.status_code == 422
    assert "No safe suggestion available" in response.json()["detail"]
    with SessionLocal() as db:
        stored = db.get(Category, category_id)
        assert stored is not None
        assert stored.matching_rule == {"=": ["employment_type", "MANUAL"]}
        assert stored.rule_human_readable == "Original safe rule"
        assert stored.source == "manual"


def test_ai_suggest_receives_company_context_and_persists_validation(
    client: TestClient,
) -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        category = Category(
            policy_year_id=PY_2026,
            product_id=product.id,
            priority=502,
            display_name="MANUAL EMPLOYEES",
            raw_description="MANUAL EMPLOYEES",
            matching_rule=None,
            status=CategoryStatus.needs_review.value,
            source="system_generated",
            plan_assignments={"plan_code": "AI-CONTEXT"},
        )
        db.add(category)
        db.commit()
        category_id = category.id

    captured: dict = {}

    def fake_generate(*args, **kwargs):
        captured.update(kwargs.get("context") or {})
        return _ai_result({"=": ["employment_type", "MANUAL"]})

    proxy_warning = (
        "based in Thailand mapped through nationality; "
        "confirm nationality represents work base"
    )
    with patch(
        "app.api.v1.categories.generate_rule_for_category",
        side_effect=fake_generate,
    ), patch(
        "app.api.v1.categories.normalize_ai_matching_rule",
        return_value=({"=": ["employment_type", "MANUAL"]}, [proxy_warning]),
    ):
        response = client.post(f"/api/v1/categories/{category_id}/ai-suggest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "ai_extracted"
    assert payload["rule_validation"]["ai_prompt_version"] == "rule_generation/v2"
    assert payload["rule_validation"]["unresolved_clauses"] == [proxy_warning]
    assert captured["employee_attributes"]
    assert any(
        item["attribute_id"] == "employment_type" and "MANUAL" in item["observed_values"]
        for item in captured["employee_attributes"]
    )


def test_ai_routes_accept_product_scoped_location_exclusion(client: TestClient) -> None:
    location = "All Employees based in Thailand"
    description = "Senior (Job Category: A1)"
    rule = {"and": [
        {"=": ["job_category", "A1"]},
        {"not": {"in": ["category", [location]]}},
    ]}
    with SessionLocal() as db:
        product = Product(
            client_id=CLIENT_ID,
            code="AI-LOCATION-GUARD-QA",
            display_name="AI location guard QA",
        )
        db.add(product)
        db.flush()
        location_category = Category(
            policy_year_id=PY_2027,
            product_id=product.id,
            display_name=location,
            raw_description=location,
            status=CategoryStatus.needs_review.value,
            plan_assignments={"plan_code": "LOCATION"},
        )
        grade_category = Category(
            policy_year_id=PY_2027,
            product_id=product.id,
            display_name="Senior",
            raw_description=description,
            status=CategoryStatus.needs_review.value,
            plan_assignments={"plan_code": "SUGGEST"},
        )
        plan = Plan(
            product_id=product.id,
            policy_year_id=PY_2027,
            code="CREATE",
            display_name="AI create plan",
            status="needs_review",
        )
        db.add_all([location_category, grade_category, plan])
        db.add_all([
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2027,
                staff_id="E-AI-LOCATION-GRADE",
                attribute_values={"job_category": "A1", "employment_type": "MANUAL"},
            ),
            Employee(
                client_id=CLIENT_ID,
                policy_year_id=PY_2027,
                staff_id="E-AI-LOCATION-THAI",
                attribute_values={
                    "job_category": "A1", "category": location,
                    "employment_type": "MANUAL",
                },
            ),
        ])
        db.commit()
        grade_id = grade_category.id
        plan_id = plan.id

    with patch(
        "app.api.v1.categories.generate_rule_for_category",
        return_value=_ai_result(rule),
    ), patch(
        "app.api.v1.categories.normalize_ai_matching_rule",
        return_value=(rule, []),
    ):
        suggested = client.post(f"/api/v1/categories/{grade_id}/ai-suggest")

    assert suggested.status_code == 200
    assert suggested.json()["matching_rule"] == rule

    with patch(
        "app.api.v1.eligibility_mappings.generate_rule_for_category",
        return_value=_ai_result(rule),
    ), patch(
        "app.api.v1.eligibility_mappings.normalize_ai_matching_rule",
        return_value=(rule, []),
    ):
        created = client.post(
            f"/api/v1/policy-years/{PY_2027}/eligibility-mappings/ai-create-category",
            json={"plan_id": plan_id, "eligibility_description": description},
        )

    assert created.status_code == 201, created.text
    assert created.json()["matching_rule"] == rule


def test_missing_plan_is_detected_and_ai_category_creation_is_guided(
    client: TestClient,
) -> None:
    with SessionLocal() as db:
        product = db.execute(select(Product).where(Product.code == "WICA")).scalar_one()
        plan = Plan(
            product_id=product.id,
            policy_year_id=PY_2027,
            code="AI-MISSING",
            display_name="AI Missing Category Plan",
            cover_description="Benefit schedule, not eligibility authority",
            status="needs_review",
        )
        db.add(plan)
        db.commit()
        plan_id = plan.id

    summary = client.get(f"/api/v1/policy-years/{PY_2027}/eligibility-mappings")
    assert summary.status_code == 200
    assert any(item["plan_id"] == plan_id for item in summary.json()["missing_category_plans"])

    proxy_warning = (
        "based in Thailand mapped through nationality; "
        "confirm nationality represents work base"
    )
    with patch(
        "app.api.v1.eligibility_mappings.generate_rule_for_category",
        return_value=_ai_result({"=": ["employment_type", "MANUAL"]}),
    ), patch(
        "app.api.v1.eligibility_mappings.normalize_ai_matching_rule",
        return_value=({"=": ["employment_type", "MANUAL"]}, [proxy_warning]),
    ):
        created = client.post(
            f"/api/v1/policy-years/{PY_2027}/eligibility-mappings/ai-create-category",
            json={
                "plan_id": plan_id,
                "eligibility_description": "MANUAL EMPLOYEES",
                "display_name": "Manual employees",
                "participation_model": "compulsory",
            },
        )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["plan_assignments"]["plan_code"] == "AI-MISSING"
    assert body["matching_rule"] == {"=": ["employment_type", "MANUAL"]}
    assert body["status"] == "needs_review"
    assert body["rule_validation"]["created_for_missing_plan"] is True
    assert body["rule_validation"]["unresolved_clauses"] == [proxy_warning]

    duplicate = client.post(
        f"/api/v1/policy-years/{PY_2027}/eligibility-mappings/ai-create-category",
        json={
            "plan_id": plan_id,
            "eligibility_description": "MANUAL EMPLOYEES",
        },
    )
    assert duplicate.status_code == 409
