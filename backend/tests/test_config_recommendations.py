"""Slip-driven config recommendations — recommend + apply endpoints.

The AI provider is always mocked (`recommend_schema_via_ai` and, for the roster
path, `propose_derivation_rules_via_ai`) so tests never hit the network; the
gateway's cache/breaker/budget/spend plumbing runs for real.

All data lives under a dedicated test client (C) created inline, with the auth
dependency overridden to a client-C user. The whole pytest suite shares one
SQLite engine, so keeping this module's policy years / employees off the demo
client avoids polluting other modules' seed assertions.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

TEST_DB = Path(__file__).parent / "_test_config_recommendations.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-fake-key")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    CurrentUser,
    get_current_user,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AISpendLog,
    AuditLog,
    Category,
    Client,
    Employee,
    EmployeeAttributeSchema,
    PlacementSlipRow,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.placement_slip import ParseStatus  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services import ai_breaker, ai_cache  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_C_ID = "00000000-0000-0000-0000-0000000000c1"
PY_MAIN = "00000000-0000-0000-0000-0000000000c2"
PY_ROSTER = "00000000-0000-0000-0000-0000000000c3"
PY_EMPTY = "00000000-0000-0000-0000-0000000000c4"
# (raw_description, sheet) — sheet doubles as the detected product code.
_CATS = [
    ("Thailand 11 to 15 Single", "GXP"),
    ("18 and above Married", "GXP"),
    ("All Employees", "GHS"),
]


def _user_c() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000000cc",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_C_ID,
        role="broker_admin",
    )


def _seed_slip_and_categories(db, policy_year_id: str) -> None:
    slip = PlacementSlipRow(
        policy_year_id=policy_year_id,
        uploaded_by=_user_c().user_id,
        filename="test.xlsx",
        parse_status=ParseStatus.parsed,
    )
    db.add(slip)
    db.flush()
    counts: dict[str, int] = {}
    for i, (desc, sheet) in enumerate(_CATS):
        counts[sheet] = counts.get(sheet, 0) + 1
        db.add(
            Category(
                policy_year_id=policy_year_id,
                priority=i + 1,
                display_name=desc,
                raw_description=desc,
                source=SourceKind.system_generated.value,
                source_ref=f"placement_slip://{slip.id}/{sheet}/row_{i}",
                status=CategoryStatus.needs_review.value,
                human_modified=False,
            )
        )
    slip.parse_log = {
        "products_detected": [
            {"sheet": s, "code": s, "categories": n} for s, n in counts.items()
        ]
    }


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()  # global attribute defaults (grade, pass, …) + global product catalog
    with SessionLocal() as db:
        db.add(Client(id=CLIENT_C_ID, name="Client C (test)",
                       broker_firm_id=DEMO_BROKER_FIRM_ID))
        db.flush()
        for py_id, year in ((PY_MAIN, 2026), (PY_ROSTER, 2027), (PY_EMPTY, 2028)):
            db.add(PolicyYear(
                id=py_id, client_id=CLIENT_C_ID, year=year,
                start_date=date(year, 1, 1), end_date=date(year, 12, 31),
                status=PolicyYearStatus.draft,
            ))
        db.flush()
        _seed_slip_and_categories(db, PY_MAIN)
        _seed_slip_and_categories(db, PY_ROSTER)
        for i, desc in enumerate(["Thailand 11 to 15 Single", "18 and above"]):
            db.add(Employee(
                client_id=CLIENT_C_ID, policy_year_id=PY_ROSTER, staff_id=f"R-{i}",
                employee_name=f"Emp {i}", attribute_values={"category": desc, "pass": "WP"},
                derived_attribute_values={},
            ))
        db.commit()
    yield
    # The whole suite shares one SQLite engine, so scrub every row this module
    # created — otherwise other modules' unscoped queries (e.g. a global
    # `select(Category)`) would pick up our client-C data.
    py_ids = [PY_MAIN, PY_ROSTER, PY_EMPTY]
    with SessionLocal() as db:
        db.query(AuditLog).filter(AuditLog.client_id == CLIENT_C_ID).delete(
            synchronize_session=False)
        db.query(AISpendLog).filter(AISpendLog.client_id == CLIENT_C_ID).delete(
            synchronize_session=False)
        db.query(Employee).filter(Employee.client_id == CLIENT_C_ID).delete(
            synchronize_session=False)
        db.query(Category).filter(Category.policy_year_id.in_(py_ids)).delete(
            synchronize_session=False)
        db.query(PlacementSlipRow).filter(
            PlacementSlipRow.policy_year_id.in_(py_ids)).delete(synchronize_session=False)
        db.query(EmployeeAttributeSchema).filter(
            EmployeeAttributeSchema.client_id == CLIENT_C_ID).delete(synchronize_session=False)
        db.query(Product).filter(Product.client_id == CLIENT_C_ID).delete(
            synchronize_session=False)
        db.query(PolicyYear).filter(PolicyYear.id.in_(py_ids)).delete(
            synchronize_session=False)
        db.query(Client).filter(Client.id == CLIENT_C_ID).delete(synchronize_session=False)
        db.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_singletons():
    ai_cache.reset_cache_for_tests()
    ai_breaker.reset_breaker_for_tests()


@pytest.fixture(scope="module")
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user_c
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _mock_recommendation():
    payload = {
        "attributes": [
            {"attribute_id": "job_band", "display_name": "Job Grade",
             "data_type": "integer", "enum_values": None,
             "description": "Numeric grade band", "reasoning": "categories cite grade ranges"},
            {"attribute_id": "grade", "display_name": "Grade", "data_type": "integer",
             "enum_values": None, "description": "existing", "reasoning": "already configured"},
            {"attribute_id": "pass_type", "display_name": "Pass Type", "data_type": "enum",
             "enum_values": ["WP", "SP", "EP"], "description": "Work pass type",
             "reasoning": "WP holders category"},
            {"attribute_id": "id_number", "display_name": "ID Number", "data_type": "string",
             "enum_values": None, "is_pii": True, "description": "NRIC/FIN",
             "reasoning": "identity field"},
        ],
        "products": [
            {"code": "GXP", "display_name": "Group Extra Plan", "insurer": None,
             "participation_model": "standard", "has_dependants": True,
             "is_outpatient": False, "reasoning": "detected, not in catalog"},
            {"code": "GHS", "display_name": "Group Hospital & Surgical", "insurer": None,
             "participation_model": "standard", "has_dependants": True,
             "is_outpatient": False, "reasoning": "already in catalog"},
        ],
    }
    metadata = {"provider": "anthropic", "model": "claude-test",
                "input_tokens": 200, "output_tokens": 100}
    return payload, metadata


def _mock_derivations():
    return (
        [
            {"attribute_id": "job_band", "source": "category",
             "derivation_rule": {"op": "regex_extract", "source": "category",
                                 "pattern": r"\b(\d{1,2})\b", "group": 1, "cast": "int"},
             "confidence": 0.8, "mappable": True, "reasoning": "first number is the grade"},
        ],
        {"provider": "anthropic", "model": "claude-test",
         "input_tokens": 90, "output_tokens": 40},
    )


# ── recommend-config ──────────────────────────────────────────────────────────


def test_recommend_no_roster(client: TestClient) -> None:
    with patch(
        "app.services.ai_gateway.recommend_schema_via_ai",
        return_value=_mock_recommendation(),
    ) as m:
        res = client.post(f"/api/v1/policy-years/{PY_MAIN}/recommend-config")
    assert res.status_code == 200, res.text
    assert m.call_count == 1
    body = res.json()
    assert body["roster_present"] is False
    assert body["category_count"] == len(_CATS)

    attrs = {a["attribute_id"]: a for a in body["attributes"]}
    assert attrs["job_band"]["already_exists"] is False
    assert attrs["grade"]["already_exists"] is True  # global seed default
    assert attrs["pass_type"]["enum_values"] == ["WP", "SP", "EP"]
    assert attrs["id_number"]["is_pii"] is True  # model-flagged PII propagates
    assert attrs["job_band"]["derivation_rule"] is None  # no roster → no derivation

    prods = {p["code"]: p for p in body["products"]}
    assert prods["GXP"]["already_exists"] is False
    assert prods["GXP"]["category_count"] == 2
    assert prods["GHS"]["already_exists"] is True

    with SessionLocal() as db:
        rows = db.execute(
            select(AISpendLog).where(
                AISpendLog.operation == "ai_recommend_config",
                AISpendLog.client_id == CLIENT_C_ID,
            )
        ).scalars().all()
        assert rows and rows[-1].input_tokens == 200


def test_recommend_with_roster_proposes_derivation(client: TestClient) -> None:
    with patch(
        "app.services.ai_gateway.recommend_schema_via_ai",
        return_value=_mock_recommendation(),
    ), patch(
        "app.services.ai_gateway.propose_derivation_rules_via_ai",
        return_value=_mock_derivations(),
    ):
        res = client.post(f"/api/v1/policy-years/{PY_ROSTER}/recommend-config")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["roster_present"] is True
    assert body["employee_count"] == 2
    jg = {a["attribute_id"]: a for a in body["attributes"]}["job_band"]
    assert jg["derivation_rule"] is not None
    assert jg["valid"] is True
    assert jg["match_count"] >= 1
    assert any(s["output"] == 11 for s in jg["samples"])


def test_recommend_no_categories_400(client: TestClient) -> None:
    res = client.post(f"/api/v1/policy-years/{PY_EMPTY}/recommend-config")
    assert res.status_code == 400


def test_recommend_unknown_py_404(client: TestClient) -> None:
    res = client.post(
        "/api/v1/policy-years/00000000-0000-0000-0000-0000000000ff/recommend-config"
    )
    assert res.status_code == 404


# ── apply-config ────────────────────────────────────────────────────────────


def test_apply_config_creates_and_relinks(client: TestClient) -> None:
    rule = {"op": "regex_extract", "source": "category",
            "pattern": r"\b(\d{1,2})\b", "group": 1, "cast": "int"}
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={
            "attributes": [{
                "attribute_id": "job_band", "display_name": "Job Grade",
                "data_type": "integer", "enum_values": None, "is_pii": False,
                "description": "Grade band", "derived_from": "category",
                "derivation_rule": rule,
            }],
            "products": [{
                "code": "GXP", "display_name": "Group Extra Plan", "insurer": None,
                "participation_model": "standard", "has_dependants": True,
                "is_outpatient": False,
            }],
            "rerun_matching": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "job_band" in body["attributes_created"]
    assert "GXP" in body["products_created"]
    assert body["categories_relinked"] == 2  # the two GXP categories
    assert body["rematched"] is True

    with SessionLocal() as db:
        attr = db.execute(
            select(EmployeeAttributeSchema).where(
                EmployeeAttributeSchema.attribute_id == "job_band",
                EmployeeAttributeSchema.client_id == CLIENT_C_ID,
            )
        ).scalar_one()
        assert attr.derivation_rule == rule
        product = db.execute(
            select(Product).where(
                Product.code == "GXP", Product.client_id == CLIENT_C_ID
            )
        ).scalar_one()
        relinked = db.execute(
            select(Category).where(
                Category.policy_year_id == PY_MAIN, Category.product_id == product.id
            )
        ).scalars().all()
        assert len(relinked) == 2


def test_apply_config_rejects_bad_regex(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={"attributes": [{
            "attribute_id": "test_bad", "display_name": "Bad", "data_type": "string",
            "derivation_rule": {"op": "regex_extract", "source": "category",
                                "pattern": r"(unclosed"},
        }], "products": [], "rerun_matching": False},
    )
    assert res.status_code == 422


def test_apply_config_enum_without_values_422(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={"attributes": [{
            "attribute_id": "test_enum", "display_name": "Enum", "data_type": "enum",
            "enum_values": None,
        }], "products": [], "rerun_matching": False},
    )
    assert res.status_code == 422


def test_apply_config_nothing_400(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={"attributes": [], "products": [], "rerun_matching": False},
    )
    assert res.status_code == 400


def test_apply_config_persists_pii_flag(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={"attributes": [{
            "attribute_id": "id_number", "display_name": "ID Number",
            "data_type": "string", "is_pii": True,
        }], "products": [], "rerun_matching": False},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as db:
        row = db.execute(
            select(EmployeeAttributeSchema).where(
                EmployeeAttributeSchema.attribute_id == "id_number",
                EmployeeAttributeSchema.client_id == CLIENT_C_ID,
            )
        ).scalar_one()
        assert row.is_pii is True

    # Re-applying with is_pii=False must NOT downgrade (PII is sticky).
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={"attributes": [{
            "attribute_id": "id_number", "display_name": "ID Number",
            "data_type": "string", "is_pii": False,
        }], "products": [], "rerun_matching": False},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as db:
        row = db.execute(
            select(EmployeeAttributeSchema).where(
                EmployeeAttributeSchema.attribute_id == "id_number",
                EmployeeAttributeSchema.client_id == CLIENT_C_ID,
            )
        ).scalar_one()
        assert row.is_pii is True


def test_apply_config_dedups_same_attribute_in_payload(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={"attributes": [
            {"attribute_id": "dup_attr", "display_name": "First", "data_type": "string"},
            {"attribute_id": "dup_attr", "display_name": "Second", "data_type": "string"},
        ], "products": [], "rerun_matching": False},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as db:
        rows = db.execute(
            select(EmployeeAttributeSchema).where(
                EmployeeAttributeSchema.attribute_id == "dup_attr",
                EmployeeAttributeSchema.client_id == CLIENT_C_ID,
            )
        ).scalars().all()
        assert len(rows) == 1  # second item updated the first, no duplicate row
        assert rows[0].display_name == "Second"


def test_apply_config_rule_missing_source_422(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_MAIN}/apply-config",
        json={"attributes": [{
            "attribute_id": "test_nosrc", "display_name": "No Source",
            "data_type": "string",
            "derivation_rule": {"op": "regex_extract", "pattern": r"(\d+)"},
        }], "products": [], "rerun_matching": False},
    )
    assert res.status_code == 422


def test_unique_constraint_blocks_duplicate_client_row() -> None:
    with SessionLocal() as db:
        db.add(EmployeeAttributeSchema(
            client_id=CLIENT_C_ID, attribute_id="uq_probe",
            display_name="A", data_type="string",
        ))
        db.commit()
    with SessionLocal() as db, pytest.raises(IntegrityError):
        db.add(EmployeeAttributeSchema(
            client_id=CLIENT_C_ID, attribute_id="uq_probe",
            display_name="B", data_type="string",
        ))
        db.commit()


def test_sheet_from_source_ref_strips_whitespace() -> None:
    from app.api.v1.recommendations import _samples_for_sheets, _sheet_from_source_ref

    assert _sheet_from_source_ref("placement_slip://slip/ GHS /row_2") == "GHS"
    assert _sheet_from_source_ref("placement_slip://slip//row_2") is None
    assert _sheet_from_source_ref("garbage") is None
    # Samples aggregate across every sheet mapped to one product code.
    agg = _samples_for_sheets({"A": ["x", "y"], "B": ["y", "z"]}, ["A", "B"])
    assert agg == ["x", "y", "z"]  # deduped, order preserved
