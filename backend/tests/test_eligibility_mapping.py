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
    auto_map_policy_year,
    build_ai_eligibility_inputs,
    category_signature,
    confirm_category_mapping,
    propose_category_rule,
    validate_ai_matching_rule,
    validate_matching_rule,
)
from scripts.seed_demo import seed

CLIENT_ID = "00000000-0000-0000-0000-00000000e101"
PY_2026 = "00000000-0000-0000-0000-00000000e126"
PY_2027 = "00000000-0000-0000-0000-00000000e127"


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


def test_based_in_country_does_not_fall_back_to_nationality() -> None:
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
        "in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]
    }
    assert "based in Thailand" in proposal.unresolved_clauses


def test_location_only_category_never_uses_nationality() -> None:
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
    assert unresolved.rule is None
    assert unresolved.unresolved_clauses == ["based in Thailand"]


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
    assert empty.errors == [
        "Employee attribute pass has no values in the employee listing"
    ]
    assert valid.valid is True
    assert valid.errors == []


def test_matching_rule_validation_rejects_hallucinated_company_values() -> None:
    """An AI rule may only use a value known to this company.

    Attribute-only validation used to accept a plausible-looking but invented
    job grade, which then matched nobody after it had already been persisted.
    """

    catalog = _catalog(designation=["Manager", "Executive"])

    result = validate_matching_rule(
        {"in": ["designation", ["Manager", "Chief Wizard"]]}, catalog
    )

    assert result.valid is False
    assert result.errors == [
        "Unknown company value for designation: Chief Wizard"
    ]


def test_ai_rule_cannot_silently_expand_specific_wording_to_everyone() -> None:
    catalog = _catalog(designation=["Manager", "Executive"])

    specific = validate_ai_matching_rule(
        "Managers only", {"and": []}, catalog
    )
    explicit = validate_ai_matching_rule(
        "All employees", {"and": []}, catalog
    )

    assert specific.valid is False
    assert "only when the eligibility wording says so" in specific.errors[0]
    assert explicit.valid is True


def test_matching_rule_validation_bounds_nesting_depth() -> None:
    rule: dict = {"=": ["designation", "Manager"]}
    for _ in range(20):
        rule = {"not": rule}

    result = validate_matching_rule(
        rule, _catalog(designation=["Manager", "Executive"])
    )

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

        profile = confirm_category_mapping(db, category=category, client_id=CLIENT_ID)
        assert profile.status == "confirmed"
        assert category.status == CategoryStatus.confirmed.value
        db.commit()


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
        context_ids = {
            item["attribute_id"] for item in context["employee_attributes"]
        }
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

    with patch(
        "app.api.v1.categories.generate_rule_for_category",
        side_effect=fake_generate,
    ):
        response = client.post(f"/api/v1/categories/{category_id}/ai-suggest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "ai_extracted"
    assert payload["rule_validation"]["ai_prompt_version"] == "rule_generation/v2"
    assert captured["employee_attributes"]
    assert any(
        item["attribute_id"] == "employment_type"
        and "MANUAL" in item["observed_values"]
        for item in captured["employee_attributes"]
    )


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
    assert any(
        item["plan_id"] == plan_id
        for item in summary.json()["missing_category_plans"]
    )

    with patch(
        "app.api.v1.eligibility_mappings.generate_rule_for_category",
        return_value=_ai_result({"=": ["employment_type", "MANUAL"]}),
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

    assert created.status_code == 201
    body = created.json()
    assert body["plan_assignments"]["plan_code"] == "AI-MISSING"
    assert body["matching_rule"] == {"=": ["employment_type", "MANUAL"]}
    assert body["status"] == "needs_review"
    assert body["rule_validation"]["created_for_missing_plan"] is True

    duplicate = client.post(
        f"/api/v1/policy-years/{PY_2027}/eligibility-mappings/ai-create-category",
        json={
            "plan_id": plan_id,
            "eligibility_description": "MANUAL EMPLOYEES",
        },
    )
    assert duplicate.status_code == 409
