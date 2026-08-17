"""Dependant self-add: pending status, proof documents, broker approval flow,
and the invariant that a pending dependant never leaks into coverage."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_dependants.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.core.settings import clear_settings_cache  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Dependant, Employee, MemberAccount, PolicyYear  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-00000000pd01"
EMP_A = "00000000-0000-0000-0000-00000000pd02"
ACC_A = "00000000-0000-0000-0000-00000000pd03"

PNG = b"\x89PNG\r\n\x1a\nfake proof bytes"


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    os.environ["INSPRO_STORAGE_DIR"] = str(tmp_path_factory.mktemp("dep_storage"))
    clear_settings_cache()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY,
                client_id=DEMO_CLIENT_ID,
                year=2027,  # not 2026 — see seed() one_or_none note
                start_date=date(2027, 5, 1),
                end_date=date(2028, 4, 30),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        session.add(
            MemberAccount(
                id=ACC_A, client_id=DEMO_CLIENT_ID, email="dep@a.test",
                staff_id="PD-1", status="active",
            )
        )
        session.add(
            Employee(
                id=EMP_A, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="PD-1", employee_name="Alice", member_account_id=ACC_A,
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status="active",
            )
        )
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(MemberAccount).filter(
            MemberAccount.client_id == DEMO_CLIENT_ID
        ).delete()
        py = session.get(PolicyYear, PY)
        if py is not None:
            session.delete(py)
        session.commit()
    os.environ.pop("INSPRO_STORAGE_DIR", None)
    clear_settings_cache()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def anon() -> TestClient:
    return TestClient(app)


@pytest.fixture
def broker() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _auth() -> dict[str, str]:
    token, _ = issue_member_token(ACC_A, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def _add_dependant(anon: TestClient, name: str = "Junior") -> dict:
    res = anon.post(
        "/api/v1/portal/dependants",
        json={"name": name, "relationship": "Child", "dob": "2020-03-14"},
        headers=_auth(),
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_self_add_is_pending_and_linked(anon: TestClient):
    dep = _add_dependant(anon, "Pending Kid")
    assert dep["status"] == "pending_approval"
    assert dep["employee_id"] == EMP_A
    assert dep["link_method"] == "member_portal"
    assert dep["attribute_values"]["relationship"] == "child"  # normalized
    assert dep["attribute_values"]["dob"] == "2020-03-14"

    # Member sees it in their own list (with the pending status).
    listing = anon.get("/api/v1/portal/dependants", headers=_auth())
    row = next(d for d in listing.json() if d["id"] == dep["id"])
    assert row["status"] == "pending_approval"


def test_pending_dependant_not_in_benefit_statement(anon: TestClient):
    dep = _add_dependant(anon, "Invisible Kid")
    res = anon.get("/api/v1/portal/benefit-statement", headers=_auth())
    assert res.status_code == 200
    assert dep["id"] not in {d["id"] for d in res.json()["dependants"]}


def test_proof_upload_and_broker_download(anon: TestClient, broker: TestClient):
    dep = _add_dependant(anon, "Proof Kid")
    res = anon.post(
        f"/api/v1/portal/dependants/{dep['id']}/documents",
        files={"file": ("birth-cert.png", PNG, "image/png")},
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    doc_id = res.json()["id"]

    docs = broker.get(f"/api/v1/dependants/{dep['id']}/documents")
    assert docs.status_code == 200
    assert docs.json()[0]["id"] == doc_id

    dl = broker.get(f"/api/v1/dependants/{dep['id']}/documents/{doc_id}/download")
    assert dl.status_code == 200
    assert dl.content == PNG


def test_broker_approval_activates(anon: TestClient, broker: TestClient):
    dep = _add_dependant(anon, "Approved Kid")
    res = broker.post(
        f"/api/v1/dependants/{dep['id']}/approval",
        json={"action": "approve"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "active"

    # Now it shows on the member statement.
    stmt = anon.get("/api/v1/portal/benefit-statement", headers=_auth())
    assert dep["id"] in {d["id"] for d in stmt.json()["dependants"]}

    # Approving twice is a conflict.
    res = broker.post(
        f"/api/v1/dependants/{dep['id']}/approval", json={"action": "approve"}
    )
    assert res.status_code == 409


def test_broker_reject(anon: TestClient, broker: TestClient):
    dep = _add_dependant(anon, "Rejected Kid")
    res = broker.post(
        f"/api/v1/dependants/{dep['id']}/approval",
        json={"action": "reject", "note": "No proof provided"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"

    stmt = anon.get("/api/v1/portal/benefit-statement", headers=_auth())
    assert dep["id"] not in {d["id"] for d in stmt.json()["dependants"]}


def test_proof_upload_after_decision_409(anon: TestClient, broker: TestClient):
    dep = _add_dependant(anon, "Locked Kid")
    broker.post(f"/api/v1/dependants/{dep['id']}/approval", json={"action": "approve"})
    res = anon.post(
        f"/api/v1/portal/dependants/{dep['id']}/documents",
        files={"file": ("late.png", PNG, "image/png")},
        headers=_auth(),
    )
    assert res.status_code == 409


def test_broker_pending_filter(anon: TestClient, broker: TestClient):
    dep = _add_dependant(anon, "Filtered Kid")
    res = broker.get(
        f"/api/v1/dependants?policy_year_id={PY}&status=pending_approval"
    )
    assert res.status_code == 200
    ids = {d["id"] for d in res.json()["items"]}
    assert dep["id"] in ids
    with SessionLocal() as session:
        statuses = {
            session.get(Dependant, i).status for i in ids
        }
    assert statuses == {"pending_approval"}
