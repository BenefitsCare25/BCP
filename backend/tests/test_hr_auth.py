"""HR credential-login surface: provisioning, set-password, login, MFA-less
flow, lockout, refresh + reuse detection, and cross-tenant defense.

The subdomain is the tenant selector; in tests we stand in for it with the
non-prod `X-Inspro-Tenant-Slug` header (see `core.tenancy_host.optional_tenant`).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_hr_auth.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import totp as T  # noqa: E402
from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BrokerFirm, Client  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

DEMO_SLUG = "demo"
FIRM_B = "00000000-0000-0000-0000-0000000hrfb0"
CLIENT_B = "00000000-0000-0000-0000-0000000hrcb0"
BETA_SLUG = "beta"

STRONG_PW = "Zx9!qL2m@Vw8Tr"  # >12 chars, mixed classes, > 60 bits
NEW_PW = "Rp4#kT7n$Bs1Yc"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        demo = s.get(Client, DEMO_CLIENT_ID)
        demo.slug = DEMO_SLUG
        # A second firm + client to prove firm/tenant isolation.
        s.add(BrokerFirm(id=FIRM_B, name="Firm B"))
        s.add(Client(id=CLIENT_B, name="Client B", broker_firm_id=FIRM_B, slug=BETA_SLUG))
        s.commit()
    yield
    with SessionLocal() as s:
        cb = s.get(Client, CLIENT_B)
        if cb:
            s.delete(cb)
        fb = s.get(BrokerFirm, FIRM_B)
        if fb:
            s.delete(fb)
        s.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


def _tenant(slug: str = DEMO_SLUG) -> dict[str, str]:
    return {"X-Inspro-Tenant-Slug": slug}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provision_hr(api: TestClient, email: str, role: str = "client_hr") -> dict:
    """Broker (mock broker_admin) provisions an HR account for the demo client,
    then disables the breach check so set-password doesn't call HIBP in tests."""
    api.put(
        f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy",
        json={"breach_check_enabled": False},
    )
    res = api.post(
        "/api/v1/hr-admin/accounts",
        json={"client_id": DEMO_CLIENT_ID, "email": email, "role": role},
    )
    assert res.status_code == 201, res.text
    return res.json()


# ── Provisioning + set-password + login ────────────────────────────────────────
def test_full_hr_flow_email_and_login_id(api: TestClient):
    acct = _provision_hr(api, "hr.one@democo.test")
    assert acct["status"] == "invited"
    assert acct["hr_login_id"].startswith("HR-")
    assert acct["set_password_token"]

    # Can't log in before setting a password.
    pre = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.one@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    assert pre.status_code == 401

    # Set the password via the single-use token.
    sp = api.post(
        "/api/v1/hr/auth/set-password",
        json={"token": acct["set_password_token"], "password": STRONG_PW},
        headers=_tenant(),
    )
    assert sp.status_code == 200, sp.text
    assert sp.json()["me"]["client_id"] == DEMO_CLIENT_ID
    assert sp.json()["access_token"]

    # The set-password token is single-use.
    reuse = api.post(
        "/api/v1/hr/auth/set-password",
        json={"token": acct["set_password_token"], "password": NEW_PW},
        headers=_tenant(),
    )
    assert reuse.status_code == 401

    # Login by email.
    by_email = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.one@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    assert by_email.status_code == 200, by_email.text
    body = by_email.json()
    assert body["status"] == "authenticated"
    token = body["access_token"]

    # Login by HR login id.
    by_id = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": acct["hr_login_id"], "password": STRONG_PW},
        headers=_tenant(),
    )
    assert by_id.status_code == 200, by_id.text

    # The access token authenticates /hr/auth/me.
    me = api.get("/api/v1/hr/auth/me", headers={**_bearer(token), **_tenant()})
    assert me.status_code == 200
    assert me.json()["email"] == "hr.one@democo.test"


def test_wrong_password_then_lockout(api: TestClient):
    _provision_hr(api, "hr.lock@democo.test")
    acct = api.post(
        "/api/v1/hr-admin/accounts",
        json={"client_id": DEMO_CLIENT_ID, "email": "hr.lock2@democo.test"},
    ).json()
    api.post(
        "/api/v1/hr/auth/set-password",
        json={"token": acct["set_password_token"], "password": STRONG_PW},
        headers=_tenant(),
    )
    # 5 wrong attempts → lockout on the 6th (threshold reached → 423).
    codes = []
    for _ in range(6):
        r = api.post(
            "/api/v1/hr/auth/login",
            json={"identifier": "hr.lock2@democo.test", "password": "wrong-password-x"},
            headers=_tenant(),
        )
        codes.append(r.status_code)
    assert 423 in codes, codes
    # Even the correct password is now blocked while locked.
    locked = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.lock2@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    assert locked.status_code == 423


def test_refresh_and_reuse_detection(api: TestClient):
    acct = api.post(
        "/api/v1/hr-admin/accounts",
        json={"client_id": DEMO_CLIENT_ID, "email": "hr.refresh@democo.test"},
    ).json()
    api.put(
        f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy",
        json={"breach_check_enabled": False},
    )
    api.post(
        "/api/v1/hr/auth/set-password",
        json={"token": acct["set_password_token"], "password": STRONG_PW},
        headers=_tenant(),
    )
    login = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.refresh@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    assert login.status_code == 200
    old_refresh = api.cookies.get("inspro_hr_refresh")
    assert old_refresh

    # Rotate.
    r1 = api.post("/api/v1/hr/auth/refresh", headers=_tenant())
    assert r1.status_code == 200, r1.text
    assert r1.json()["access_token"]

    # Replay the OLD (now-rotated) refresh token → reuse detected, family killed.
    reuse = api.post(
        "/api/v1/hr/auth/refresh",
        headers=_tenant(),
        cookies={"inspro_hr_refresh": old_refresh},
    )
    assert reuse.status_code == 401


# ── Cross-tenant defense ───────────────────────────────────────────────────────
def test_login_rejected_on_other_tenant_subdomain(api: TestClient):
    acct = _provision_hr(api, "hr.iso@democo.test")
    api.post(
        "/api/v1/hr/auth/set-password",
        json={"token": acct["set_password_token"], "password": STRONG_PW},
        headers=_tenant(),
    )
    # Demo HR creds presented on Client B's subdomain → no grant there → 401.
    res = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.iso@democo.test", "password": STRONG_PW},
        headers=_tenant(BETA_SLUG),
    )
    assert res.status_code == 401


def test_access_token_rejected_across_tenant(api: TestClient):
    acct = _provision_hr(api, "hr.tok@democo.test")
    sp = api.post(
        "/api/v1/hr/auth/set-password",
        json={"token": acct["set_password_token"], "password": STRONG_PW},
        headers=_tenant(),
    )
    token = sp.json()["access_token"]
    # Token minted for the demo tenant, used on Client B's subdomain → 404.
    res = api.get(
        "/api/v1/hr/auth/me",
        headers={**_bearer(token), **_tenant(BETA_SLUG)},
    )
    assert res.status_code == 404


def test_unknown_slug_404(api: TestClient):
    res = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "x@y.test", "password": STRONG_PW},
        headers=_tenant("does-not-exist"),
    )
    assert res.status_code == 404


def test_broker_cannot_provision_other_firm_client(api: TestClient):
    # Mock broker_admin belongs to the demo firm; Client B is in Firm B → 404.
    res = api.post(
        "/api/v1/hr-admin/accounts",
        json={"client_id": CLIENT_B, "email": "sneak@beta.test"},
    )
    assert res.status_code == 404


# ── MFA enrolment ──────────────────────────────────────────────────────────────
def _authed_account(api: TestClient, email: str) -> str:
    acct = _provision_hr(api, email)
    sp = api.post(
        "/api/v1/hr/auth/set-password",
        json={"token": acct["set_password_token"], "password": STRONG_PW},
        headers=_tenant(),
    )
    return sp.json()["access_token"]


def _code(secret: str, step_offset: int = 0) -> str:
    return T._hotp(secret, T.current_step() + step_offset)


def test_mfa_enrollment_login_recovery_and_disable(api: TestClient):
    token = _authed_account(api, "hr.mfa@democo.test")
    auth = {**_bearer(token), **_tenant()}

    # Broker must enable 2FA for the HR surface before anyone can enrol.
    api.put(
        f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy",
        json={"mfa_hr_enabled": True},
    )

    # Enrol: start → secret, confirm with a fresh code → recovery codes.
    start = api.post("/api/v1/hr/auth/mfa/enroll/start", headers=auth)
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    assert start.json()["otpauth_uri"].startswith("otpauth://totp/")

    confirm = api.post(
        "/api/v1/hr/auth/mfa/enroll/confirm",
        json={"code": _code(secret)},
        headers=auth,
    )
    assert confirm.status_code == 200, confirm.text
    recovery = confirm.json()["recovery_codes"]
    assert len(recovery) == 10

    # Login now requires the TOTP step.
    login = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.mfa@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    assert login.json()["status"] == "mfa_required"
    challenge = login.json()["challenge_token"]

    # Complete with a TOTP code from a step past the one confirm consumed.
    step2 = api.post(
        "/api/v1/hr/auth/mfa",
        json={"challenge_token": challenge, "code": _code(secret, 1)},
        headers=_tenant(),
    )
    assert step2.status_code == 200, step2.text
    assert step2.json()["status"] == "authenticated"

    # A recovery code also satisfies the challenge (single-use).
    login2 = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.mfa@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    rc = recovery[0]
    ok = api.post(
        "/api/v1/hr/auth/mfa",
        json={"challenge_token": login2.json()["challenge_token"], "code": rc},
        headers=_tenant(),
    )
    assert ok.status_code == 200
    # ...and it can't be reused.
    login3 = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.mfa@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    reused = api.post(
        "/api/v1/hr/auth/mfa",
        json={"challenge_token": login3.json()["challenge_token"], "code": rc},
        headers=_tenant(),
    )
    assert reused.status_code == 401

    # Disable requires the password; then login is single-factor again.
    bad = api.post(
        "/api/v1/hr/auth/mfa/disable", json={"password": "nope-nope-nope"}, headers=auth
    )
    assert bad.status_code == 401
    good = api.post(
        "/api/v1/hr/auth/mfa/disable", json={"password": STRONG_PW}, headers=auth
    )
    assert good.status_code == 200
    after = api.post(
        "/api/v1/hr/auth/login",
        json={"identifier": "hr.mfa@democo.test", "password": STRONG_PW},
        headers=_tenant(),
    )
    assert after.json()["status"] == "authenticated"
