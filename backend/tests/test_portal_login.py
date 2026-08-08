"""Employee-portal credential login (username + password) + optional TOTP.

Members sign in with a username (email / system-generated id / staff id) that
the broker configures, plus an Argon2id password. Verified on the portal
subdomain (stood in for by X-Inspro-Tenant-Slug in tests).
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_login.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import totp as T  # noqa: E402
from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, ClientAuthPolicy, Employee, PolicyYear  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-00000000pl01"
EMP_EMAILLESS = "00000000-0000-0000-0000-00000000pl02"
STRONG_PW = "Zx9!qL2m@Vw8Tr"
DEMO_SLUG = "demo"


@pytest.fixture(scope="module", autouse=True)
def _setup():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.get(Client, DEMO_CLIENT_ID).slug = DEMO_SLUG
        # Get-or-create: the suite shares one DB, so another module may have
        # already made a policy row for the demo client.
        pol = s.get(ClientAuthPolicy, DEMO_CLIENT_ID)
        if pol is None:
            pol = ClientAuthPolicy(client_id=DEMO_CLIENT_ID)
            s.add(pol)
        pol.breach_check_enabled = False
        pol.mfa_portal_enabled = False
        s.add(
            PolicyYear(
                id=PY, client_id=DEMO_CLIENT_ID, year=2031,
                start_date=date(2031, 1, 1), end_date=date(2031, 12, 31),
                status=PolicyYearStatus.active,
            )
        )
        s.flush()
        s.add(
            Employee(
                id=EMP_EMAILLESS, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="S-900", employee_name="Pat No-Email",
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status="active",
            )
        )
        s.commit()
    yield
    with SessionLocal() as s:
        from app.models import MemberAccount

        s.query(MemberAccount).filter(
            MemberAccount.client_id == DEMO_CLIENT_ID
        ).delete()
        py = s.get(PolicyYear, PY)
        if py:
            s.delete(py)  # cascades employees
        pol = s.get(ClientAuthPolicy, DEMO_CLIENT_ID)
        if pol:
            pol.mfa_portal_enabled = False  # leave the row; reset the flag we set
        s.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


def _tenant(slug: str = DEMO_SLUG) -> dict[str, str]:
    return {"X-Inspro-Tenant-Slug": slug}


def _provision_emailless(api: TestClient) -> dict:
    res = api.post(f"/api/v1/employees/{EMP_EMAILLESS}/member-account", json={})
    assert res.status_code == 201, res.text
    return res.json()


def test_emailless_member_set_password_then_login(api: TestClient):
    acct = _provision_emailless(api)
    assert acct["email"] is None
    login_id = acct["system_login_id"]
    staff_id = "S-900"

    # Can't log in before a password exists.
    pre = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": staff_id, "password": STRONG_PW},
        headers=_tenant(),
    )
    assert pre.status_code == 401

    # Member redeems the set-password token on the portal.
    sp = api.post(
        "/api/v1/portal/auth/set-password",
        json={"token": acct["set_password_token"], "password": STRONG_PW},
        headers=_tenant(),
    )
    assert sp.status_code == 200, sp.text
    assert sp.json()["token"]

    # Log in by STAFF ID.
    by_staff = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": staff_id, "password": STRONG_PW},
        headers=_tenant(),
    )
    assert by_staff.status_code == 200, by_staff.text
    assert by_staff.json()["token"]

    # Log in by the broker-generated SYSTEM ID.
    by_id = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": login_id, "password": STRONG_PW},
        headers=_tenant(),
    )
    assert by_id.status_code == 200, by_id.text

    # The member token authenticates the portal.
    token = by_id.json()["token"]
    me = api.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_broker_direct_set_password(api: TestClient):
    # The member was provisioned in the previous test; the broker resets the
    # password directly (the email-less path).
    account_id = api.get("/api/v1/member-accounts").json()["items"][0]["id"]
    res = api.post(
        f"/api/v1/member-accounts/{account_id}/set-password",
        json={"password": "Nn6@rT3k$Ws2Yc"},
    )
    assert res.status_code == 200, res.text
    login = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": "S-900", "password": "Nn6@rT3k$Ws2Yc"},
        headers=_tenant(),
    )
    assert login.status_code == 200


def test_wrong_password_lockout(api: TestClient):
    codes = []
    for _ in range(6):
        r = api.post(
            "/api/v1/portal/auth/login",
            json={"identifier": "S-900", "password": "definitely-wrong"},
            headers=_tenant(),
        )
        codes.append(r.status_code)
    assert 423 in codes, codes


def test_login_rejected_on_unknown_tenant(api: TestClient):
    res = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": "S-900", "password": STRONG_PW},
        headers=_tenant("does-not-exist"),
    )
    assert res.status_code == 404


def test_portal_mfa_enrol_and_login(api: TestClient):
    # Enable portal 2FA for the tenant.
    with SessionLocal() as s:
        s.get(ClientAuthPolicy, DEMO_CLIENT_ID).mfa_portal_enabled = True
        s.commit()

    # Fresh password (previous test locked/rotated it) via broker direct-set.
    lst = api.get("/api/v1/member-accounts").json()
    account_id = lst["items"][0]["id"]
    api.post(
        f"/api/v1/member-accounts/{account_id}/set-password",
        json={"password": STRONG_PW},
    )
    login = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": "S-900", "password": STRONG_PW},
        headers=_tenant(),
    )
    token = login.json()["token"]
    auth = {"Authorization": f"Bearer {token}", **_tenant()}

    start = api.post("/api/v1/portal/auth/mfa/enroll/start", headers=auth)
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    confirm = api.post(
        "/api/v1/portal/auth/mfa/enroll/confirm",
        json={"code": T._hotp(secret, T.current_step())},
        headers=auth,
    )
    assert confirm.status_code == 200
    assert len(confirm.json()["recovery_codes"]) == 10

    # Login now requires the TOTP step.
    step1 = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": "S-900", "password": STRONG_PW},
        headers=_tenant(),
    )
    assert step1.json()["status"] == "mfa_required"
    step2 = api.post(
        "/api/v1/portal/auth/mfa",
        json={
            "challenge_token": step1.json()["challenge_token"],
            "code": T._hotp(secret, T.current_step() + 1),
        },
        headers=_tenant(),
    )
    assert step2.status_code == 200, step2.text
    assert step2.json()["token"]


def test_otp_verify_does_not_bypass_mfa(api: TestClient):
    """The emailed code is the FIRST factor, not a whole sign-in.

    Runs after `test_portal_mfa_enrol_and_login`, so the account already has
    portal 2FA enabled and a confirmed authenticator. A correct OTP must end in
    an `mfa_required` challenge — returning a member token here let anyone with
    mailbox access skip the second factor entirely.
    """
    from app.models import MemberAccount

    email = "otp-mfa-probe@example.com"
    with SessionLocal() as s:
        account = s.get(MemberAccount, _account_id(api))
        assert account is not None
        account.email = email
        s.commit()

    req = api.post("/api/v1/portal/auth/request-code", json={"email": email})
    assert req.status_code == 202, req.text
    code = req.json()["debug_code"]
    assert code, "dev+mock should surface the code for local sign-in"

    res = api.post(
        "/api/v1/portal/auth/verify",
        json={"email": email, "code": code},
        headers=_tenant(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("status") == "mfa_required", body
    assert body.get("challenge_token")
    assert "token" not in body


def test_password_change_evicts_existing_member_tokens(api: TestClient):
    """A reset must actually end other sessions.

    Member tokens are stateless JWTs with no `auth_sessions` row to revoke, so
    without the credential-version claim a phished password stayed usable for
    the token's full TTL and the reset gave no containment.
    """
    account_id = _account_id(api)
    with SessionLocal() as s:
        s.get(ClientAuthPolicy, DEMO_CLIENT_ID).mfa_portal_enabled = False
        s.commit()
    api.post(
        f"/api/v1/member-accounts/{account_id}/set-password",
        json={"password": STRONG_PW},
    )
    login = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": "S-900", "password": STRONG_PW},
        headers=_tenant(),
    )
    token = login.json()["token"]
    auth = {"Authorization": f"Bearer {token}", **_tenant()}
    assert api.get("/api/v1/portal/me", headers=auth).status_code == 200

    # Broker resets the password — the old token must stop working.
    api.post(
        f"/api/v1/member-accounts/{account_id}/set-password",
        json={"password": "Qw7#tR4p!Lz2Nk"},
    )
    assert api.get("/api/v1/portal/me", headers=auth).status_code == 401


def _account_id(api: TestClient) -> str:
    return api.get("/api/v1/member-accounts").json()["items"][0]["id"]


def test_broker_direct_set_password_enforces_breach_check(api, monkeypatch):
    """The broker-direct path must apply the same HIBP gate as the portal."""
    import app.api.v1.member_accounts as MA

    monkeypatch.setattr(MA, "is_breached", lambda pw: True)
    with SessionLocal() as s:
        s.get(ClientAuthPolicy, DEMO_CLIENT_ID).breach_check_enabled = True
        s.commit()
    try:
        res = api.post(
            f"/api/v1/member-accounts/{_account_id(api)}/set-password",
            json={"password": "Zx9!qL2m@Vw8Tr-notbreached-but-forced"},
        )
        assert res.status_code == 422, res.text
        assert "breach" in res.text.lower()
    finally:
        with SessionLocal() as s:
            s.get(ClientAuthPolicy, DEMO_CLIENT_ID).breach_check_enabled = False
            s.commit()


def test_member_forced_rotation_challenges(api):
    from datetime import UTC, datetime, timedelta

    from app.models import MemberAccount

    with SessionLocal() as s:
        s.get(ClientAuthPolicy, DEMO_CLIENT_ID).password_rotation_days = 30
        s.commit()
    account_id = _account_id(api)
    # Broker-direct set-password now stamps a rotation deadline.
    api.post(
        f"/api/v1/member-accounts/{account_id}/set-password",
        json={"password": STRONG_PW},
    )
    with SessionLocal() as s:
        acct = s.get(MemberAccount, account_id)
        assert acct.must_rotate_after is not None
        acct.must_rotate_after = datetime.now(UTC) - timedelta(days=1)
        s.commit()

    login = api.post(
        "/api/v1/portal/auth/login",
        json={"identifier": "S-900", "password": STRONG_PW},
        headers=_tenant(),
    ).json()
    assert login["status"] == "password_reset_required"
    assert login.get("challenge_token")
    with SessionLocal() as s:
        s.get(ClientAuthPolicy, DEMO_CLIENT_ID).password_rotation_days = None
        s.commit()
