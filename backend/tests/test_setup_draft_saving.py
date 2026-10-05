"""Draft progress, live terms and confirmation share a safe transaction boundary."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1 import product_setups
from app.core.auth import CurrentUser, get_current_user
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import BrokerFirm, Client, Plan, PolicyYear, Product, ProductTerm
from app.services.product_templates import get_template

PATH = "/api/v1/policy-years/year/product-setups/GHS"


@pytest.fixture
def context(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        db.add(BrokerFirm(id="firm", name="Test firm"))
        db.flush()
        db.add(Client(id="client", name="Draft saving test", broker_firm_id="firm"))
        db.flush()
        db.add(PolicyYear(
            id="year", client_id="client", year=2030,
            start_date=date(2030, 1, 1), end_date=date(2030, 12, 31), status="draft",
        ))
        db.add(Product(id="product", client_id="client", code="GHS", display_name="Hospital",
                       product_metadata={"line": "medical"}))
        db.flush()
        db.add(ProductTerm(policy_year_id="year", product_id="product", pre_hosp_days=30))
        db.commit()
        tpl = get_template("GHS")
        assert tpl is not None
        monkeypatch.setattr(product_setups, "_resolve_template", lambda *a, **kw: tpl)

        def database():
            try:
                yield db
            finally:
                db.rollback()

        user = CurrentUser(user_id="operator", broker_firm_id="firm", client_id="client",
                           role="broker_admin")
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            with TestClient(app) as client:
                yield client, db, user
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
    engine.dispose()


def payload(**sections):
    return {"template_version": 1, "answers": {
        "header": {}, "eligibility": {}, "categories": [], "rate_table": {},
        "plans": [{"code": "1", "label": "Plan 1", "selected": True}],
        "sob": {"columns": [{"id": "one", "label": "Plan 1", "plan_codes": ["1"]}],
                "items": []},
        **sections,
    }}


def term(db):
    return db.execute(select(ProductTerm).where(ProductTerm.product_id == "product")).scalar_one()


def test_draft_round_trip_keeps_live_terms_and_plans_unchanged(context):
    client, db, _ = context
    response = client.put(PATH, json=payload(policy_terms={"pre_hosp_days": "65"}))
    assert response.status_code == 200, response.text
    saved = client.get(PATH).json()
    assert saved["answers"]["policy_terms"] == {"pre_hosp_days": "65"}
    assert saved["status"] == "draft"
    assert term(db).pre_hosp_days == 30
    assert db.execute(select(Plan)).scalars().all() == []


def test_confirm_applies_saved_terms_and_clears_patch(context):
    client, db, _ = context
    body = payload(policy_terms={"pre_hosp_days": "65", "gst_included": True, "gst_rate": "9"})
    saved = client.put(PATH, json=body).json()
    body["expected_updated_at"] = saved["updated_at"]
    response = client.post(f"{PATH}/confirm", json=body)
    assert response.status_code == 200, response.text
    assert term(db).pre_hosp_days == 65
    assert term(db).gst_rate == 9
    assert term(db).gst_included is True
    assert client.get(PATH).json()["answers"]["policy_terms"] == {}
    assert db.execute(select(Plan)).scalars().all()


@pytest.mark.parametrize("patch", [
    {"pre_hosp_days": "invalid"}, {"pre_hosp_days": "366"},
    {"coverage_start": "", "coverage_end": "2030-12-31"},
    {"coverage_start": "2031-01-01", "coverage_end": "2030-12-31"},
    {"gst_rate": "NaN"}, {"free_cover_limit": "Infinity"}, {"product_id": "another-tenant"},
])
def test_incomplete_draft_can_be_saved_but_not_applied(context, patch):
    client, db, _ = context
    body = payload(policy_terms=patch)
    saved = client.put(PATH, json=body)
    assert saved.status_code == 200, saved.text
    body["expected_updated_at"] = saved.json()["updated_at"]
    response = client.post(f"{PATH}/confirm", json=body)
    assert response.status_code == 422, response.text
    assert term(db).pre_hosp_days == 30
    assert client.get(PATH).json()["answers"]["policy_terms"] == patch
    assert db.execute(select(Plan)).scalars().all() == []


def test_confirmation_failure_does_not_partially_apply_terms(context, monkeypatch):
    client, db, _ = context

    def fail_mapping(*args, **kwargs):
        raise HTTPException(422, "Mapping failed")

    monkeypatch.setattr(product_setups, "auto_map_policy_year", fail_mapping)
    body = payload(policy_terms={"pre_hosp_days": "65"})
    saved = client.put(PATH, json=body).json()
    body["expected_updated_at"] = saved["updated_at"]
    response = client.post(f"{PATH}/confirm", json=body)
    assert response.status_code == 422, response.text
    assert term(db).pre_hosp_days == 30
    assert db.execute(select(Plan)).scalars().all() == []
    assert client.get(PATH).json()["status"] == "draft"


def test_saving_changes_to_confirmed_product_marks_pending_and_preserves_live(context):
    client, db, _ = context
    assert client.post(f"{PATH}/confirm", json=payload()).status_code == 200
    saved = client.get(PATH).json()
    response = client.put(PATH, json={**payload(policy_terms={"pre_hosp_days": "90"}),
                                    "expected_updated_at": saved["updated_at"]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "draft"
    assert response.json()["materialized_product_id"] == "product"
    assert term(db).pre_hosp_days == 30
    assert product_setups.seed_draft_from_slip(db, "year", "slip", "GHS", {}, 1) is False


def test_stale_save_does_not_overwrite_saved_progress(context):
    client, _, _ = context
    original = client.put(PATH, json=payload(cover_description="Original")).json()
    body = {**payload(cover_description="First editor"),
            "expected_updated_at": original["updated_at"]}
    assert client.put(PATH, json=body).status_code == 200
    body["answers"]["cover_description"] = "Stale editor"
    assert client.put(PATH, json=body).status_code == 409
    assert client.get(PATH).json()["answers"]["cover_description"] == "First editor"


def test_viewer_cannot_save_or_confirm(context):
    client, _, user = context
    viewer = replace(user, role="broker_viewer")
    app.dependency_overrides[get_current_user] = lambda: viewer
    assert client.put(PATH, json=payload()).status_code == 403
    assert client.post(f"{PATH}/confirm", json=payload()).status_code == 403
