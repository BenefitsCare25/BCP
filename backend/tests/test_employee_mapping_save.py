"""Employee mapping saves are persistent, scope-safe and protected against stale edits."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.auth import CurrentUser, get_current_user
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import BrokerFirm, Category, Client, Employee, PolicyYear, Product


@pytest.fixture
def mapping_api():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(BrokerFirm(id="firm", name="Review firm"))
    db.flush()
    db.add(Client(id="client", name="Review company", broker_firm_id="firm"))
    db.flush()
    db.add(PolicyYear(id="year", client_id="client", year=2032,
                      start_date=date(2032, 1, 1), end_date=date(2032, 12, 31)))
    for code in ("MED", "LIFE", "DEP"):
        db.add(Product(id=code, client_id="client", code=code, display_name=code))
    db.flush()
    for cid, product, scope in (("med", "MED", "employee"), ("med-other", "MED", "employee"),
                                ("life", "LIFE", "employee"), ("spouse", "LIFE", "dependant"),
                                ("dep-only", "DEP", "dependant")):
        db.add(Category(id=cid, policy_year_id="year", product_id=product,
                        display_name=cid, raw_description=cid,
                        plan_assignments={"member_scope": scope}, status="confirmed"))
    db.flush()
    db.add(Employee(id="employee", client_id="client", policy_year_id="year", staff_id="R-1",
                    employee_name="Review employee", attribute_values={"category": "Executives"},
                    derived_attribute_values={}, matched_category_id="med",
                    matched_categories=[{"category_id": "med", "product_code": "MED"}],
                    source="manual", status="active"))
    db.commit()
    actor = CurrentUser(user_id="review", broker_firm_id="firm", client_id="client",
                        role="broker_admin")
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        with TestClient(app) as client:
            yield client, db
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        db.close()
        engine.dispose()


def test_mapping_save_refresh_and_stale_rejection(mapping_api):
    client, db = mapping_api
    employee = client.get("/api/v1/employees/employee").json()
    assert employee["unmatched_product_codes"] == ["LIFE"]
    response = client.post("/api/v1/match-results/employees/employee/override", json={
        "category_ids": ["med", "life"], "expected_updated_at": employee["updated_at"],
    })
    assert response.status_code == 200, response.text
    saved = client.get("/api/v1/employees/employee").json()
    assert saved["unmatched_product_codes"] == []
    assert {p["category_id"] for p in saved["matched_plans"]} == {"med", "life"}
    assert saved["updated_at"] == response.json()["updated_at"]
    stale = client.post("/api/v1/match-results/employees/employee/override", json={
        "category_ids": ["med-other", "life"], "expected_updated_at": employee["updated_at"],
    })
    assert stale.status_code == 409
    assert {m["category_id"] for m in db.get(Employee, "employee").matched_categories} == {
        "med", "life"
    }


@pytest.mark.parametrize("payload", [{"category_ids": ["spouse"]}, {"category_id": "spouse"},
                                    {"category_ids": ["med", "med-other"]}])
def test_invalid_mapping_keeps_existing_assignment(mapping_api, payload):
    client, db = mapping_api
    response = client.post("/api/v1/match-results/employees/employee/override", json=payload)
    assert response.status_code == 422
    assert db.get(Employee, "employee").matched_category_id == "med"


@pytest.mark.parametrize("payload", [{"category_ids": []}, {"category_id": None},
                                    {"category_ids": ["life"]}])
def test_broker_cannot_remove_saved_product_mapping(mapping_api, payload):
    client, db = mapping_api
    assert client.post("/api/v1/match-results/employees/employee/override",
                       json=payload).status_code == 403
    assert db.get(Employee, "employee").matched_category_id == "med"
