"""Backfill structured outpatient per-policy-year claim limits."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Client, Plan, PolicyYear, Product, ProductSetup
from app.models.category import CategoryStatus, SourceKind
from app.models.product_setup import ProductSetupOrigin, ProductSetupStatus


def _migration(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrations_repair_unsafe_suggestions_without_overwriting_decisions(
    monkeypatch,
) -> None:
    verified = {
        "basis": "policy_year",
        "amount": 250,
        "currency": "SGD",
        "display": "SGD 250 per policy year",
        "claim_scope_codes": ["gp_tcm"],
        "status": "verified",
        "source": "manual",
    }
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        db.add(Client(id="client", name="Client", broker_firm_id="firm"))
        db.flush()
        db.add(
            PolicyYear(
                id="year",
                client_id="client",
                year=2030,
                start_date=date(2030, 1, 1),
                end_date=date(2030, 12, 31),
                status="draft",
            )
        )
        product = Product(id="product", code="GCGP", display_name="Clinical GP")
        db.add(product)
        db.flush()
        db.add(
            Plan(
                id="plan",
                product_id=product.id,
                policy_year_id="year",
                code="1",
                display_name="Plan 1",
                benefit_schedule={
                    "items": [
                        {
                            "number": "-4",
                            "name": "Traditional Chinese Medicine",
                            "kind": "copay",
                            "value": None,
                            "properties": {"per_policy_year": "SGD 300"},
                        },
                        {
                            "number": "-5",
                            "name": "Physiotherapy",
                            "kind": "copay",
                            "value": None,
                            "properties": {"per_policy_year": "5 visits"},
                        },
                        {
                            "number": "-6",
                            "name": "Existing decision",
                            "value": "SGD 250 per policy year",
                            "claim_limit": verified,
                        },
                        {
                            "number": "-7",
                            "name": "Previously verified count",
                            "kind": "copay",
                            "value": None,
                            "properties": {"per_policy_year": "5 visits"},
                            "claim_limit": {
                                **verified,
                                "amount": 5,
                                "display": "5 visits per policy year",
                                "claim_scope_codes": [],
                            },
                        },
                        {
                            "number": "-8",
                            "name": "Stale monetary setting",
                            "kind": "copay",
                            "value": None,
                            "properties": {"per_policy_year": "SGD 500"},
                            "claim_limit": {
                                **verified,
                                "amount": 300,
                                "display": "SGD 300 per policy year",
                                "claim_scope_codes": [],
                            },
                        },
                    ]
                },
                source=SourceKind.system_generated.value,
                status=CategoryStatus.needs_review.value,
            )
        )
        db.add(
            ProductSetup(
                id="setup",
                policy_year_id="year",
                product_code="GCGP",
                template_version=1,
                answers={
                    "plans": [{"code": "1", "label": "Plan 1", "selected": True}],
                    "sob": {
                        "columns": [{"id": "col0", "label": "Plan 1", "plan_codes": ["1"]}],
                        "items": [
                            {
                                "uid": "tcm",
                                "number": "-4",
                                "name": "Traditional Chinese Medicine",
                                "kind": "copay",
                                "base_value": "",
                                "overrides": {},
                                "properties": {},
                                "column_properties": {
                                    "col0": {"per_policy_year": "SGD 300"}
                                },
                                "sub_items": [],
                            },
                            {
                                "uid": "physio",
                                "number": "-5",
                                "name": "Physiotherapy",
                                "kind": "copay",
                                "base_value": "",
                                "overrides": {},
                                "properties": {},
                                "column_properties": {
                                    "col0": {"per_policy_year": "5 visits"}
                                },
                                "sub_items": [],
                            },
                            {
                                "uid": "existing",
                                "number": "-6",
                                "name": "Existing decision",
                                "kind": "copay",
                                "base_value": "SGD 250 per policy year",
                                "overrides": {},
                                "properties": {},
                                "column_properties": {},
                                "sub_items": [],
                                "claim_limits": {"col0": verified},
                            },
                            {
                                "uid": "verified-count",
                                "number": "-7",
                                "name": "Previously verified count",
                                "kind": "copay",
                                "base_value": "",
                                "overrides": {},
                                "properties": {},
                                "column_properties": {
                                    "col0": {"per_policy_year": "5 visits"}
                                },
                                "sub_items": [],
                                "claim_limits": {
                                    "col0": {
                                        **verified,
                                        "amount": 5,
                                        "display": "5 visits per policy year",
                                        "claim_scope_codes": [],
                                    }
                                },
                            },
                            {
                                "uid": "stale",
                                "number": "-8",
                                "name": "Stale monetary setting",
                                "kind": "copay",
                                "base_value": "",
                                "overrides": {},
                                "properties": {},
                                "column_properties": {
                                    "col0": {"per_policy_year": "SGD 500"}
                                },
                                "sub_items": [],
                                "claim_limits": {
                                    "col0": {
                                        **verified,
                                        "amount": 300,
                                        "display": "SGD 300 per policy year",
                                        "claim_scope_codes": [],
                                    }
                                },
                            }
                        ],
                    },
                },
                status=ProductSetupStatus.draft,
                origin=ProductSetupOrigin.placement_slip,
            )
        )
        db.commit()

    seed = _migration(
        "d6a8c0e2f4b3_seed_copay_claim_limit_suggestions.py",
        "seed_copay_limit_migration",
    )
    repair = _migration(
        "e8c0d2f4b6a9_repair_copay_claim_limit_suggestions.py",
        "repair_copay_limit_migration",
    )
    with engine.begin() as connection:
        monkeypatch.setattr(seed.op, "get_bind", lambda: connection)
        seed.upgrade()
        monkeypatch.setattr(repair.op, "get_bind", lambda: connection)
        repair.upgrade()
        first = connection.exec_driver_sql(
            "SELECT answers FROM product_setups WHERE id = 'setup'"
        ).scalar_one()
        repair.upgrade()
        second = connection.exec_driver_sql(
            "SELECT answers FROM product_setups WHERE id = 'setup'"
        ).scalar_one()
    assert first == second

    with Session(engine) as db:
        schedule = db.get(Plan, "plan").benefit_schedule
        detected = schedule["items"][0]["claim_limit"]
        assert detected["amount"] == 300
        assert detected["display"] == "SGD 300 per policy year"
        assert detected["claim_scope_codes"] == []
        assert detected["status"] == "needs_review"
        count = schedule["items"][1]["claim_limit"]
        assert count["basis"] == "informational"
        assert count["amount"] is None
        assert count["claim_scope_codes"] == ["gp_physiotherapy"]
        assert schedule["items"][2]["claim_limit"] == verified
        verified_count = schedule["items"][3]["claim_limit"]
        assert verified_count["basis"] == "informational"
        assert verified_count["amount"] is None
        assert verified_count["status"] == "needs_review"
        assert schedule["items"][4]["claim_limit"]["status"] == "needs_review"

        answers = db.get(ProductSetup, "setup").answers
        setup_limit = answers["sob"]["items"][0]["claim_limits"]["col0"]
        assert setup_limit["amount"] == 300
        assert setup_limit["claim_scope_codes"] == []
        assert setup_limit["status"] == "needs_review"
        setup_count = answers["sob"]["items"][1]["claim_limits"]["col0"]
        assert setup_count["basis"] == "informational"
        assert setup_count["amount"] is None
        assert setup_count["claim_scope_codes"] == ["gp_physiotherapy"]
        assert answers["sob"]["items"][2]["claim_limits"]["col0"] == verified
        setup_verified_count = answers["sob"]["items"][3]["claim_limits"]["col0"]
        assert setup_verified_count["basis"] == "informational"
        assert setup_verified_count["amount"] is None
        assert setup_verified_count["status"] == "needs_review"
        assert (
            answers["sob"]["items"][4]["claim_limits"]["col0"]["status"]
            == "needs_review"
        )
    engine.dispose()
