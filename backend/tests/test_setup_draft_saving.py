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
from app.services.policy_numbers import resolve_policy_number
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
    return db.execute(select(ProductTerm).where(
        ProductTerm.product_id == "product", ProductTerm.policy_year_id == "year",
    )).scalar_one()


def test_policy_assignments_remain_draft_until_atomic_confirmation(context):
    client, db, _ = context
    mappings = [
        {"entity": "Entity A", "policy_number": "POL-A"},
        {"entity": "Entity B", "policy_number": "POL-B"},
    ]
    body = payload(
        header={"policy_no": "POL-A, POL-B"}, policy_number_mappings=mappings,
        policy_terms={"pre_hosp_days": "42"},
    )
    saved = client.put(PATH, json=body)
    assert saved.status_code == 200, saved.text
    assert term(db).policy_number_mappings is None
    body["expected_updated_at"] = saved.json()["updated_at"]
    applied = client.post(f"{PATH}/confirm", json=body)
    assert applied.status_code == 200, applied.text
    assert term(db).policy_number_mappings == mappings
    assert term(db).policy_number is None  # a scalar would lose the entity association
    assert term(db).pre_hosp_days == 42
    assert resolve_policy_number(term(db), " entity a ").number == "POL-A"
    assert resolve_policy_number(term(db), "Entity B").number == "POL-B"
    assert resolve_policy_number(term(db), "Other entity").number is None
    assert client.get(PATH).json()["answers"]["header"]["policy_no"] == "POL-A, POL-B"


@pytest.mark.parametrize("mappings", [
    [{"entity": "A", "policy_number": "ONE, TWO"}],
    [{"entity": "A", "policy_number": "TBA"}],
    [{"entity": "A", "policy_number": ""}],
    [{"entity": "A", "policy_number": "X" * 65}],
    [{"entity": "A", "policy_number": "ONE"}, {"entity": " a ", "policy_number": "TWO"}],
    [{"entity": "", "policy_number": "ONE"}],
    [{"entity": None, "policy_number": "ONE", "product_id": "another-client"}],
    None,
])
def test_incomplete_policy_mapping_can_save_but_cannot_apply(context, mappings):
    client, db, _ = context
    body = payload(policy_number_mappings=mappings, policy_terms={"pre_hosp_days": "42"})
    saved = client.put(PATH, json=body)
    assert saved.status_code == 200, saved.text
    body["expected_updated_at"] = saved.json()["updated_at"]
    applied = client.post(f"{PATH}/confirm", json=body)
    assert applied.status_code == 422, applied.text
    assert term(db).policy_number_mappings is None
    assert term(db).pre_hosp_days == 30
    assert not db.scalars(select(Plan)).all()


def test_composite_legacy_numbers_require_review_before_confirmation(context):
    client, db, _ = context
    body = payload(header={"policy_no": "G0005086, G0005088, G0005089"})
    saved = client.put(PATH, json=body)
    assert saved.status_code == 200
    body["expected_updated_at"] = saved.json()["updated_at"]
    applied = client.post(f"{PATH}/confirm", json=body)
    assert applied.status_code == 422
    assert term(db).policy_number is None
    assert not db.scalars(select(Plan)).all()


def test_new_term_with_mapping_and_staged_terms_is_created_once(context):
    client, db, _ = context
    db.delete(term(db))
    db.commit()
    body = payload(
        policy_number_mappings=[{"entity": None, "policy_number": "PRODUCT-WIDE"}],
        policy_terms={"pre_hosp_days": "42"},
    )
    applied = client.post(f"{PATH}/confirm", json=body)
    assert applied.status_code == 200, applied.text
    assert term(db).policy_number == "PRODUCT-WIDE"
    assert term(db).pre_hosp_days == 42


def test_only_system_admin_can_remove_saved_policy_assignments(context):
    client, db, user = context
    body = payload(policy_number_mappings=[{"entity": "A", "policy_number": "ONE"}])
    assert client.post(f"{PATH}/confirm", json=body).status_code == 200
    cleared = payload(policy_number_mappings=[])
    assert client.put(PATH, json=cleared).status_code == 403
    assert client.post(f"{PATH}/confirm", json=cleared).status_code == 403
    assert client.put(PATH, json=payload(policy_number_mappings=None)).status_code == 403
    endpoint = "/api/v1/policy-years/year/product-terms/product"
    assert client.put(endpoint, json={"policy_number_mappings": []}).status_code == 403
    assert term(db).policy_number_mappings == body["answers"]["policy_number_mappings"]
    app.dependency_overrides[get_current_user] = lambda: replace(user, role="system_admin")
    cleared["expected_updated_at"] = client.get(PATH).json()["updated_at"]
    assert client.post(f"{PATH}/confirm", json=cleared).status_code == 200
    assert term(db).policy_number_mappings == []


def test_operational_mapping_update_rejects_legacy_overwrite(context):
    client, db, _ = context
    endpoint = "/api/v1/policy-years/year/product-terms/product"
    result = client.put(endpoint, json={"policy_number_mappings": [
        {"entity": "A", "policy_number": "POL-A"},
    ]})
    assert result.status_code == 200, result.text
    assert result.json()["policy_number_mappings"][0]["entity"] == "A"
    assert client.put(endpoint, json={"policy_number": "OVERWRITE"}).status_code == 409
    assert resolve_policy_number(term(db), "A").number == "POL-A"


def test_renewal_does_not_reuse_issued_policy_numbers(context):
    from app.models import ProductSetup
    from app.services.policy_year_clone import clone_policy_year_config
    from app.services.product_terms import resolve_terms

    client, db, _ = context
    body = payload(
        header={"policy_no": "POL-2030"},
        policy_number_mappings=[{"entity": None, "policy_number": "POL-2030"}],
    )
    assert client.post(f"{PATH}/confirm", json=body).status_code == 200
    renewal = PolicyYear(
        id="renewal", client_id="client", year=2031,
        start_date=date(2031, 1, 1), end_date=date(2031, 12, 31), status="draft",
    )
    db.add(renewal)
    db.flush()
    clone_policy_year_config(db, source_id="year", target_id="renewal", client_id="client")
    target = db.scalar(select(ProductTerm).where(ProductTerm.policy_year_id == "renewal"))
    assert target.policy_number is None
    assert target.policy_number_mappings == []
    target_setup = db.scalar(select(ProductSetup).where(
        ProductSetup.policy_year_id == "renewal",
    ))
    assert target_setup.answers["header"]["policy_no"] == ""
    assert target_setup.answers["policy_number_mappings"] == []
    assert resolve_policy_number(resolve_terms(db, renewal)[0], "A").number is None
    assert term(db).policy_number == "POL-2030"


def test_older_client_omitting_mapping_field_preserves_saved_assignments(context):
    client, _, _ = context
    body = payload(policy_number_mappings=[{"entity": "A", "policy_number": "POL-A"}])
    saved = client.put(PATH, json=body).json()
    old_client_body = payload()
    old_client_body["expected_updated_at"] = saved["updated_at"]
    result = client.put(PATH, json=old_client_body)
    assert result.status_code == 200, result.text
    assert (
        result.json()["answers"]["policy_number_mappings"]
        == body["answers"]["policy_number_mappings"]
    )


def test_operational_mapping_edit_refreshes_confirmed_setup_and_rejects_stale_confirm(context):
    client, _, _ = context
    body = payload(policy_number_mappings=[{"entity": "A", "policy_number": "OLD"}])
    assert client.post(f"{PATH}/confirm", json=body).status_code == 200
    before = client.get(PATH).json()
    endpoint = "/api/v1/policy-years/year/product-terms/product"
    applied = [{"entity": "A", "policy_number": "NEW"}]
    response = client.put(endpoint, json={"policy_number_mappings": applied})
    assert response.status_code == 200, response.text
    after = client.get(PATH).json()
    assert after["answers"]["policy_number_mappings"] == applied
    assert after["updated_at"] != before["updated_at"]
    body["expected_updated_at"] = before["updated_at"]
    assert client.post(f"{PATH}/confirm", json=body).status_code == 409


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
