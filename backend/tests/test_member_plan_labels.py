"""Display labels must never change coverage/election identity or stored data."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.v1.portal_claims import build_coverage_options
from app.db.base import Base
from app.models import Category, Client, Employee, Plan, PolicyYear, Product, RosterMappingProfile
from app.models.client import BrokerFirm
from app.models.employee_plan_override import EmployeePlanOverride
from app.schemas.enrollment import CohortTierOut, EnrollmentOptionsOut, ProductTierSetOut
from app.services.enrollment_elections import member_labelled_options
from app.services.enrollment_forms.pricing import plan_facts
from app.services.insurer_listings import member_id_for_insurer
from app.services.member_statement import build_member_statement, member_visible_statement
from app.services.plan_labels import member_plan_label
from app.services.roster_mapping import employee_roster_fields


@pytest.fixture
def world():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        firm = BrokerFirm(name="Synthetic label checks")
        db.add(firm)
        db.flush()
        company = Client(name="Synthetic label company", broker_firm_id=firm.id)
        db.add(company)
        db.flush()
        year = PolicyYear(
            client_id=company.id,
            year=2026,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        product = Product(client_id=company.id, code="GCGP", display_name="Group GP")
        db.add_all([year, product])
        db.flush()
        plan = Plan(
            product_id=product.id,
            policy_year_id=year.id,
            code="1",
            display_name="Plan 1",
            report_label="Panel only",
            status="confirmed",
        )
        alternate = Plan(
            product_id=product.id,
            policy_year_id=year.id,
            code="2",
            display_name="Plan 2",
            report_label="Panel and non-panel",
            status="confirmed",
        )
        category = Category(
            policy_year_id=year.id,
            product_id=product.id,
            display_name="All employees",
            raw_description="All employees",
            plan_assignments={"plan_code": "1"},
        )
        db.add_all([plan, alternate, category])
        db.flush()
        employee = Employee(
            client_id=company.id,
            policy_year_id=year.id,
            staff_id="SYNTHETIC",
            attribute_values={},
            derived_attribute_values={},
            matched_categories=[{"category_id": category.id, "product_code": product.code}],
        )
        db.add(employee)
        db.flush()
        yield db, employee, plan, alternate
    engine.dispose()


@pytest.mark.parametrize(
    "report_label,expected",
    [
        ("Panel only", "Panel only"),
        ("  Panel only  ", "Panel only"),
        (None, "Plan 1"),
        ("", "Plan 1"),
        ("  ", "Plan 1"),
    ],
)
def test_label_falls_back_without_mutating_plan(report_label, expected):
    plan = Plan(code="1", display_name="Plan 1", report_label=report_label)
    assert member_plan_label(plan) == expected
    assert plan.display_name == "Plan 1" and plan.report_label == report_label


def test_member_statement_tracks_labels_and_elected_plan(world):
    db, employee, plan, alternate = world
    line = member_visible_statement(build_member_statement(db, employee)).coverage[0]
    assert (line.plan_code, line.plan_display_name) == ("1", "Panel only")
    year = db.get(PolicyYear, employee.policy_year_id)
    claim_options = build_coverage_options(db, build_member_statement(db, employee), employee, year)
    assert claim_options.insured[0].plan_display_name == "Panel only"
    assert line.financials is None and line.match_method is None
    plan.report_label = " "
    db.flush()
    assert build_member_statement(db, employee).coverage[0].plan_display_name == "Plan 1"
    db.add(
        EmployeePlanOverride(
            employee_id=employee.id,
            client_id=employee.client_id,
            policy_year_id=employee.policy_year_id,
            product_id=plan.product_id,
            product_code="GCGP",
            plan_code="2",
        )
    )
    db.flush()
    line = build_member_statement(db, employee).coverage[0]
    assert (line.plan_code, line.plan_display_name) == ("2", alternate.report_label)
    assert plan.code == "1" and plan.display_name == "Plan 1"


def tier(key, code, label):
    return CohortTierOut(
        key=key,
        tier_category_id=key,
        plan_code=code,
        label=label,
        participation="compulsory",
        direction="same",
        is_baseline=key == "base",
    )


def test_options_and_form_facts_use_labels_preserving_option_keys(world):
    db, employee, plan, _alternate = world
    options = EnrollmentOptionsOut(
        products=[
            ProductTierSetOut(
                product_id=plan.product_id,
                product_code="GCGP",
                employee_participation="compulsory",
                dependant_participation=None,
                baseline_tier_category_id="base",
                baseline_plan_code="1",
                allow_plan_change=True,
                can_decline=False,
                tiers=[
                    tier("base", "1", "Plan 1"),
                    tier("upgrade", "2", "Plan 2"),
                    tier("missing", "99", "Plan 99"),
                ],
            )
        ]
    )
    updated = member_labelled_options(db, options, employee.policy_year_id)
    assert [t.label for t in updated.products[0].tiers] == [
        "Panel only",
        "Panel and non-panel",
        "Plan 99",
    ]
    assert [t.key for t in updated.products[0].tiers] == ["base", "upgrade", "missing"]
    assert [t.plan_code for t in updated.products[0].tiers] == ["1", "2", "99"]
    assert options.products[0].tiers[0].label == "Plan 1"
    facts = plan_facts(db, updated.products, employee.policy_year_id)
    assert facts[0].label == "Panel only"
    # A different year cannot supply a name for this year's choices.
    unchanged = member_labelled_options(db, options, "different-year")
    assert unchanged.products[0].tiers[0].label == "Plan 1"
    options.products[0].tiers = [tier("base", "1", "Option 1"), tier("other", "1", "Option 2")]
    updated = member_labelled_options(db, options, employee.policy_year_id)
    assert [t.label for t in updated.products[0].tiers] == [
        "Panel only · Option 1",
        "Panel only · Option 2",
    ]


def test_roster_insurer_keys_match_parser_for_empty_and_populated_ids(world):
    db, employee, _plan, _alternate = world
    employee.attribute_values = {"insurer_member_ids": {"Great Eastern": "ZU-9"}}
    db.add(
        RosterMappingProfile(
            client_id=employee.client_id,
            member_type="employee",
            fingerprint="synthetic-whitespace",
            source_headers=[
                "Great   Eastern Member ID",
                "Blank\tInsurer Member ID",
            ],
            column_mapping={"0": "insurer_member_ids", "1": "insurer_member_ids"},
        )
    )
    db.flush()
    fields = employee_roster_fields(db, employee)
    assert fields[0].object_keys == ["Great Eastern", "Blank Insurer"]
    assert member_id_for_insurer(employee.attribute_values, fields[0].object_keys[0]) == "ZU-9"
    employee.attribute_values = {"insurer_member_ids": {fields[0].object_keys[1]: "AB-123"}}
    assert member_id_for_insurer(employee.attribute_values, "Blank Insurer") == "AB-123"
