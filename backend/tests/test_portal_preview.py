"""Broker "employee view" preview — GET /employees/{id}/portal-preview/*.

The core invariant: the preview returns EXACTLY what the member sees on the
matching /portal/* endpoint (member-gated statement, same dependants, same
claims), while access control is the broker's (load_employee), not a member
token. Cross-tenant 404s live in test_tenant_isolation.py.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_preview.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Dependant, Employee, MemberAccount, PolicyYear  # noqa: E402
from app.models.member_account import MEMBER_STATUS_ACTIVE  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-00000000pp01"
EMP_ALICE = "00000000-0000-0000-0000-00000000pp02"
EMP_DAVE = "00000000-0000-0000-0000-00000000pp03"
DEP_ALICE = "00000000-0000-0000-0000-00000000pp04"
ACC_ALICE = "00000000-0000-0000-0000-00000000pp05"
ACC_DAVE = "00000000-0000-0000-0000-00000000pp06"


def _broker() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000pp99",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY,
                # Year 2027, NOT 2026 — a second demo 2026 policy year breaks
                # seed()'s .one_or_none() for later modules on the shared DB.
                client_id=DEMO_CLIENT_ID,
                year=2027,
                start_date=date(2027, 3, 1),
                end_date=date(2028, 2, 28),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        session.add(
            MemberAccount(
                id=ACC_ALICE,
                client_id=DEMO_CLIENT_ID,
                email="alice@a.test",
                staff_id="S-1",
                status=MEMBER_STATUS_ACTIVE,
            )
        )
        session.add(
            MemberAccount(
                id=ACC_DAVE,
                client_id=DEMO_CLIENT_ID,
                email="dave@a.test",
                staff_id="S-3",
                status=MEMBER_STATUS_ACTIVE,
            )
        )
        session.flush()
        session.add(
            Employee(
                id=EMP_ALICE,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY,
                staff_id="S-1",
                employee_name="Alice",
                member_account_id=ACC_ALICE,
                attribute_values={},
                derived_attribute_values={},
                source="csv_import",
                status="active",
            )
        )
        # Dave has an account but the roster row is unstamped — the preview
        # context must still surface it via the (client_id, staff_id) key.
        session.add(
            Employee(
                id=EMP_DAVE,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY,
                staff_id="S-3",
                employee_name="Dave",
                attribute_values={},
                derived_attribute_values={},
                source="csv_import",
                status="active",
            )
        )
        session.flush()
        session.add(
            Dependant(
                id=DEP_ALICE,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY,
                employee_id=EMP_ALICE,
                attribute_values={"name": "Alice Jr", "relationship": "child"},
                link_method="staff_id",
                status="active",
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
            session.delete(py)  # cascades employees + dependants
        session.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def broker() -> TestClient:
    app.dependency_overrides[get_current_user] = _broker
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _member_auth(account_id: str) -> dict[str, str]:
    token, _ = issue_member_token(account_id, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def test_preview_context(broker: TestClient) -> None:
    res = broker.get(f"/api/v1/employees/{EMP_ALICE}/portal-preview")
    assert res.status_code == 200
    body = res.json()
    assert body["employee"]["id"] == EMP_ALICE
    assert body["policy_year"]["id"] == PY
    assert body["is_active_policy_year"] is True
    assert body["member_account"]["email"] == "alice@a.test"


def test_preview_context_unstamped_account_via_staff_id(broker: TestClient) -> None:
    res = broker.get(f"/api/v1/employees/{EMP_DAVE}/portal-preview")
    assert res.status_code == 200
    assert res.json()["member_account"]["id"] == ACC_DAVE


def test_preview_statement_matches_portal(broker: TestClient) -> None:
    preview = broker.get(f"/api/v1/employees/{EMP_ALICE}/portal-preview/benefit-statement")
    portal = broker.get(
        "/api/v1/portal/benefit-statement", headers=_member_auth(ACC_ALICE)
    )
    assert preview.status_code == portal.status_code == 200
    assert preview.json() == portal.json()
    # Member-gated: broker-only internals must be stripped in the preview too.
    for line in preview.json()["coverage"]:
        assert line["financials"] is None
        assert line["match_method"] is None


def test_preview_utilization_matches_portal(broker: TestClient) -> None:
    preview = broker.get(f"/api/v1/employees/{EMP_ALICE}/portal-preview/utilization")
    portal = broker.get("/api/v1/portal/utilization", headers=_member_auth(ACC_ALICE))
    assert preview.status_code == portal.status_code == 200
    assert preview.json() == portal.json()


def test_preview_coverage_options_match_portal(broker: TestClient) -> None:
    preview = broker.get(
        f"/api/v1/employees/{EMP_ALICE}/portal-preview/coverage-options"
    )
    portal = broker.get(
        "/api/v1/portal/coverage-options", headers=_member_auth(ACC_ALICE)
    )
    assert preview.status_code == portal.status_code == 200
    assert preview.json() == portal.json()


def test_preview_dependants_match_portal(broker: TestClient) -> None:
    preview = broker.get(f"/api/v1/employees/{EMP_ALICE}/portal-preview/dependants")
    portal = broker.get("/api/v1/portal/dependants", headers=_member_auth(ACC_ALICE))
    assert preview.status_code == portal.status_code == 200
    assert preview.json() == portal.json()
    assert {d["id"] for d in preview.json()} == {DEP_ALICE}


def test_preview_claims_match_portal(broker: TestClient) -> None:
    preview = broker.get(f"/api/v1/employees/{EMP_ALICE}/portal-preview/claims")
    portal = broker.get("/api/v1/portal/claims", headers=_member_auth(ACC_ALICE))
    assert preview.status_code == portal.status_code == 200
    assert preview.json() == portal.json()
