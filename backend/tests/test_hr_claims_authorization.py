"""Tenant, role and per-HR-owner boundaries for delegated claims."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import hr_claims
from app.core.auth import CurrentUser, Role
from app.core.hr_auth import get_current_hr_user
from app.db.session import SessionLocal, get_db
from app.main import app
from app.models import (
    BrokerFirm,
    Claim,
    Client,
    Employee,
    PolicyYear,
    User,
    UserClientAccess,
)
from app.models.claim import ORIGIN_HR
from app.models.policy_year import PolicyYearStatus

FIRM_ID = "hr-auth-firm"
CLIENT_A = "hr-auth-client-a"
CLIENT_B = "hr-auth-client-b"
YEAR_A = "hr-auth-year-a"
YEAR_B = "hr-auth-year-b"
EMPLOYEE_A = "hr-auth-employee-a"
EMPLOYEE_B = "hr-auth-employee-b"
USER_HR_A = "hr-auth-user-a"
USER_HR_OTHER = "hr-auth-user-other"
USER_ADMIN = "hr-auth-user-admin"
CLAIM_A = "hr-auth-claim-a"
CLAIM_OTHER = "hr-auth-claim-other"
CLAIM_B = "hr-auth-claim-b"


def _principal(user_id: str, client_id: str, role: str = "client_hr") -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        broker_firm_id=FIRM_ID,
        client_id=client_id,
        role=cast(Role, role),
        email=f"{user_id}@example.test",
    )


def _claim(claim_id: str, client_id: str, year_id: str, employee_id: str, user_id: str) -> Claim:
    return Claim(
        id=claim_id,
        client_id=client_id,
        policy_year_id=year_id,
        employee_id=employee_id,
        claim_kind="flex",
        flex_category_name="Wellness",
        claim_type="Wellness",
        incurred_date=date(2026, 9, 20),
        provider_name="Demo Provider",
        invoice_number=claim_id,
        amount_claimed=Decimal("25.00"),
        currency="SGD",
        origin=ORIGIN_HR,
        created_by_user_id=user_id,
        intake_meta={"submission_channel": "hr"},
    )


def _cleanup() -> None:
    """Remove only this module's deterministic fixtures, including failed retries."""
    with SessionLocal() as db:
        db.query(Claim).filter(
            Claim.id.in_((CLAIM_A, CLAIM_OTHER, CLAIM_B))
        ).delete(synchronize_session=False)
        db.query(UserClientAccess).filter(
            UserClientAccess.user_id.in_((USER_HR_A, USER_HR_OTHER, USER_ADMIN))
        ).delete(synchronize_session=False)
        db.query(Employee).filter(
            Employee.id.in_((EMPLOYEE_A, EMPLOYEE_B))
        ).delete(synchronize_session=False)
        db.query(PolicyYear).filter(
            PolicyYear.id.in_((YEAR_A, YEAR_B))
        ).delete(synchronize_session=False)
        db.query(User).filter(
            User.id.in_((USER_HR_A, USER_HR_OTHER, USER_ADMIN))
        ).delete(synchronize_session=False)
        db.query(Client).filter(
            Client.id.in_((CLIENT_A, CLIENT_B))
        ).delete(synchronize_session=False)
        db.query(BrokerFirm).filter(BrokerFirm.id == FIRM_ID).delete(
            synchronize_session=False
        )
        db.commit()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, dict[str, CurrentUser]]]:
    _cleanup()
    with SessionLocal() as db:
        db.add(BrokerFirm(id=FIRM_ID, name="HR authorization firm"))
        db.add_all(
            [
                Client(id=CLIENT_A, name="Client A", broker_firm_id=FIRM_ID),
                Client(id=CLIENT_B, name="Client B", broker_firm_id=FIRM_ID),
            ]
        )
        db.flush()
        db.add_all(
            [
                PolicyYear(
                    id=YEAR_A,
                    client_id=CLIENT_A,
                    year=2026,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                    status=PolicyYearStatus.active,
                ),
                PolicyYear(
                    id=YEAR_B,
                    client_id=CLIENT_B,
                    year=2026,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                    status=PolicyYearStatus.active,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                Employee(
                    id=EMPLOYEE_A,
                    client_id=CLIENT_A,
                    policy_year_id=YEAR_A,
                    staff_id="A-001",
                    employee_name="Client A Employee",
                ),
                Employee(
                    id=EMPLOYEE_B,
                    client_id=CLIENT_B,
                    policy_year_id=YEAR_B,
                    staff_id="B-001",
                    employee_name="Client B Employee",
                ),
            ]
        )
        users = [
            (USER_HR_A, "client_hr"),
            (USER_HR_OTHER, "client_hr"),
            (USER_ADMIN, "client_admin"),
        ]
        for user_id, role in users:
            db.add(
                User(
                    id=user_id,
                    email=f"{user_id}@example.test",
                    display_name=user_id,
                    broker_firm_id=FIRM_ID,
                    role=role,
                )
            )
            db.add(UserClientAccess(user_id=user_id, client_id=CLIENT_A))
        db.flush()
        db.add_all(
            [
                _claim(CLAIM_A, CLIENT_A, YEAR_A, EMPLOYEE_A, USER_HR_A),
                _claim(CLAIM_OTHER, CLIENT_A, YEAR_A, EMPLOYEE_A, USER_HR_OTHER),
                _claim(CLAIM_B, CLIENT_B, YEAR_B, EMPLOYEE_B, USER_HR_OTHER),
            ]
        )
        db.commit()

    active = {"user": _principal(USER_HR_A, CLIENT_A)}

    def db_override() -> Iterator[Any]:
        with SessionLocal() as db:
            yield db

    def user_override() -> CurrentUser:
        return active["user"]

    # This suite tests the endpoint's scope, not the shared claim projection.
    monkeypatch.setattr(
        hr_claims,
        "_out",
        lambda _db, claim: {
            "id": claim.id,
            "employee_id": claim.employee_id,
            "created_by_user_id": claim.created_by_user_id,
        },
    )
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_hr_user] = user_override
    with TestClient(app) as client:
        yield client, active
    app.dependency_overrides.clear()
    _cleanup()


def test_client_hr_sees_only_own_claims_and_own_company_employees(
    api: tuple[TestClient, dict[str, CurrentUser]],
) -> None:
    client, _ = api

    employees = client.get("/api/v1/hr/claims/employees")
    claims = client.get("/api/v1/hr/claims")

    assert employees.status_code == 200
    assert [item["id"] for item in employees.json()["items"]] == [EMPLOYEE_A]
    assert claims.status_code == 200
    assert [item["id"] for item in claims.json()["items"]] == [CLAIM_A]


def test_client_admin_sees_all_delegated_claims_but_only_within_company(
    api: tuple[TestClient, dict[str, CurrentUser]],
) -> None:
    client, active = api
    active["user"] = _principal(USER_ADMIN, CLIENT_A, "client_admin")

    claims = client.get("/api/v1/hr/claims")

    assert claims.status_code == 200
    assert {item["id"] for item in claims.json()["items"]} == {CLAIM_A, CLAIM_OTHER}
    assert client.get(f"/api/v1/hr/claims/{CLAIM_B}").status_code == 404


def test_live_grant_revocation_and_non_hr_roles_fail_closed(
    api: tuple[TestClient, dict[str, CurrentUser]],
) -> None:
    client, active = api
    with SessionLocal() as db:
        grant = db.query(UserClientAccess).filter_by(
            user_id=USER_HR_A,
            client_id=CLIENT_A,
        ).one()
        db.delete(grant)
        db.commit()

    assert client.get("/api/v1/hr/claims").status_code == 403

    active["user"] = _principal(USER_HR_A, CLIENT_A, "broker_admin")
    assert client.get("/api/v1/hr/claims").status_code == 403
