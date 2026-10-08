"""Revocation, mandatory MFA and anonymous-auth abuse controls."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from app.core import credentials as CRED
from app.core import passwords as PW
from app.core import sessions as SESS
from app.core import totp as T
from app.core.auth import DEMO_CLIENT_ID
from app.core.rate_limit import _key_func, limiter
from app.core.settings import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    AuthCredential,
    AuthEvent,
    AuthSession,
    Client,
    ClientAuthPolicy,
    MemberAccount,
    User,
)
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
    assert api.post(f"/api/v1/{surface}/auth/logout", headers=headers).status_code == 200
    assert api.get(target, headers=headers).status_code == 401
    new_token = refreshed.json()["token" if surface == "portal" else "access_token"]
    assert api.get(target, headers={
        **TENANT, "Authorization": f"Bearer {new_token}",
    }).status_code == 401


@pytest.mark.parametrize("surface", ["portal", "hr"])
@pytest.mark.parametrize("same_account", [False, True])
def test_logout_revokes_requesting_tab_and_preserves_other_family(api, surface, same_account):
    authenticate = member if surface == "portal" else hr
    name = f"{surface}-tab-a-{int(same_account)}"
    first = authenticate(api, name)
    key = "token" if surface == "portal" else "access_token"
    first_headers = {**TENANT, "Authorization": f"Bearer {first[key]}"}
    # A sibling tab can rotate A's cookie before another login replaces it.
    rotated = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert rotated.status_code == 200
    if same_account:
        second_response = api.post(f"/api/v1/{surface}/auth/login", headers=TENANT, json={
            "identifier": f"{name}@example.test", "password": PASSWORD,
        })
        assert second_response.status_code == 200
        second = second_response.json()
    else:
        second = authenticate(api, f"{surface}-tab-b")
    cookie_name = f"inspro_{surface}_refresh_{DEMO_CLIENT_ID}"
    second_cookie = api.cookies.get(cookie_name)
    response = api.post(f"/api/v1/{surface}/auth/logout", headers=first_headers)
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    assert api.cookies.get(cookie_name) == second_cookie
    target = f"/api/v1/{surface}/auth/" + ("security-status" if surface == "portal" else "me")
    for token in (first[key], rotated.json()[key]):
        assert api.get(target, headers={
            **TENANT, "Authorization": f"Bearer {token}",
        }).status_code == 401
    assert api.get(target, headers={
        **TENANT, "Authorization": f"Bearer {second[key]}",
    }).status_code == 200
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).status_code == 200


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
def test_logout_requires_signed_session_bound_to_subject_surface_and_tenant(api, surface):
    from app.core import hr_auth as HR

    authenticate = member if surface == "portal" else hr
    first = authenticate(api, f"{surface}-logout-validation")
    token = first["token" if surface == "portal" else "access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    key = (HR._derive_key(get_settings(), HR._HR_KEY_LABEL)
           if surface == "hr" else get_settings().portal_jwt_secret)
    tenant_claim = "cid" if surface == "hr" else "client_id"
    cookie_name = f"inspro_{surface}_refresh_{DEMO_CLIENT_ID}"
    original_cookie = api.cookies.get(cookie_name)
    invalid_tokens = [None, "not-a-token", jwt.encode(claims, "wrong-key" * 8, algorithm="HS256")]
    for field, value in (
        ("typ", "member" if surface == "hr" else "hr"),
        (tenant_claim, "another-company"), ("sub", "another-account"),
        ("sid", "missing-session"), ("sid", ["invalid-type"]),
    ):
        invalid_tokens.append(jwt.encode({**claims, field: value}, key, algorithm="HS256"))
    for invalid in invalid_tokens:
        headers = {**TENANT, **({"Authorization": f"Bearer {invalid}"} if invalid else {})}
        response = api.post(f"/api/v1/{surface}/auth/logout", headers=headers)
        assert response.status_code == 401, response.text
        assert "set-cookie" not in response.headers
        assert api.cookies.get(cookie_name) == original_cookie
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).status_code == 200


@pytest.mark.parametrize("surface", ["portal", "hr"])
@pytest.mark.parametrize("with_cookie", [False, True])
def test_logout_accepts_expired_access_without_refresh_and_is_idempotent(api, surface, with_cookie):
    from app.core import hr_auth as HR

    first = (member if surface == "portal" else hr)(
        api, f"{surface}-expired-logout-{int(with_cookie)}",
    )
    token = first["token" if surface == "portal" else "access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["exp"] = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
    key = (HR._derive_key(get_settings(), HR._HR_KEY_LABEL)
           if surface == "hr" else get_settings().portal_jwt_secret)
    expired = jwt.encode(claims, key, algorithm="HS256")
    if not with_cookie:
        api.cookies.clear()
    headers = {**TENANT, "Authorization": f"Bearer {expired}"}
    response = api.post(f"/api/v1/{surface}/auth/logout", headers=headers)
    assert response.status_code == 200, response.text
    assert ("set-cookie" in response.headers) == with_cookie
    assert api.post(f"/api/v1/{surface}/auth/logout", headers=headers).status_code == 200
    target = f"/api/v1/{surface}/auth/" + ("security-status" if surface == "portal" else "me")
    assert api.get(target, headers={
        **TENANT, "Authorization": f"Bearer {token}",
    }).status_code == 401


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
    first = authenticate(api, f"{surface}-cookie-first")
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
    token = first["token" if surface == "portal" else "access_token"]
    assert api.post(f"/api/v1/{surface}/auth/logout", headers={
        **TENANT, "Authorization": f"Bearer {token}",
    }).status_code == 200
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
    refused = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert refused.status_code == 401
    # The family is revoked on the way out, so its cookie goes too: replayed,
    # it would be filed as token reuse.
    assert _clears_refresh_cookie(refused, surface)


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

    def send(*args, **_):
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


def _origin_check(host: str, origin: str, *, scheme: str = "http", **extra: str) -> int:
    """Status `require_same_origin` gives a request to `host` from `origin`."""
    from app.core.cookie_auth import require_same_origin

    headers = [(b"host", host.encode()), (b"origin", origin.encode())] + [
        (name.replace("_", "-").encode(), value.encode()) for name, value in extra.items()
    ]
    request = Request({
        "type": "http", "scheme": scheme, "method": "POST", "path": "/api/v1/portal/auth/refresh",
        "query_string": b"", "headers": headers, "server": (host, 80),
    })
    try:
        require_same_origin(request)
    except HTTPException as exc:
        return exc.status_code
    return 200


def test_cookie_auth_accepts_each_firm_domain_without_listing_it(monkeypatch):
    """Every broker serves the SPA from its own domain; the page's own host is
    same-origin without an `INSPRO_CORS_ORIGINS` entry per domain."""
    from dataclasses import replace

    from app.core import cookie_auth

    assert "https://benefits.brokera.test" not in get_settings().cors_origins
    assert _origin_check("benefits.brokera.test", "http://benefits.brokera.test") == 200
    # TLS ended at the edge: the app sees http for a page served over https.
    assert _origin_check("benefits.brokera.test", "https://benefits.brokera.test") == 200
    assert _origin_check("benefits.brokera.test:443", "https://benefits.brokera.test") == 200
    assert _origin_check("benefits.brokera.test", "https://benefits.brokerb.test") == 403

    prod = replace(get_settings(), env="prod", cors_origins=("https://platform.example",))
    monkeypatch.setattr(cookie_auth, "get_settings", lambda: prod)
    assert _origin_check("benefits.brokera.test", "https://benefits.brokera.test") == 200
    assert _origin_check("platform.example.internal", "https://platform.example") == 200
    # A plaintext page on the same name is not the site in production.
    assert _origin_check("benefits.brokera.test", "http://benefits.brokera.test") == 403


def test_cookie_auth_never_trusts_a_forwarded_host():
    """Only the Host header counts — rewritten behind Front Door from the
    edge-set header the client cannot choose. A forwarded host is client input."""
    assert _origin_check(
        "benefits.brokera.test", "https://attacker.example",
        x_forwarded_host="attacker.example", forwarded="host=attacker.example",
    ) == 403


def test_passive_poll_and_refresh_preserve_idle_deadline(api):
    result = member(api, "passive-enrolment")
    claims = jwt.decode(result["token"], options={"verify_signature": False})
    past = datetime.now(UTC) - timedelta(minutes=10)
    headers = {**TENANT, "Authorization": f"Bearer {result['token']}"}
    with SessionLocal() as db:
        db.get(AuthSession, claims["sid"]).last_seen_at = past
        db.commit()
    # Even older bundles without the passive header must not keep a session alive.
    for _ in range(3):
        assert api.get("/api/v1/portal/enrollment/notices", headers=headers).status_code != 401
    with SessionLocal() as db:
        assert db.get(AuthSession, claims["sid"]).last_seen_at.replace(tzinfo=UTC) == past
    refreshed = api.post("/api/v1/portal/auth/refresh", headers={
        **TENANT, "X-Inspro-Session-Activity": "passive",
    })
    assert refreshed.status_code == 200, refreshed.text
    child = jwt.decode(refreshed.json()["token"], options={"verify_signature": False})
    with SessionLocal() as db:
        assert db.get(AuthSession, child["sid"]).last_seen_at.replace(tzinfo=UTC) == past
    # A deliberate action still advances activity.
    active = {**TENANT, "Authorization": f"Bearer {refreshed.json()['token']}"}
    assert api.get("/api/v1/portal/auth/security-status", headers=active).status_code == 200
    with SessionLocal() as db:
        session = db.get(AuthSession, child["sid"])
        assert session.last_seen_at.replace(tzinfo=UTC) > past
        session.last_seen_at = datetime.now(UTC) - timedelta(minutes=31)
        db.commit()
    assert api.get("/api/v1/portal/enrollment/notices", headers=active).status_code == 401
    assert api.post("/api/v1/portal/auth/refresh", headers={
        **TENANT, "X-Inspro-Session-Activity": "passive",
    }).status_code == 401


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


# ── Increment 1 hardening ──────────────────────────────────────────────────────
def _access(surface, result):
    return result["token" if surface == "portal" else "access_token"]


def _subject_id(surface, result):
    return result["member"]["id"] if surface == "portal" else result["me"]["user_id"]


def _failed_attempts(surface, result):
    with SessionLocal() as db:
        if surface == "portal":
            return db.get(MemberAccount, _subject_id(surface, result)).failed_attempts
        return db.execute(select(AuthCredential).where(
            AuthCredential.user_id == _subject_id(surface, result),
        )).scalar_one().failed_attempts


def _refresh_with(surface, client_id, token):
    """Present `token` as this surface's refresh cookie for `client_id`, from a
    browser that holds nothing else."""
    probe = TestClient(app)
    probe.cookies.set(f"inspro_{surface}_refresh_{client_id}", token,
                      domain="testserver.local", path=f"/api/v1/{surface}/auth")
    return probe.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)


def _unspent(token):
    with SessionLocal() as db:
        row = db.execute(select(AuthSession).where(
            AuthSession.refresh_hash == SESS.hash_refresh(token),
        )).scalar_one()
        return row.rotated_at is None and row.revoked_at is None


def _set_demo_flag(flag, value):
    with SessionLocal() as db:
        setattr(db.get(Client, DEMO_CLIENT_ID), flag, value)
        db.commit()


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_credential_401_is_a_fresh_exception_per_raise(api, surface):
    """A module-level HTTPException re-raised on every failed sign-in grew its
    traceback with each raise, keeping every request's frames and locals —
    plaintext passwords included — alive for the life of the process."""
    from app.api.v1 import hr_auth, portal_auth

    module = portal_auth if surface == "portal" else hr_auth
    first, second = module._invalid(), module._invalid()
    assert first is not second
    assert (first.status_code, first.detail) == (401, "Invalid credentials.")
    assert [name for name, value in vars(module).items() if isinstance(value, HTTPException)] == []
    for _ in range(2):
        response = api.post(f"/api/v1/{surface}/auth/login", headers=TENANT, json={
            "identifier": "nobody@example.test", "password": "Wrong-Password-1!",
        })
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials."}


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_refresh_refuses_another_surfaces_token_without_spending_it(api, surface):
    """Rotation spends a token, so the surface check must come first: HR refresh
    used to rotate an employee's token before refusing it, signing the employee
    out and later revoking their family as a replay."""
    other = "hr" if surface == "portal" else "portal"
    (member if other == "portal" else hr)(api, f"{other}-cross-surface-refresh")
    token = api.cookies.get(f"inspro_{other}_refresh_{DEMO_CLIENT_ID}")
    assert _refresh_with(surface, DEMO_CLIENT_ID, token).status_code == 401
    assert _unspent(token)
    assert api.post(f"/api/v1/{other}/auth/refresh", headers=TENANT).status_code == 200


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_refresh_refuses_another_companys_token_without_spending_it(api, surface):
    slug = f"refresh-other-{surface}"
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
    (member if surface == "portal" else hr)(api, f"{surface}-other-company-refresh", other_id, slug)
    token = api.cookies.get(f"inspro_{surface}_refresh_{other_id}")
    assert _refresh_with(surface, DEMO_CLIENT_ID, token).status_code == 401
    assert _unspent(token)
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers={
        "X-Inspro-Tenant-Slug": slug,
    }).status_code == 200


def test_failure_count_increments_from_the_database_not_a_stale_row(api):
    """Attempts that each loaded the row at count N each wrote N+1, so a burst
    of parallel guesses counted once. The increment now happens in SQL."""
    member(api, "member-atomic-count")
    with SessionLocal() as stale_db, SessionLocal() as other_db:
        stale = stale_db.execute(select(MemberAccount).where(
            MemberAccount.staff_id == "member-atomic-count",
        )).scalar_one()
        assert stale.failed_attempts == 0
        assert CRED.register_failure(other_db, other_db.get(MemberAccount, stale.id)) == 1
        other_db.commit()
        # `stale` still holds 0 in memory; the next failure counts from the stored 1.
        assert CRED.register_failure(stale_db, stale) == 2
        stale_db.commit()
        assert stale.failed_attempts == 2
    with SessionLocal() as db:
        assert db.get(MemberAccount, stale.id).failed_attempts == 2


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_password_reverification_counts_failures_and_honours_lockout(api, surface):
    """A session holder guessing at "confirm your password" is guessing the
    password: each miss counts toward the sign-in lockout, and a locked account
    is refused without a check."""
    name = f"{surface}-reverify-lockout"
    result = (member if surface == "portal" else hr)(api, name)
    headers = {**TENANT, "Authorization": f"Bearer {_access(surface, result)}"}
    disable = f"/api/v1/{surface}/auth/mfa/disable"
    for _ in range(5):
        assert api.post(disable, headers=headers, json={
            "password": "Wrong-Password-1!",
        }).status_code == 401
    assert _failed_attempts(surface, result) == 5
    # Locked: the right password is refused unchecked, here and at sign-in.
    assert api.post(disable, headers=headers, json={"password": PASSWORD}).status_code == 423
    assert api.post(f"/api/v1/{surface}/auth/login", headers=TENANT, json={
        "identifier": f"{name}@example.test", "password": PASSWORD,
    }).status_code == 423
    with SessionLocal() as db:
        failures = db.query(AuthEvent).filter(
            AuthEvent.subject_id == _subject_id(surface, result),
            AuthEvent.event_type == "login_fail",
        ).all()
    assert [event.detail for event in failures] == [
        {"reason": "reauth_failed", "action": "mfa_disable"},
    ] * 5


def test_member_change_password_counts_a_wrong_current_password(api):
    result = member(api, "member-change-reverify")
    response = api.post("/api/v1/portal/auth/change-password", headers={
        **TENANT, "Authorization": f"Bearer {result['token']}",
    }, json={"current_password": "Wrong-Password-1!", "new_password": "Rp4#kT7n$Bs1Yc"})
    assert response.status_code == 401
    assert _failed_attempts("portal", result) == 1


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_switching_a_surface_off_refuses_its_live_sessions(api, surface):
    """The company switch used to stop only new sign-ins (tenant resolution);
    a session already open kept working until it expired."""
    result = (member if surface == "portal" else hr)(api, f"{surface}-kill-switch")
    bearer = {"Authorization": f"Bearer {_access(surface, result)}"}
    target = f"/api/v1/{surface}/auth/" + ("security-status" if surface == "portal" else "me")
    assert api.get(target, headers={**TENANT, **bearer}).status_code == 200
    _set_demo_flag(f"{surface}_enabled", False)
    try:
        # With the company header and without it (the token's company governs).
        for headers in ({**TENANT, **bearer}, bearer):
            response = api.get(target, headers=headers)
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == f"{surface}_disabled"
    finally:
        _set_demo_flag(f"{surface}_enabled", True)
    assert api.get(target, headers={**TENANT, **bearer}).status_code == 200


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_enrolment_start_needs_a_recent_sign_in_or_the_password(api, surface):
    """Binding an authenticator to an account is a takeover step, so a session
    left open must re-prove the password. Straight after sign-in (mandatory
    setup) nothing more is asked."""
    api.put(f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy", json={
        f"mfa_{surface}_enabled": True,
    })
    result = (member if surface == "portal" else hr)(api, f"{surface}-enrol-reauth")
    token = _access(surface, result)
    start = f"/api/v1/{surface}/auth/mfa/enroll/start"
    fresh = api.post(start, headers={**TENANT, "Authorization": f"Bearer {token}"})
    assert fresh.status_code == 200, fresh.text
    sid = jwt.decode(token, options={"verify_signature": False})["sid"]
    with SessionLocal() as db:
        db.get(AuthSession, sid).issued_at = datetime.now(UTC) - timedelta(minutes=11)
        db.commit()
    # A refresh rotates the token but is not a sign-in: the session stays stale.
    refreshed = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert refreshed.status_code == 200, refreshed.text
    headers = {**TENANT, "Authorization": f"Bearer {_access(surface, refreshed.json())}"}
    for body in (None, {}, {"current_password": "Wrong-Password-1!"}):
        response = api.post(start, headers=headers, json=body)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == {
            "code": "reauth_required",
            "message": "Confirm your password to set up two-factor.",
        }
    assert _failed_attempts(surface, result) == 1  # only the wrong password counts
    confirmed = api.post(start, headers=headers, json={"current_password": PASSWORD})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["secret"]


# ── Refresh refusals drop the cookie; a lapsing access token is not a 500 ─────
def _clears_refresh_cookie(response, surface, client_id=DEMO_CLIENT_ID):
    """Whether `response` tells the browser to drop this surface's refresh
    cookie for `client_id`: Max-Age=0 on the cookie's own name and path."""
    name = f"inspro_{surface}_refresh_{client_id}"
    return any(
        header.startswith(f"{name}=") and "Max-Age=0" in header
        and f"Path=/api/v1/{surface}/auth" in header
        for header in response.headers.get_list("set-cookie")
    )


@pytest.mark.parametrize(("surface", "detail"), [
    ("portal", "Session ended. Sign in again."), ("hr", "Session expired."),
])
def test_an_ended_refresh_session_deletes_its_cookie(api, surface, detail):
    """The refusal cleared the cookie on the injected `Response` and then
    raised, and FastAPI drops those headers, so the dead cookie stayed in the
    browser and was replayed by every later refresh."""
    (member if surface == "portal" else hr)(api, f"{surface}-idle-cookie")
    cookie_name = f"inspro_{surface}_refresh_{DEMO_CLIENT_ID}"
    with SessionLocal() as db:
        db.execute(select(AuthSession).where(
            AuthSession.refresh_hash == SESS.hash_refresh(api.cookies.get(cookie_name)),
        )).scalar_one().last_seen_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    response = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert response.status_code == 401
    assert response.json() == {"detail": detail}
    assert _clears_refresh_cookie(response, surface)
    assert api.cookies.get(cookie_name) is None


@pytest.mark.parametrize(("surface", "detail"), [
    ("portal", "Session ended. Sign in again."), ("hr", "Session revoked. Sign in again."),
])
def test_a_replayed_refresh_token_is_recorded_and_its_cookie_deleted(api, surface, detail):
    """Replaying a rotated-out token revokes the family. Both surfaces now say
    so in the auth trail (the portal recorded nothing) and drop the cookie."""
    result = (member if surface == "portal" else hr)(api, f"{surface}-replayed-refresh")
    cookie_name = f"inspro_{surface}_refresh_{DEMO_CLIENT_ID}"
    spent = api.cookies.get(cookie_name)
    assert api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT).status_code == 200
    api.cookies.set(cookie_name, spent, domain="testserver.local", path=f"/api/v1/{surface}/auth")
    response = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert response.status_code == 401
    assert response.json() == {"detail": detail}
    assert _clears_refresh_cookie(response, surface)
    assert api.cookies.get(cookie_name) is None
    with SessionLocal() as db:
        events = db.query(AuthEvent).filter(
            AuthEvent.event_type == "token_reuse_detected",
            AuthEvent.subject_id == _subject_id(surface, result),
        ).all()
        assert [(event.surface, event.client_id, event.outcome) for event in events] == [
            (surface, DEMO_CLIENT_ID, "blocked"),
        ]


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_a_refresh_refused_for_a_disabled_account_deletes_its_cookie(api, surface):
    """The family is revoked on the way to the refusal, so the cookie is dead."""
    result = (member if surface == "portal" else hr)(api, f"{surface}-disabled-refresh")
    with SessionLocal() as db:
        model = MemberAccount if surface == "portal" else User
        db.get(model, _subject_id(surface, result)).status = "disabled"
        db.commit()
    response = api.post(f"/api/v1/{surface}/auth/refresh", headers=TENANT)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials."}
    assert _clears_refresh_cookie(response, surface)
    assert api.cookies.get(f"inspro_{surface}_refresh_{DEMO_CLIENT_ID}") is None


def _lapsed(surface, token):
    """`token` re-signed with an expiry in the past: what the handler holds
    when the token lapses after the auth dependency accepted it."""
    from app.core import hr_auth as HR

    claims = jwt.decode(token, options={"verify_signature": False})
    claims["exp"] = int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())
    key = (HR._derive_key(get_settings(), HR._HR_KEY_LABEL)
           if surface == "hr" else get_settings().portal_jwt_secret)
    return jwt.encode(claims, key, algorithm="HS256")


def _accepted_principal(surface, result, name):
    """The auth dependency, and the principal it returned for this sign-in."""
    from app.core import hr_auth as HR
    from app.core.auth import CurrentUser
    from app.core.portal_auth import CurrentMember, get_current_member

    subject = _subject_id(surface, result)
    if surface == "portal":
        return get_current_member, CurrentMember(
            member_account_id=subject, client_id=DEMO_CLIENT_ID, broker_firm_id=None,
            email=f"{name}@example.test", staff_id=name,
        )
    with SessionLocal() as db:
        firm_id = db.get(User, subject).broker_firm_id
    return HR.get_current_hr_user, CurrentUser(
        user_id=subject, broker_firm_id=firm_id, client_id=DEMO_CLIENT_ID,
        role="client_hr", email=f"{name}@example.test",
    )


@pytest.mark.parametrize("surface", ["portal", "hr"])
def test_an_access_token_lapsing_after_its_dependency_is_not_a_server_error(api, surface):
    """`_session_id` decodes the bearer a second time. With the expiry enforced
    there, a token that lapsed between the dependency and the handler raised
    ExpiredSignatureError: a 500 from enrolment and security-status."""
    api.put(f"/api/v1/hr-admin/clients/{DEMO_CLIENT_ID}/auth-policy", json={
        f"mfa_{surface}_enabled": True,
    })
    name = f"{surface}-lapsed-access"
    result = (member if surface == "portal" else hr)(api, name)
    dependency, principal = _accepted_principal(surface, result, name)
    headers = {**TENANT, "Authorization": f"Bearer {_lapsed(surface, _access(surface, result))}"}
    app.dependency_overrides[dependency] = lambda: principal
    try:
        start = api.post(f"/api/v1/{surface}/auth/mfa/enroll/start", headers=headers, json={})
        assert start.status_code == 200, start.text
        confirm = api.post(f"/api/v1/{surface}/auth/mfa/enroll/confirm", headers=headers, json={
            "code": T._hotp(start.json()["secret"], T.current_step()),
        })
        assert confirm.status_code == 200, confirm.text
        if surface == "portal":
            status = api.get("/api/v1/portal/auth/security-status", headers=headers)
            assert status.status_code == 200, status.text
            assert status.json()["mfa_status"] == "confirmed"
    finally:
        app.dependency_overrides.pop(dependency, None)
    sid = jwt.decode(_access(surface, result), options={"verify_signature": False})["sid"]
    with SessionLocal() as db:
        assert db.get(AuthSession, sid).mfa_verified is True
