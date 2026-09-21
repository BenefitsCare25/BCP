"""Original product periods govern claims through renewals and grace expiry."""
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.portal_auth import CurrentMember, get_current_member
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    BrokerFirm,
    Claim,
    Client,
    Employee,
    MemberAccount,
    Plan,
    PolicyYear,
    Product,
    ProductTerm,
)
from app.models.policy_year import PolicyYearStatus
from app.schemas.api import BenefitStatementOut, CoverageLine, StatementEmployee
from app.services import claims


@pytest.fixture
def scope():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        db.add_all([Client(id=key, name=key, broker_firm_id="firm")
                    for key in ("company", "other")])
        db.flush()
        db.add_all([
            MemberAccount(id="member", client_id="company", staff_id="staff", status="active"),
            MemberAccount(id="other-member", client_id="other", staff_id="staff", status="active"),
        ])
        db.flush()
        for key, year, state, client in (
            ("old", 2025, PolicyYearStatus.archived, "company"),
            ("new", 2026, PolicyYearStatus.active, "company"),
            ("foreign", 2025, PolicyYearStatus.archived, "other"),
            ("draft", 2027, PolicyYearStatus.draft, "company"),
        ):
            db.add(PolicyYear(id=key, client_id=client, year=year,
                              start_date=date(year, 1, 1), end_date=date(year, 12, 31),
                              status=state, claim_grace_period_days=30))
            db.flush()
            db.add(Employee(id=key, client_id=client, policy_year_id=key,
                            member_account_id="member" if client == "company" else "other-member",
                            staff_id="staff", employee_name="Member", attribute_values={},
                            derived_attribute_values={}, source="csv_import", status="active"))
        db.add(Product(id="product", client_id="company", code="GHS", display_name="Hospital"))
        db.flush()
        db.add(Plan(product_id="product", policy_year_id="old", code="P1", display_name="Plan"))
        db.add(ProductTerm(product_id="product", policy_year_id="old",
                           coverage_start=date(2025, 4, 1), coverage_end=date(2026, 3, 31)))
        db.add(Claim(id="historic", client_id="company", policy_year_id="old", employee_id="old",
                     claim_kind="insured", product_code="GHS", claim_type="Inpatient",
                     incurred_date=date(2026, 2, 1), amount_claimed=25, status="needs_info"))
        db.commit()
        yield db
    engine.dispose()


def test_product_period_outlives_nominal_year_and_clamps_leaver(scope):
    year, employee, claim = (scope.get(model, key) for model, key in (
        (PolicyYear, "old"), (Employee, "old"), (Claim, "historic")))
    window = claims.assert_incurred_in_period(scope, year, claim, employee)
    assert (window.start, window.end) == (date(2025, 4, 1), date(2026, 3, 31))
    claim.incurred_date = date(2025, 3, 31)
    with pytest.raises(HTTPException, match="GHS coverage period"):
        claims.assert_incurred_in_period(scope, year, claim, employee)
    employee.status = "terminated"
    employee.terminated_effective = date(2025, 12, 1)
    window = claims.claim_period_window(scope, year, "insured", employee, "GHS")
    assert window.end == date(2025, 12, 1)
    assert window.period_end == date(2026, 3, 31)


def test_archived_grace_and_requested_reply(scope, monkeypatch):
    claim, employee = scope.get(Claim, "historic"), scope.get(Employee, "old")
    monkeypatch.setattr(claims, "validate_claim_facts", lambda *args, **kwargs: kwargs["window"])
    monkeypatch.setattr(claims, "apply_conversion", lambda *args: None)
    monkeypatch.setattr(claims, "assert_fx_acknowledged", lambda *args: None)
    monkeypatch.setattr(claims, "mint_reference_no", lambda *args: None)
    monkeypatch.setattr(claims, "business_today", lambda: date(2026, 4, 30))
    claim.status = "draft"
    claims.submit_claim(scope, claim, employee, submitted_by_member_id="member")
    assert claim.status == "submitted"
    claim.status = "draft"
    monkeypatch.setattr(claims, "business_today", lambda: date(2026, 5, 1))
    with pytest.raises(HTTPException, match="2026-04-30"):
        claims.submit_claim(scope, claim, employee, submitted_by_member_id="member")
    claim.status = "needs_info"
    claims.submit_claim(scope, claim, employee, submitted_by_member_id="member")
    assert claim.status == "submitted"


def test_portal_claim_period_selection_is_member_bound_and_draft_years_hidden(scope, monkeypatch):
    from app.services import utilization

    monkeypatch.setattr(utilization, "build_member_statement", lambda db, employee:
        BenefitStatementOut(
            employee=StatementEmployee(id=employee.id, staff_id="staff", employee_name="Member"),
            policy_year_id=employee.policy_year_id, is_matched=True,
            coverage=[CoverageLine(product_code="GHS", product_name="Hospital", plan_code="P1")],
        ))
    member = CurrentMember(member_account_id="member", client_id="company", broker_firm_id=None,
                           email="member@example.test", staff_id="staff")
    app.dependency_overrides[get_current_member] = lambda: member
    app.dependency_overrides[get_db] = lambda: scope
    try:
        with TestClient(app) as client:
            current = client.get("/api/v1/portal/claims")
            assert current.status_code == 200, current.text
            assert current.json()["total"] == 0
            headers = {"X-Inspro-Claims-Year-ID": "old"}
            historic = client.get("/api/v1/portal/claims", headers=headers)
            assert historic.status_code == 200, historic.text
            assert historic.json()["items"][0]["id"] == "historic"
            assert client.get("/api/v1/portal/claims/historic", headers=headers).status_code == 200
            old_usage = client.get("/api/v1/portal/claims/utilization", headers=headers)
            current_usage = client.get("/api/v1/portal/utilization", headers=headers)
            assert old_usage.status_code == current_usage.status_code == 200
            assert old_usage.json()["policy_year_id"] == "old"
            assert current_usage.json()["policy_year_id"] == "new"
            assert sum(row["pending"] for row in old_usage.json()["insured"]) == 25
            assert sum(row["pending"] for row in current_usage.json()["insured"]) == 0
            for year in ("foreign", "draft"):
                assert client.get("/api/v1/portal/claims",
                                  headers={"X-Inspro-Claims-Year-ID": year}).status_code == 404
            periods = client.get("/api/v1/portal/claim-periods")
            assert periods.status_code == 200, periods.text
            assert {row["id"] for row in periods.json()} == {"old", "new"}
    finally:
        app.dependency_overrides.pop(get_current_member, None)
        app.dependency_overrides.pop(get_db, None)


def test_product_switch_is_a_period_amendment():
    assert "product_code" in claims.PERIOD_FIELDS


def test_coverage_options_expose_product_window_and_hide_expired_filing(scope, monkeypatch):
    from app.api.v1 import portal_claims

    statement = BenefitStatementOut(
        employee=StatementEmployee(id="old", staff_id="staff", employee_name="Member"),
        policy_year_id="old", is_matched=True,
        coverage=[CoverageLine(product_code="GHS", product_name="Hospital", plan_code="P1")],
    )
    monkeypatch.setattr(portal_claims, "business_today", lambda: date(2026, 4, 30))
    options = portal_claims.build_coverage_options(
        scope, statement, scope.get(Employee, "old"), scope.get(PolicyYear, "old"),
    )
    assert options.insured[0].claimable_from == "2025-04-01"
    assert options.insured[0].claimable_to == "2026-03-31"
    assert options.insured[0].submission_deadline == "2026-04-30"
    monkeypatch.setattr(portal_claims, "business_today", lambda: date(2026, 5, 1))
    options = portal_claims.build_coverage_options(
        scope, statement, scope.get(Employee, "old"), scope.get(PolicyYear, "old"),
    )
    assert not options.insured
    assert "2026-04-30" in options.claim_block
