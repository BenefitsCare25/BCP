"""Revocation, mandatory MFA and anonymous-auth abuse controls."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core import passwords as PW
from app.core import totp as T
from app.core.auth import DEMO_CLIENT_ID
from app.core.rate_limit import _key_func, limiter
from app.core.settings import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import AuthSession, Client, ClientAuthPolicy, MemberAccount
from scripts.seed_demo import seed

PASSWORD = "Zx9!qL2m@Vw8Tr"
TENANT = {"X-Inspro-Tenant-Slug": "demo"}


@pytest.fixture(scope="module", autouse=True)
def setup():
    seed()
    with SessionLocal() as db:
        db.get(Client, DEMO_CLIENT_ID).slug = "demo"
        policy = db.get(ClientAuthPolicy, DEMO_CLIENT_ID)
        if policy is None:
            policy = ClientAuthPolicy(client_id=DEMO_CLIENT_ID)
            db.add(policy)
        policy.breach_check_enabled = False
        db.commit()


@pytest.fixture
def api():
    with SessionLocal() as db:
        policy = db.get(ClientAuthPolicy, DEMO_CLIENT_ID)
        policy.mfa_hr_enabled = policy.mfa_portal_enabled = False
        policy.mfa_hr_required = policy.mfa_portal_required = False
        policy.session_idle_minutes = 30
        db.commit()
    return TestClient(app)


def member(api, name, client_id=DEMO_CLIENT_ID, slug="demo"):
    with SessionLocal() as db:
        account = MemberAccount(
            client_id=client_id, staff_id=name, email=f"{name}@example.test",
            status="active", password_hash=PW.hash_password(PASSWORD),
            password_updated_at=datetime.now(UTC),
        )
        db.add(account)
        db.commit()
    result = api.post("/api/v1/portal/auth/login", headers={"X-Inspro-Tenant-Slug": slug}, json={
        "identifier": f"{name}@example.test", "password": PASSWORD,
    })
    assert result.status_code == 200, result.text
    return result.json()


def hr(api, name, client_id=DEMO_CLIENT_ID, slug="demo"):
    account = api.post("/api/v1/hr-admin/accounts", json={
        "client_id": client_id, "email": f"{name}@example.test",
    })
    assert account.status_code == 201, account.text
    result = api.post("/api/v1/hr/auth/set-password", headers={"X-Inspro-Tenant-Slug": slug}, json={
        "token": account.json()["set_password_token"], "password": PASSWORD,
    })
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_logout_invalidates_access_and_rotated_family(api, surface):
    result = member(api, "member-logout") if surface == "portal" else hr(api, "hr-logout")
    token = result["token" if surface == "portal" else "access_token"]
    headers = {**TENANT, "Authorization": f"Bearer {token}"}
    target = f"/api/v1/{surface}/auth/" + ("security-status" if surface == "portal" else "me")
    assert api.get(target, headers=headers).status_code == 200
    refreshed = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert refreshed.status_code == 200, refreshed.text
    assert api.post(f"/api/v1/{surface}/auth/logout", headers=TENANT).status_code == 200
    assert api.get(target, headers=headers).status_code == 401
    new_token = refreshed.json()["token" if surface == "portal" else "access_token"]
    assert api.get(target, headers={
        **TENANT, "Authorization": f"Bearer {new_token}",
    }).status_code == 401


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_required_mfa_restricts_api_until_confirmation(api, surface):
    policy = api.put(f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy", json={
        f"mfa_{surface}_required": True,
    })
    assert policy.status_code == 200
    assert policy.json()[f"mfa_{surface}_enabled"] is True
    result = member(api, "member-required") if surface == "portal" else hr(api, "hr-required")
    assert result["mfa_enrollment_required"] is True
    token = result["token" if surface == "portal" else "access_token"]
    headers = {**TENANT, "Authorization": f"Bearer {token}"}
    protected = "/api/v1/portal/me" if surface == "portal" else "/api/v1/hr/claims"
    assert api.get(protected, headers=headers).status_code == 403
    refreshed = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert refreshed.json()["mfa_enrollment_required"] is True
    start = api.post(f"/api/v1/{surface}/auth/mfa/enroll/start", headers=headers, json={})
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    confirmed = api.post(f"/api/v1/{surface}/auth/mfa/enroll/confirm", headers=headers, json={
        "code": T._hotp(secret, T.current_step()),
    })
    assert confirmed.status_code == 200, confirmed.text
    assert len(confirmed.json()["recovery_codes"]) == 10
    assert api.get(protected, headers=headers).status_code != 403
    assert api.post(f"/api/v1/{surface}/auth/mfa/disable", headers=headers, json={
        "password": PASSWORD,
    }).status_code == 403
    refreshed = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert refreshed.json()["mfa_enrollment_required"] is False


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_cookie_auth_rejects_cross_origin(api, surface):
    for operation in ("refresh", "logout"):
        response = api.post(f"/api/v1/{surface}/auth/{operation}", headers={
            **TENANT, "Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site",
        })
        assert response.status_code == 403


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_company_cookies_refresh_and_logout_independently(api, surface):
    slug = f"security-other-{surface}"
    with SessionLocal() as db:
        other = Client(
            name=slug, slug=slug,
            broker_firm_id=db.get(Client, DEMO_CLIENT_ID).broker_firm_id,
        )
        db.add(other)
        db.flush()
        other_id = other.id
        db.add(ClientAuthPolicy(client_id=other_id, breach_check_enabled=False))
        db.commit()
    authenticate = member if surface == "portal" else hr
    authenticate(api, f"{surface}-cookie-first")
    authenticate(api, f"{surface}-cookie-second", other_id, slug)
    cookie_names = [f"inspro_{surface}_refresh_{client}" for client in (DEMO_CLIENT_ID, other_id)]
    assert all(api.cookies.get(name) for name in cookie_names)
    for cookie in api.cookies.jar:
        if cookie.name in cookie_names:
            assert cookie.path == f"/api/v1/{surface}/auth"
            assert cookie._rest["SameSite"] == "strict"
            assert "HttpOnly" in cookie._rest
    other_headers = {"X-Inspro-Tenant-Slug": slug}
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).status_code == 200
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=other_headers).status_code == 200
    assert api.post(f"/api/v1/{surface}/auth/logout", headers=TENANT).status_code == 200
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).status_code == 401
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=other_headers).status_code == 200


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_optional_mfa_does_not_restrict_unenrolled_accounts(api, surface):
    api.put(f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy", json={
        f"mfa_{surface}_enabled": True,
    })
    result = member(api, "member-optional") if surface == "portal" else hr(api, "hr-optional")
    assert result["mfa_enrollment_required"] is False


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_new_required_policy_reauthenticates_existing_enrolled_sessions(api, surface):
    api.put(f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy", json={
        f"mfa_{surface}_enabled": True,
    })
    authenticate = member if surface == "portal" else hr
    result = authenticate(api, f"{surface}-new-policy")
    token = result["token" if surface == "portal" else "access_token"]
    headers = {**TENANT, "Authorization": f"Bearer {token}"}
    start = api.post(f"/api/v1/{surface}/auth/mfa/enroll/start", headers=headers, json={})
    confirm = api.post(f"/api/v1/{surface}/auth/mfa/enroll/confirm", headers=headers, json={
        "code": T._hotp(start.json()["secret"], T.current_step()),
    })
    assert confirm.status_code == 200
    # An earlier password-only family has no proof, even though the user is enrolled.
    from app.core import sessions as SESS
    from app.models.auth import SUBJECT_MEMBER, SUBJECT_USER
    with SessionLocal() as db:
        old = SESS.issue_session(
            db, subject_type=SUBJECT_MEMBER if surface == "portal" else SUBJECT_USER,
            subject_id=result["member"]["id"] if surface == "portal" else result["me"]["user_id"],
            client_id=DEMO_CLIENT_ID, broker_firm_id=None, absolute_hours=12,
        )
        db.commit()
    api.cookies.set(f"inspro_{surface}_refresh_{DEMO_CLIENT_ID}", old.token,
                    domain="testserver.local", path=f"/api/v1/{surface}/auth")
    api.put(f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy", json={
        f"mfa_{surface}_required": True,
    })
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).status_code == 401


def test_employee_password_change_revokes_refresh(api):
    out = member(api, "member-password")
    assert api.post("/api/v1/portal/auth/change-password", headers={
        **TENANT, "Authorization": f"Bearer {out['token']}",
    }, json={"current_password": PASSWORD, "new_password": "Rp4#kT7n$Bs1Yc"}).status_code == 200
    assert api.post("/api/v1/portal/auth/refresh", headers=TENANT).status_code == 401


@pytest.mark.parametrize("delivered", [True, False])
def test_resend_invite_revokes_before_delivery_and_preserves_password_on_failure(
    api, monkeypatch, delivered,
):
    from app.api.v1 import member_accounts as accounts

    out = member(api, f"member-resend-{str(delivered).lower()}")
    headers = {**TENANT, "Authorization": f"Bearer {out['token']}"}

    def send(*args):
        # Even a refresh during the mail-delivery window must fail.
        assert api.post("/api/v1/portal/auth/refresh", headers=TENANT).status_code == 401
        assert api.get("/api/v1/portal/auth/security-status", headers=headers).status_code == 401
        return delivered

    monkeypatch.setattr(accounts, "send_member_invite", send)
    result = api.post(f"/api/v1/member-accounts/{out['member']['id']}/resend-invite")
    assert result.status_code == 200, result.text
    assert result.json()["mail_sent"] is delivered
    assert api.post("/api/v1/portal/auth/refresh", headers=TENANT).status_code == 401
    with SessionLocal() as db:
        account = db.get(MemberAccount, out["member"]["id"])
        assert PW.verify_password(account.password_hash, PASSWORD) is (not delivered)


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_cookie_auth_rejects_malformed_origin(api, surface):
    response = api.post(f"/api/v1/{surface}/auth/refresh", headers={**TENANT, "Origin": "https://["})
    assert response.status_code == 403


def test_employee_idle_expiry_applies_to_access_tokens(api):
    out = member(api, "member-idle")
    sid = jwt.decode(out["token"], get_settings().portal_jwt_secret, algorithms=["HS256"])["sid"]
    with SessionLocal() as db:
        db.get(AuthSession, sid).last_seen_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    assert api.get("/api/v1/portal/auth/security-status", headers={
        **TENANT, "Authorization": f"Bearer {out['token']}",
    }).status_code == 401


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_revoked_root_blocks_a_child_missed_by_concurrent_logout(api, surface):
    authenticate = member if surface == "portal" else hr
    result = authenticate(api, f"{surface}-root-revocation")
    refreshed = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).json()
    from app.core import hr_auth as HR
    token = result["token" if surface == "portal" else "access_token"]
    claims = (jwt.decode(token, get_settings().portal_jwt_secret, algorithms=["HS256"])
              if surface == "portal" else HR._decode_hr_access_token(token))
    with SessionLocal() as db:
        db.get(AuthSession, claims["sid"]).revoked_at = datetime.now(UTC)
        db.commit()
    # Simulates a child committed after the logout update took its snapshot.
    child_token = refreshed["token" if surface == "portal" else "access_token"]
    target = f"/api/v1/{surface}/auth/" + ("security-status" if surface == "portal" else "me")
    assert api.get(target, headers={
        **TENANT, "Authorization": f"Bearer {child_token}",
    }).status_code == 401
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).status_code == 401


def test_auth_rate_key_ignores_untrusted_headers():
    for value in (b"a", b"b"):
        request = Request({"type": "http", "client": ("198.51.100.1", 80), "headers": [
            (b"x-inspro-client", value), (b"x-forwarded-for", value),
        ]})
        assert _key_func(request) == "ip:198.51.100.1"


def test_login_rate_limit_cannot_be_reset_by_headers(api, monkeypatch):
    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(PW, "dummy_verify", lambda password: None)
    limiter.reset()
    try:
        statuses = [api.post("/api/v1/portal/auth/login", headers={
            **TENANT, "X-Inspro-Client": f"bucket-{index}", "X-Forwarded-For": f"192.0.2.{index}",
        }, json={"identifier": "missing@example.test", "password": "wrong"}).status_code
            for index in range(11)]
        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429
    finally:
        limiter.reset()
