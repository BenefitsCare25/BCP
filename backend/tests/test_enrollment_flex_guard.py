"""Server-side flex-wallet guards at enrollment submit/confirm.

The election panel's balance strip is display-only; these tests pin the
ENFORCEMENT: an overdrawn enrollment can't be finalized unless the window
allows overdrafts, and changed-but-unpriced elections need explicit broker
acknowledgment. Guards read the SNAPSHOTTED ``flex_price_tag``, so tests set
tags directly rather than coupling to the pricing plumbing.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_enrollment_flex_guard.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Employee,
    EmployeePlanOverride,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    LeaveElection,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000000f200"
PY_ID = "00000000-0000-0000-0000-00000000f201"
PROD_ID = "00000000-0000-0000-0000-00000000f202"
CAT_ID = "00000000-0000-0000-0000-00000000f203"
EMP_WALLET = "00000000-0000-0000-0000-00000000f204"  # flex member, $100 wallet
EMP_NO_WALLET = "00000000-0000-0000-0000-00000000f205"  # not a flex member


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000f2ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Guard Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2029,
            start_date=date(2029, 1, 1), end_date=date(2029, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        s.add(Product(
            id=PROD_ID, client_id=CLIENT_ID, code="MED",
            display_name="Medical", insurer="ACME", has_dependants=True,
        ))
        s.flush()
        for code, name in (("SILVER", "Silver"), ("GOLD", "Gold")):
            s.add(Plan(
                id=f"f-plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                code=code, display_name=name, status="confirmed",
            ))
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="All staff", raw_description="All staff",
            plan_assignments={"plan_code": "SILVER"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        for eid, staff, wallet in (
            (EMP_WALLET, "F-1", 100.0),
            (EMP_NO_WALLET, "F-2", None),
        ):
            s.add(Employee(
                id=eid, client_id=CLIENT_ID, policy_year_id=PY_ID,
                staff_id=staff, employee_name=f"Emp {staff}",
                attribute_values={}, derived_attribute_values={},
                matched_categories=[{"category_id": CAT_ID, "product_code": "MED",
                                     "method": "rule", "confidence": 1.0}],
                flex_wallet_amount=wallet,
                source="csv_import", status="active",
            ))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_enrollment_state():
    yield
    with SessionLocal() as s:
        for model in (LeaveElection, EnrollmentElection, Enrollment,
                      EmployeePlanOverride, EnrollmentWindow):
            s.query(model).delete()
        s.commit()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _make_window(client: TestClient, **over) -> str:
    body = {
        "name": "OE", "window_type": "open",
        "opens_at": "2020-01-01T00:00:00Z", "closes_at": "2035-01-01T00:00:00Z",
    }
    body.update(over)
    wid = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows", json=body
    ).json()["id"]
    opened = client.post(f"/api/v1/enrollment-windows/{wid}/open")
    assert opened.status_code == 200, opened.text
    # These tests isolate the submit/confirm guard. Mark the already-open period
    # as Flex-funded directly so the draft-opening readiness gate is tested in
    # test_enrollment_config instead of requiring a full scheme fixture here.
    with SessionLocal() as s:
        s.get(EnrollmentWindow, wid).uses_flex = True
        s.commit()
    return wid


def _enrollment_id(client: TestClient, wid: str, staff: str) -> str:
    roster = client.get(f"/api/v1/enrollment-windows/{wid}/enrollments").json()
    return next(i["id"] for i in roster["items"] if i["staff_id"] == staff)


def _elect_gold(client: TestClient, eid: str) -> None:
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
    )
    assert res.status_code == 200, res.text


def _set_tag(eid: str, tag: float | None) -> None:
    with SessionLocal() as s:
        row = s.query(EnrollmentElection).filter_by(enrollment_id=eid).one()
        row.flex_price_tag = tag
        s.commit()


def test_overdrawn_submit_blocked_then_allowed_by_overdraft_window(
    client: TestClient,
) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "F-1")
    _elect_gold(client, eid)
    _set_tag(eid, 150.0)  # wallet is 100 -> balance -50

    res = client.post(f"/api/v1/enrollments/{eid}/submit")
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "flex_overdrawn"
    assert detail["wallet"] == 100.0
    assert detail["balance"] == -50.0

    # Enabling overdraft on the window unblocks the same enrollment.
    patched = client.patch(
        f"/api/v1/enrollment-windows/{wid}", json={"allow_overdraft": True}
    )
    assert patched.status_code == 200 and patched.json()["allow_overdraft"] is True
    ok = client.post(f"/api/v1/enrollments/{eid}/submit")
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "submitted"


def test_overdraft_recheck_at_confirm(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "F-1")
    _elect_gold(client, eid)
    _set_tag(eid, 60.0)  # within wallet at submit
    assert client.post(
        f"/api/v1/enrollments/{eid}/submit",
        json={"acknowledge_unpriced": True},
    ).status_code == 200

    _set_tag(eid, 150.0)  # tag grows past the wallet before confirm
    res = client.post(f"/api/v1/enrollments/{eid}/confirm")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "flex_overdrawn"

    _set_tag(eid, 90.0)
    assert client.post(f"/api/v1/enrollments/{eid}/confirm").status_code == 200


def test_unpriced_changed_election_needs_acknowledgment(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "F-1")
    _elect_gold(client, eid)  # no pricing configured -> flex_price_tag is None

    res = client.post(f"/api/v1/enrollments/{eid}/submit")
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "unpriced_elections"
    assert detail["products"] == ["MED"]

    ok = client.post(
        f"/api/v1/enrollments/{eid}/submit", json={"acknowledge_unpriced": True}
    )
    assert ok.status_code == 200, ok.text


def test_non_flex_member_is_not_guarded(client: TestClient) -> None:
    # No wallet -> flex doesn't apply; unpriced changed elections submit freely.
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "F-2")
    _elect_gold(client, eid)
    res = client.post(f"/api/v1/enrollments/{eid}/submit")
    assert res.status_code == 200, res.text


def test_keep_and_decline_elections_are_not_flagged_unpriced(
    client: TestClient,
) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "F-1")
    # Electing the baseline plan (keep) carries no tag -- that's legitimate.
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "SILVER"}]},
    )
    assert res.status_code == 200, res.text
    assert client.post(f"/api/v1/enrollments/{eid}/submit").status_code == 200

