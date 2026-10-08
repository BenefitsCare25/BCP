"""Broker staff sign-in and session security against the suite's disposable
database: Microsoft exchange per firm directory, invitation links, password
sign-in with a mandatory authenticator, per-account MFA and firm binding.

Microsoft tokens are stubbed (`verify_entra_token` is covered with real keys in
`test_auth_entra.py`). A stubbed token is the object id, or `<tid>:<oid>` for
a token issued by directory `<tid>`, refused like a real one by any other
directory.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.v1 import broker_auth as endpoint
from app.core import auth, sessions, totp
from app.core import broker_auth as BA
from app.core.entra import EntraAuthError
from app.core.rate_limit import limiter
from app.core.settings import clear_settings_cache, get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import AuthEvent, AuthSession, User

PLATFORM_TID = "3c1e8f2a-5b7d-4e9f-8a1c-2d3e4f5a6b7c"
CLIENT_ID = "6f1c2a9e-3b4d-4c5e-8f60-718293a4b5c6"
# Brokers A and B in `broker_hosts` share one directory, so a person of A
# presenting a valid token on B's host reaches the firm check.
SHARED_TID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"


def _sign_in_mode(mode: str):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("INSPRO_AUTH_MODE", mode)
        patch.setenv("INSPRO_ENTRA_TENANT_ID", PLATFORM_TID)
        patch.setenv("INSPRO_ENTRA_CLIENT_ID", CLIENT_ID)
        patch.delenv("INSPRO_ENTRA_AUDIENCE", raising=False)
        patch.delenv("INSPRO_ENTRA_ISSUER", raising=False)
        patch.delenv("INSPRO_ENTRA_JWKS_URL", raising=False)
        clear_settings_cache()
        yield get_settings()
    clear_settings_cache()


@pytest.fixture
def entra_mode():
    """Entra mode with the platform directory and app registration set."""
    yield from _sign_in_mode("entra")


@pytest.fixture
def mock_mode():
    yield from _sign_in_mode("mock")


def _directory_token(token, _settings, *, tenant_id, jwks=None):
    """Stands in for `verify_entra_token`: `<tid>:<oid>` is a token from
    directory `<tid>`; a bare object id is from the directory asked about."""
    tid, _, oid = token.rpartition(":")
    if tid and tid != tenant_id:
        raise EntraAuthError("tenant mismatch")
    return {"oid": oid, "tid": tenant_id}


@pytest.fixture
def actor(monkeypatch, entra_mode):
    uid = str(uuid4())
    oid = str(uuid4())
    with SessionLocal() as db:
        db.add(
            User(
                id=uid,
                external_id=oid,
                external_tid=PLATFORM_TID,
                email=uid + "@example.test",
                status="active",
                role="broker_admin",
                broker_mfa_required=True,
            )
        )
        db.commit()
    monkeypatch.setattr(
        auth, "verify_entra_token", lambda token, _s, **kw: {"oid": oid, "tid": kw["tenant_id"]}
    )
    return uid, oid


@pytest.fixture
def client():
    return TestClient(app)


def login(client):
    response = client.post("/api/v1/broker/auth/exchange", json={"access_token": "signed-stub"})
    assert response.status_code == 200, response.text
    return response.json()


def header(session):
    return {"Authorization": "Bearer " + session["access_token"]}


def verified(client):
    first = login(client)
    response = client.post("/api/v1/broker/auth/mfa/start", headers=header(first))
    assert response.status_code == 200, response.text
    code = totp._hotp(response.json()["secret"], totp.current_step())
    response = client.post(
        "/api/v1/broker/auth/mfa/confirm", headers=header(first), json={"code": code}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_microsoft_first_factor_cannot_access_business(client, actor):
    session = login(client)
    assert session["mfa_verified"] is False
    assert session["mfa_required"] is True
    result = client.get("/api/v1/me", headers=header(session))
    assert result.status_code == 403
    assert result.json()["detail"]["code"] == "broker_mfa_required"
    assert (
        client.get("/api/v1/me", headers={"Authorization": "Bearer signed-stub"}).status_code == 401
    )


def test_off_policy_skips_authenticator_without_claiming_mfa_verification(client, actor):
    with SessionLocal() as db:
        db.get(User, actor[0]).broker_mfa_required = False
        db.commit()
    session = login(client)
    assert session["mfa_required"] is False
    assert session["mfa_verified"] is False
    assert client.get("/api/v1/me", headers=header(session)).status_code == 200
    with SessionLocal() as db:
        row = db.get(AuthSession, BA.decode(session["access_token"])["sid"])
        assert row.expires_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(hours=11)
    refreshed = client.post("/api/v1/broker/auth/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["mfa_required"] is False
    for path, body in [("start", {}), ("confirm", {"code": "123456"}),
                       ("verify", {"code": "123456"})]:
        response = client.post("/api/v1/broker/auth/mfa/" + path,
                               headers=header(refreshed.json()), json=body)
        assert response.status_code == 409


def test_off_policy_skips_even_if_authenticator_was_previously_enrolled(client, actor):
    completed = verified(client)
    with SessionLocal() as db:
        db.get(User, actor[0]).broker_mfa_required = False
        db.commit()
    session = login(client)
    assert session["mfa_verified"] is False
    assert client.get("/api/v1/me", headers=header(session)).status_code == 200
    status = client.get("/api/v1/broker/auth/mfa", headers=header(session)).json()
    assert status == {"status": "confirmed", "verified": False, "required": False}
    assert completed["recovery_codes"]


def test_admin_policy_change_revokes_sessions_and_preserves_enrollment(client, actor):
    completed = verified(client)

    def set_requirement(required):
        app.dependency_overrides[auth.get_current_user] = lambda: auth.CurrentUser(
            user_id=actor[0], broker_firm_id=None, client_id=None, role="system_admin",
        )
        try:
            result = client.patch("/api/v1/admin/users/" + actor[0],
                                  json={"broker_mfa_required": required})
            assert result.status_code == 200, result.text
            assert result.json()["broker_mfa_required"] is required
        finally:
            app.dependency_overrides.pop(auth.get_current_user, None)

    set_requirement(False)
    assert client.get("/api/v1/me", headers=header(completed)).status_code == 401
    assert client.post("/api/v1/broker/auth/refresh").status_code == 401
    off_session = login(client)
    assert client.get("/api/v1/me", headers=header(off_session)).status_code == 200
    set_requirement(True)
    assert client.get("/api/v1/me", headers=header(off_session)).status_code == 401
    pending = login(client)
    assert pending["mfa_required"] is True
    assert client.get("/api/v1/me", headers=header(pending)).status_code == 403
    result = client.post("/api/v1/broker/auth/mfa/verify", headers=header(pending),
                         json={"code": completed["recovery_codes"][0]})
    assert result.status_code == 200, result.text
    assert client.get("/api/v1/me", headers=header(result.json())).status_code == 200


def test_enrollment_refresh_and_logout_revoke_access(client, actor):
    session = verified(client)
    assert len(session["recovery_codes"]) == 10
    assert client.get("/api/v1/me", headers=header(session)).status_code == 200
    refresh = client.post("/api/v1/broker/auth/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["mfa_verified"] is True
    assert "HttpOnly" in refresh.headers["set-cookie"]
    assert "SameSite=strict" in refresh.headers["set-cookie"]
    assert refresh.headers["cache-control"] == "no-store"
    assert client.post("/api/v1/broker/auth/logout", headers=header(session)).status_code == 204
    assert client.get("/api/v1/me", headers=header(session)).status_code == 401
    assert client.get("/api/v1/me", headers=header(refresh.json())).status_code == 401
    assert client.post("/api/v1/broker/auth/refresh").status_code == 401


def test_confirmation_cannot_restore_a_cookie_spent_by_another_tab(client, actor):
    pending = login(client)
    old_cookie = client.cookies.get(BA.COOKIE)
    started = client.post("/api/v1/broker/auth/mfa/start", headers=header(pending))
    assert client.post("/api/v1/broker/auth/refresh").status_code == 200
    completed = client.post(
        "/api/v1/broker/auth/mfa/confirm",
        headers={**header(pending), "Cookie": f"{BA.COOKIE}={old_cookie}"},
        json={"code": totp._hotp(started.json()["secret"], totp.current_step())},
    )
    assert completed.status_code == 200, completed.text
    assert "set-cookie" not in completed.headers
    refreshed = client.post("/api/v1/broker/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["mfa_verified"] is True
    assert client.get("/api/v1/me", headers=header(refreshed.json())).status_code == 200


@pytest.mark.parametrize("operation", ["exchange", "refresh", "logout"])
def test_cross_origin_authentication_refused(client, actor, operation):
    result = client.post(
        "/api/v1/broker/auth/" + operation,
        headers={"Origin": "https://attacker.invalid"},
        json={"access_token": "signed-stub"},
    )
    assert result.status_code == 403


@pytest.mark.parametrize("kind", ["idle", "absolute", "pending"])
def test_session_expiry_is_enforced(client, actor, kind):
    session = login(client) if kind == "pending" else verified(client)
    claims = BA.decode(session["access_token"])
    with SessionLocal() as db:
        row = db.get(AuthSession, claims["sid"])
        if kind == "idle":
            row.last_seen_at = datetime.now(UTC) - timedelta(minutes=31)
        else:
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/broker/auth/mfa", headers=header(session)).status_code == 401
    assert client.post("/api/v1/broker/auth/refresh").status_code == 401


def test_recovery_code_is_single_use_and_second_factor_required_each_login(client, actor):
    completed = verified(client)
    session = login(client)
    response = client.post(
        "/api/v1/broker/auth/mfa/verify",
        headers=header(session),
        json={"code": completed["recovery_codes"][0]},
    )
    assert response.status_code == 200
    again = login(client)
    response = client.post(
        "/api/v1/broker/auth/mfa/verify",
        headers=header(again),
        json={"code": completed["recovery_codes"][0]},
    )
    assert response.status_code == 401


def test_logout_does_not_revoke_another_login_cookie(client, actor):
    first = verified(client)
    second = login(client)
    assert client.post("/api/v1/broker/auth/logout", headers=header(first)).status_code == 204
    assert client.post("/api/v1/broker/auth/refresh").status_code == 200
    assert client.get("/api/v1/broker/auth/mfa", headers=header(second)).status_code == 200


def test_email_claim_cannot_bind_an_unlinked_account(client, actor, monkeypatch):
    with SessionLocal() as db:
        user = db.get(User, actor[0])
        user.external_id = None
        email = user.email
        db.commit()
    monkeypatch.setattr(
        auth, "verify_entra_token",
        lambda token, _s, **kw: {"oid": actor[1], "email": email, "tid": kw["tenant_id"]},
    )
    assert (
        client.post(
            "/api/v1/broker/auth/exchange", json={"access_token": "signed-stub"}
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        assert db.get(User, actor[0]).external_id is None


def test_refresh_cookie_cannot_cross_auth_surfaces(client, actor):
    with SessionLocal() as db:
        issued = sessions.issue_session(
            db,
            subject_type="user",
            subject_id=actor[0],
            client_id=None,
            broker_firm_id=None,
            absolute_hours=12,
        )
        db.commit()
    client.cookies.set(BA.COOKIE, issued.token, path=BA.COOKIE_PATH)
    assert client.post("/api/v1/broker/auth/refresh").status_code == 401


def test_mfa_rate_limit_is_shared_by_account_across_sessions_and_ips(client, actor, monkeypatch):
    first, second = login(client), login(client)
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    try:
        statuses = []
        for index in range(6):
            other = TestClient(app, client=(f"192.0.2.{index + 1}", 50000))
            statuses.append(
                other.post(
                    "/api/v1/broker/auth/mfa/" + ("confirm" if index % 2 else "verify"),
                    headers=header(first if index % 2 else second),
                    json={"code": "000000"},
                ).status_code
            )
        assert statuses[:5] == [401] * 5
        assert statuses[5] == 429
    finally:
        limiter.reset()


def test_mfa_bucket_uses_only_signed_identity(client, actor):
    signed = login(client)
    buckets = []
    for index in range(2):
        request = Request(
            {
                "type": "http",
                "client": (f"192.0.2.{index}", 80),
                "headers": [
                    (b"authorization", header(signed)["Authorization"].encode()),
                    (b"x-forwarded-for", f"198.51.100.{index}".encode()),
                ],
            }
        )
        buckets.append(endpoint._account_bucket(request))
    assert buckets == ["broker-mfa:" + actor[0]] * 2
    forged = jwt.encode({"sub": actor[0]}, "attacker-key" * 4, algorithm="HS256")
    request = Request(
        {
            "type": "http",
            "client": ("192.0.2.9", 80),
            "headers": [
                (b"authorization", ("Bearer " + forged).encode()),
            ],
        }
    )
    assert endpoint._account_bucket(request) == "broker-mfa-invalid:192.0.2.9"


def test_logout_can_revoke_after_access_token_expires(client, actor):
    session = verified(client)
    claims = BA.decode(session["access_token"])
    claims["iat"] = int((datetime.now(UTC) - timedelta(minutes=11)).timestamp())
    claims["exp"] = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
    expired = {**session, "access_token": jwt.encode(claims, BA.signing_key(), algorithm="HS256")}
    assert client.get("/api/v1/me", headers=header(expired)).status_code == 401
    assert client.post("/api/v1/broker/auth/logout", headers=header(expired)).status_code == 204
    assert client.get("/api/v1/me", headers=header(session)).status_code == 401
    assert client.post("/api/v1/broker/auth/refresh").status_code == 401


def test_production_cookie_and_verified_lifetime(client, actor, monkeypatch):
    from fastapi import Response

    settings = replace(get_settings(), env="prod")
    response = Response()
    with monkeypatch.context() as patch:
        patch.setattr(BA, "get_settings", lambda: settings)
        BA.set_cookie(response, "opaque-test-token", datetime.now(UTC) + timedelta(minutes=5))
    cookie = response.headers["set-cookie"]
    assert all(
        value in cookie for value in ("Secure", "HttpOnly", "SameSite=strict", BA.COOKIE_PATH)
    )
    with SessionLocal() as db:
        issued = sessions.issue_session(
            db,
            subject_type=BA.SUBJECT,
            subject_id=actor[0],
            client_id=None,
            broker_firm_id=None,
            absolute_hours=12,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        row = db.get(AuthSession, issued.session_id)
        session = BA.response_session(db.get(User, actor[0]), row, mfa_required=True)
        db.commit()
    client.cookies.set(BA.COOKIE, issued.token, path=BA.COOKIE_PATH)
    started = client.post("/api/v1/broker/auth/mfa/start", headers=header(session))
    completed = client.post(
        "/api/v1/broker/auth/mfa/confirm",
        headers=header(session),
        json={
            "code": totp._hotp(started.json()["secret"], totp.current_step()),
        },
    )
    assert completed.status_code == 200
    cookie = SimpleCookie(completed.headers["set-cookie"])
    assert 43190 <= int(cookie[BA.COOKIE]["max-age"]) <= 43200
    with SessionLocal() as db:
        row = db.get(AuthSession, issued.session_id)
        assert abs((row.expires_at - row.issued_at).total_seconds() - 12 * 3600) < 1


def test_refresh_reuse_revokes_every_access_token(client, actor):
    first = verified(client)
    cookie = client.cookies.get(BA.COOKIE)
    second = client.post("/api/v1/broker/auth/refresh").json()
    client.cookies.set(BA.COOKIE, cookie, path=BA.COOKIE_PATH, domain="testserver.local")
    assert client.post("/api/v1/broker/auth/refresh").status_code == 401
    assert client.get("/api/v1/me", headers=header(first)).status_code == 401
    assert client.get("/api/v1/me", headers=header(second)).status_code == 401
    with SessionLocal() as db:
        event = (
            db.query(AuthEvent)
            .filter(
                AuthEvent.surface == "broker",
                AuthEvent.subject_id == actor[0],
                AuthEvent.event_type == "token_reuse_detected",
            )
            .one()
        )
        assert event.outcome == "blocked"


def test_identity_binding_is_validated_unique_and_immutable(client, actor):
    admin = auth.CurrentUser(
        user_id="binding-admin", role="system_admin", broker_firm_id=None, client_id=None
    )
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    try:
        with SessionLocal() as db:
            other = User(
                id=str(uuid4()),
                email=str(uuid4()) + "@example.test",
                role="broker_viewer",
                status="active",
            )
            db.add(other)
            db.commit()
            target = other.id
        path = f"/api/v1/admin/users/{target}"
        assert client.patch(path, json={"external_id": "not-a-uuid"}).status_code == 422
        assert client.patch(path, json={"external_id": actor[1]}).status_code == 409
        oid = str(uuid4())
        result = client.patch(path, json={"external_id": oid})
        assert result.status_code == 200, result.text
        assert result.json()["external_id"] == oid
        assert client.patch(path, json={"external_id": str(uuid4())}).status_code == 409
        assert client.patch(path, json={"external_id": oid}).status_code == 200
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)


def test_administrator_role_change_revokes_broker_session(client, actor):
    session = verified(client)
    admin = auth.CurrentUser(
        user_id="session-admin", role="system_admin", broker_firm_id=None, client_id=None
    )
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    try:
        result = client.patch(f"/api/v1/admin/users/{actor[0]}", json={"role": "broker_viewer"})
        assert result.status_code == 200, result.text
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)
    assert client.get("/api/v1/me", headers=header(session)).status_code == 401
    assert client.post("/api/v1/broker/auth/refresh").status_code == 401


@pytest.mark.parametrize("expired", [True, False])
def test_invitation_requires_bound_oid_and_unexpired_grant(client, actor, expired):
    from app.models import BrokerFirm, IdentityProvider
    from app.models.invitation import Invitation

    with SessionLocal() as db:
        firm = BrokerFirm(name="Synthetic broker auth invitation")
        db.add(firm)
        db.flush()
        db.add(IdentityProvider(broker_firm_id=firm.id, kind="entra", enabled=True,
                                entra_tenant_id=PLATFORM_TID))
        user = db.get(User, actor[0])
        user.broker_firm_id = firm.id
        user.status = "invited"
        invitation = Invitation(
            email=user.email,
            broker_firm_id=firm.id,
            role=user.role,
            token=str(uuid4()),
            status="pending",
            invited_by="auth-test",
            expires_at=datetime.now(UTC) + timedelta(minutes=-1 if expired else 1),
        )
        db.add(invitation)
        db.commit()
        invite_id = invitation.id
    result = client.post("/api/v1/broker/auth/exchange", json={"access_token": "signed-stub"})
    assert result.status_code == (403 if expired else 200), result.text
    with SessionLocal() as db:
        assert db.get(User, actor[0]).status == ("invited" if expired else "active")
        assert db.get(Invitation, invite_id).status == ("pending" if expired else "accepted")


def test_bootstrap_requires_binding_and_preserves_existing_identity(actor, monkeypatch):
    import sys

    from scripts import create_system_admin as bootstrap

    monkeypatch.setattr(
        bootstrap, "get_settings", lambda: replace(get_settings(), auth_mode="entra")
    )
    monkeypatch.setattr(sys, "argv", ["create_system_admin", "--email", "new-admin@example.test"])
    with pytest.raises(SystemExit):
        bootstrap.main()
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == "new-admin@example.test").first() is None
        email = db.get(User, actor[0]).email
        issued = sessions.issue_session(
            db,
            subject_type=BA.SUBJECT,
            subject_id=actor[0],
            client_id=None,
            broker_firm_id=None,
            absolute_hours=12,
        )
        db.commit()
    monkeypatch.setattr(
        sys, "argv", ["create_system_admin", "--email", email, "--entra-object-id", actor[1]]
    )
    bootstrap.main()
    with SessionLocal() as db:
        assert db.get(User, actor[0]).role == "system_admin"
        assert db.get(User, actor[0]).external_id == actor[1]
        assert db.get(AuthSession, issued.session_id).revoked_at is not None
    bootstrap.main()  # Idempotent; no duplicate account or binding change.
    monkeypatch.setattr(
        sys, "argv", ["create_system_admin", "--email", email, "--entra-object-id", str(uuid4())]
    )
    with pytest.raises(SystemExit):
        bootstrap.main()
    with SessionLocal() as db:
        assert db.get(User, actor[0]).external_id == actor[1]


def test_bootstrap_records_the_platform_directory(entra_mode, monkeypatch):
    """The master admin signs in through the platform directory: a new binding
    records it, an unrecorded legacy one is stamped, another directory's is refused."""
    import sys

    from scripts import create_system_admin as bootstrap

    new_oid, legacy_oid = str(uuid4()), str(uuid4())
    with SessionLocal() as db:
        db.add_all([
            User(email="legacy-admin@example.test", role="system_admin", status="active",
                 external_id=legacy_oid),
            User(email="elsewhere-admin@example.test", role="broker_admin", status="active",
                 external_id=str(uuid4()), external_tid=SHARED_TID),
        ])
        db.commit()
    for email, oid in (("fresh-admin@example.test", new_oid),
                       ("legacy-admin@example.test", None)):
        argv = ["create_system_admin", "--email", email]
        monkeypatch.setattr(sys, "argv", argv + (["--entra-object-id", oid] if oid else []))
        bootstrap.main()
    monkeypatch.setattr(sys, "argv", ["create_system_admin", "--email",
                                      "elsewhere-admin@example.test"])
    with pytest.raises(SystemExit):
        bootstrap.main()
    with SessionLocal() as db:
        rows = {u.email: u for u in db.query(User).filter(User.email.like("%-admin@example.test"))}
        assert (rows["fresh-admin@example.test"].external_tid,
                rows["fresh-admin@example.test"].external_id) == (PLATFORM_TID, new_oid)
        assert rows["legacy-admin@example.test"].external_tid == PLATFORM_TID
        assert rows["elsewhere-admin@example.test"].role == "broker_admin"
        for row in rows.values():
            db.delete(row)
        db.commit()


def test_invited_platform_admin_activates_without_a_firm(client, actor, monkeypatch):
    """A system_admin belongs to no firm, while its invitation records the firm
    it was issued from. First sign-in must still find that invitation."""
    from app.models import BrokerFirm
    from app.models.invitation import Invitation

    with SessionLocal() as db:
        firm = BrokerFirm(name="Synthetic platform admin invitation")
        db.add(firm)
        db.commit()
        firm_id = firm.id
    oid = str(uuid4())
    app.dependency_overrides[auth.get_current_user] = lambda: auth.CurrentUser(
        user_id="inviting-admin", role="system_admin", broker_firm_id=None, client_id=None
    )
    try:
        invited = client.post("/api/v1/admin/invitations", json={
            "email": str(uuid4()) + "@example.test", "role": "system_admin",
            "broker_firm_id": firm_id, "external_id": oid,
        })
        assert invited.status_code == 201, invited.text
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)
    monkeypatch.setattr(
        auth, "verify_entra_token", lambda token, _s, **kw: {"oid": oid, "tid": kw["tenant_id"]}
    )
    result = client.post("/api/v1/broker/auth/exchange", json={"access_token": "signed-stub"})
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        account = db.get(User, invited.json()["user_id"])
        assert (account.status, account.broker_firm_id) == ("active", None)
        assert (account.external_tid, account.external_id) == (PLATFORM_TID, oid)
        assert db.get(Invitation, invited.json()["id"]).status == "accepted"


def test_platform_admin_writes_name_their_company(client, actor):
    """Reads may default to a company; a write that acts on one must name it,
    while platform surfaces that need no company keep working. The company is
    in the platform owner's firm, where the master admin has standing access."""
    from app.models import BrokerFirm, Client

    with SessionLocal() as db:
        firm = db.query(BrokerFirm).filter(BrokerFirm.is_platform_owner.is_(True)).one_or_none()
        if firm is None:
            firm = BrokerFirm(name="Synthetic selection firm", is_platform_owner=True)
            db.add(firm)
            db.flush()
        company = Client(name="Synthetic selection company", broker_firm_id=firm.id)
        db.add(company)
        account = db.get(User, actor[0])
        account.role, account.broker_firm_id, account.broker_mfa_required = (
            "system_admin", None, False,
        )
        db.commit()
        company_id = company.id
    session = login(client)
    me = client.get("/api/v1/me", headers=header(session))
    assert me.status_code == 200, me.text
    assert me.json()["active_client_id"] is not None
    year = {"start_date": "2036-01-01", "end_date": "2036-12-31"}
    refused = client.post("/api/v1/policy-years", headers=header(session), json=year)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "client_selection_required"
    chosen = client.post(
        "/api/v1/policy-years",
        headers={**header(session), "X-Inspro-Client": company_id},
        json=year,
    )
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["client_id"] == company_id
    platform = client.post(
        "/api/v1/platform/firms", headers=header(session), json={"name": "Synthetic firm"}
    )
    assert platform.status_code == 201, platform.text


# ── A broker session belongs to one broker's host ────────────────────────────


@pytest.fixture
def broker_hosts(monkeypatch, entra_mode):
    """Broker A (staff and client-only hosts) and broker B (staff host), both
    signing in through one Microsoft directory, the platform owner on the
    platform host, a firm admin of A and the master admin. The Entra stub
    treats the presented token as the object id."""
    from app.models import AuthMfa, BrokerFirm, IdentityProvider
    from app.models.platform import TenantDomain

    created: dict[str, list[str]] = {"firms": [], "users": []}
    with SessionLocal() as db:
        owner = db.query(BrokerFirm).filter(BrokerFirm.is_platform_owner.is_(True)).one_or_none()
        if owner is None:
            owner = BrokerFirm(name="Broker auth owner", slug="brk-owner", is_platform_owner=True)
            db.add(owner)
            db.flush()
            created["firms"].append(owner.id)
        firms = {key: BrokerFirm(name=f"Broker {key}", slug=f"brk-{key}") for key in ("a", "b")}
        db.add_all(firms.values())
        db.flush()
        created["firms"] += [firm.id for firm in firms.values()]
        db.add_all([
            TenantDomain(broker_firm_id=firms["a"].id, hostname="staff.brk-a.test",
                         surface="staff", is_primary=True, status="active"),
            TenantDomain(broker_firm_id=firms["a"].id, hostname="portal.brk-a.test",
                         surface="client", is_primary=True, status="active"),
            TenantDomain(broker_firm_id=firms["b"].id, hostname="staff.brk-b.test",
                         surface="staff", is_primary=True, status="active"),
            *(IdentityProvider(broker_firm_id=firm.id, kind="entra", enabled=True,
                               entra_tenant_id=SHARED_TID) for firm in firms.values()),
        ])
        users = {
            "firm_admin": User(external_id=str(uuid4()), external_tid=SHARED_TID,
                               email=str(uuid4()) + "@brk-a.test",
                               status="active", role="firm_admin", broker_firm_id=firms["a"].id),
            "system_admin": User(external_id=str(uuid4()), external_tid=PLATFORM_TID,
                                 email=str(uuid4()) + "@platform.test",
                                 status="active", role="system_admin"),
        }
        db.add_all(users.values())
        db.commit()
        created["users"] = [user.id for user in users.values()]
        ids = {
            "a": firms["a"].id,
            "b": firms["b"].id,
            **{role: (user.id, user.external_id) for role, user in users.items()},
        }
    monkeypatch.setattr(auth, "verify_entra_token", _directory_token)
    try:
        yield ids
    finally:
        with SessionLocal() as db:
            for model, column, values in (
                (AuthSession, AuthSession.subject_id, created["users"]),
                (AuthEvent, AuthEvent.subject_id, created["users"]),
                (AuthMfa, AuthMfa.subject_id, created["users"]),
                (User, User.id, created["users"]),
                (TenantDomain, TenantDomain.broker_firm_id, created["firms"]),
                (IdentityProvider, IdentityProvider.broker_firm_id, created["firms"]),
                (BrokerFirm, BrokerFirm.id, created["firms"]),
            ):
                db.query(model).filter(column.in_(values)).delete(synchronize_session=False)
            db.commit()


def _host(host: str) -> TestClient:
    return TestClient(app, base_url=f"http://{host}")


def _exchange(api: TestClient, oid: str):
    return api.post("/api/v1/broker/auth/exchange", json={"access_token": oid})


def _firm_of(host: str):
    from app.core.tenant_resolution import lookup_firm

    with SessionLocal() as db:
        return lookup_firm(db, host, get_settings())


OTHER_ORGANISATION = {
    "code": "no_access", "message": "This account belongs to a different organisation.",
}


def test_broker_session_is_bound_to_the_hosts_firm(broker_hosts):
    from fastapi import HTTPException

    user_id, oid = broker_hosts["firm_admin"]
    on_a, on_b = _host("staff.brk-a.test"), _host("staff.brk-b.test")
    session = _exchange(on_a, oid)
    assert session.status_code == 200, session.text
    with SessionLocal() as db:
        row = db.get(AuthSession, BA.decode(session.json()["access_token"])["sid"])
        assert row.broker_firm_id == broker_hosts["a"]

    refused = _exchange(on_b, oid)
    assert refused.status_code == 403
    assert refused.json()["detail"] == OTHER_ORGANISATION
    with SessionLocal() as db:
        blocked = db.query(AuthEvent).filter(
            AuthEvent.subject_id == user_id, AuthEvent.outcome == "blocked",
        ).one()
        assert blocked.detail == {"reason": "other_firm", "host": "staff.brk-b.test"}

    assert on_a.get("/api/v1/broker/auth/mfa", headers=header(session.json())).status_code == 200
    assert on_b.get("/api/v1/broker/auth/mfa", headers=header(session.json())).status_code == 401
    # The dependency I2-ROLES wires into every broker API request.
    token = header(session.json())["Authorization"]
    with SessionLocal() as db:
        principal = BA.broker_principal(token, db, firm=_firm_of("staff.brk-a.test"))
        assert (principal.broker_firm_id, principal.role) == (broker_hosts["a"], "firm_admin")
        with pytest.raises(HTTPException) as other:
            BA.broker_principal(token, db, firm=_firm_of("staff.brk-b.test"))
        assert other.value.status_code == 401

    cookie = on_a.cookies.get(BA.COOKIE)
    stolen = on_b.post("/api/v1/broker/auth/refresh", headers={"Cookie": f"{BA.COOKIE}={cookie}"})
    assert stolen.status_code == 401
    bearer = header(session.json())
    assert on_b.post("/api/v1/broker/auth/logout", headers=bearer).status_code == 401
    # Refused unspent: the cookie still refreshes on its own broker's host.
    assert on_a.post("/api/v1/broker/auth/refresh").status_code == 200


def test_master_admin_signs_in_only_on_platform_hosts(broker_hosts):
    _, oid = broker_hosts["system_admin"]
    platform, broker = TestClient(app), _host("staff.brk-a.test")
    session = _exchange(platform, oid)
    assert session.status_code == 200, session.text
    with SessionLocal() as db:
        row = db.get(AuthSession, BA.decode(session.json()["access_token"])["sid"])
        assert row.broker_firm_id is None

    # The master admin's identity lives in the platform directory, which a
    # broker's host never validates against: there it is nobody.
    refused = _exchange(broker, oid)
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "no_access"
    bearer = header(session.json())
    assert platform.get("/api/v1/broker/auth/mfa", headers=bearer).status_code == 200
    assert broker.get("/api/v1/broker/auth/mfa", headers=bearer).status_code == 401


def test_broker_sign_in_is_not_served_on_client_only_hosts(broker_hosts):
    from fastapi import HTTPException

    _, oid = broker_hosts["firm_admin"]
    client_only = _host("portal.brk-a.test")
    assert _exchange(client_only, oid).status_code == 404
    session = _exchange(_host("staff.brk-a.test"), oid)
    bearer = header(session.json())
    assert client_only.get("/api/v1/broker/auth/mfa", headers=bearer).status_code == 404
    with SessionLocal() as db, pytest.raises(HTTPException) as hidden:
        BA.broker_principal(bearer["Authorization"], db, firm=_firm_of("portal.brk-a.test"))
    assert hidden.value.status_code == 404


# ── Sign-in methods per firm: Microsoft directories, invitations, passwords ──
DIRECTORY_A = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
DIRECTORY_B = "7f6e5d4c-3b2a-4f1e-9d8c-7b6a5f4e3d2c"
GOOD_PASSWORD = "Harbour-Lantern-Quietly-42"
BREACHED_PASSWORD = "Breached-Password-Example-99"


@pytest.fixture
def staff_firms(monkeypatch):
    """Firms with their own staff hosts (`staff.<key>.test`):

    - `both`: Microsoft through DIRECTORY_A and passwords; also a client-only
      host `portal.both.test`
    - `local`: passwords only
    - `entra`: Microsoft through DIRECTORY_B only

    The breach check is stubbed: only BREACHED_PASSWORD is in a breach.
    """
    from app.models import AuthCredential, AuthMfa, BrokerFirm, IdentityProvider
    from app.models.invitation import Invitation
    from app.models.platform import TenantDomain

    methods = {"both": (DIRECTORY_A, True), "local": (None, True), "entra": (DIRECTORY_B, False)}
    ids: dict[str, str] = {}
    with SessionLocal() as db:
        for key, (directory, local) in methods.items():
            firm = BrokerFirm(name=f"Sign-in {key} brokers", slug=f"signin-{key}")
            db.add(firm)
            db.flush()
            ids[key] = firm.id
            db.add(TenantDomain(broker_firm_id=firm.id, hostname=f"staff.{key}.test",
                                surface="staff", is_primary=True, status="active"))
            db.add(IdentityProvider(broker_firm_id=firm.id, kind="local", enabled=local))
            if directory:
                db.add(IdentityProvider(broker_firm_id=firm.id, kind="entra", enabled=True,
                                        entra_tenant_id=directory))
        db.add(TenantDomain(broker_firm_id=ids["both"], hostname="portal.both.test",
                            surface="client", is_primary=True, status="active"))
        db.commit()
    monkeypatch.setattr(auth, "verify_entra_token", _directory_token)
    monkeypatch.setattr(endpoint, "is_breached", lambda password: password == BREACHED_PASSWORD)
    try:
        yield ids
    finally:
        firms = list(ids.values())
        with SessionLocal() as db:
            users = [u for (u,) in db.query(User.id).filter(User.broker_firm_id.in_(firms))]
            for model, column, values in (
                (AuthSession, AuthSession.subject_id, users),
                (AuthEvent, AuthEvent.subject_id, users),
                (AuthMfa, AuthMfa.subject_id, users),
                (AuthCredential, AuthCredential.user_id, users),
                (Invitation, Invitation.broker_firm_id, firms),
                (User, User.id, users),
                (TenantDomain, TenantDomain.broker_firm_id, firms),
                (IdentityProvider, IdentityProvider.broker_firm_id, firms),
                (BrokerFirm, BrokerFirm.id, firms),
            ):
                db.query(model).filter(column.in_(values)).delete(synchronize_session=False)
            db.commit()


def _staff(key: str) -> TestClient:
    return _host(f"staff.{key}.test")


def _invitation(
    firm_id: str, *, role: str = "broker_admin", expired: bool = False,
) -> tuple[str, str, str]:
    """An invited account and its pending invitation: (raw token, user id, invitation id)."""
    import secrets

    from app.models.invitation import Invitation

    raw = secrets.token_urlsafe(32)
    email = f"{uuid4()}@invitee.test"
    with SessionLocal() as db:
        user = User(email=email, broker_firm_id=firm_id, role=role, status="invited")
        invitation = Invitation(
            email=email, broker_firm_id=firm_id, role=role, token=BA.hash_invite_token(raw),
            status="pending", invited_by="auth-test",
            expires_at=datetime.now(UTC) + timedelta(days=-1 if expired else 14),
        )
        db.add_all([user, invitation])
        db.commit()
        return raw, user.id, invitation.id


def _local_staff(firm_id: str, *, role: str = "broker_admin") -> tuple[str, str]:
    """An active password account: (user id, email)."""
    from app.core.passwords import hash_password
    from app.models import AuthCredential

    email = f"{uuid4()}@staff.test"
    with SessionLocal() as db:
        user = User(email=email, broker_firm_id=firm_id, role=role, status="active")
        db.add(user)
        db.flush()
        db.add(AuthCredential(user_id=user.id, broker_firm_id=firm_id,
                              password_hash=hash_password(GOOD_PASSWORD)))
        db.commit()
        return user.id, email


def _enrolled(api: TestClient, session: dict) -> dict:
    """Enrol an authenticator in a pending session; returns the verified
    session (with recovery codes)."""
    started = api.post("/api/v1/broker/auth/mfa/start", headers=header(session))
    assert started.status_code == 200, started.text
    confirmed = api.post(
        "/api/v1/broker/auth/mfa/confirm", headers=header(session),
        json={"code": totp._hotp(started.json()["secret"], totp.current_step())},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _accepted(api: TestClient, raw: str) -> dict:
    accepted = api.post("/api/v1/broker/auth/accept-invite",
                        json={"invite_token": raw, "password": GOOD_PASSWORD})
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


INVITATION_INVALID = {
    "code": "invitation_invalid",
    "message": "This invitation link is no longer valid. Ask your administrator for a new one.",
}


def test_microsoft_tokens_are_validated_against_the_hosts_directory(staff_firms, entra_mode):
    """A firm accepts tokens from its own directory only, and an object id is
    an identity only together with its directory."""
    oid = str(uuid4())
    with SessionLocal() as db:
        in_a = User(email=f"{uuid4()}@a.test", broker_firm_id=staff_firms["both"],
                    role="broker_admin", status="active", external_id=oid,
                    external_tid=DIRECTORY_A)
        # The same object id in another directory is a different person.
        in_b = User(email=f"{uuid4()}@b.test", broker_firm_id=staff_firms["entra"],
                    role="broker_viewer", status="active", external_id=oid,
                    external_tid=DIRECTORY_B)
        db.add_all([in_a, in_b])
        db.commit()
        a_id, b_id = in_a.id, in_b.id
    assert _exchange(_staff("both"), f"{DIRECTORY_B}:{oid}").status_code == 401
    own = _exchange(_staff("both"), f"{DIRECTORY_A}:{oid}")
    assert own.status_code == 200, own.text
    assert own.json()["user"]["id"] == a_id
    claims = BA.decode(own.json()["access_token"])
    assert (claims["idp"], claims["oid"]) == ("entra", oid)
    other = _exchange(_staff("entra"), f"{DIRECTORY_B}:{oid}")
    assert other.status_code == 200, other.text
    assert other.json()["user"]["id"] == b_id


def test_microsoft_sign_in_switched_off_is_refused(staff_firms, entra_mode):
    refused = _exchange(_staff("local"), str(uuid4()))
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "sign_in_method_disabled"
    with SessionLocal() as db:
        reasons = [
            (event.detail or {}).get("reason")
            for event in db.query(AuthEvent).filter(AuthEvent.event_type == "login_fail")
        ]
    assert "sign_in_method_disabled" in reasons


def test_legacy_binding_is_stamped_only_for_the_platform_directory(staff_firms, entra_mode):
    """A row bound before directories were recorded belongs to the platform
    directory: accepted (and stamped) for the platform owner's staff with a
    platform-directory token, never for another firm's staff."""
    from fastapi import HTTPException

    from app.models import BrokerFirm

    with SessionLocal() as db:
        owner = db.query(BrokerFirm).filter(BrokerFirm.is_platform_owner.is_(True)).one_or_none()
        created_owner = owner is None
        if owner is None:
            owner = BrokerFirm(name="Legacy owner", slug="legacy-owner", is_platform_owner=True)
            db.add(owner)
            db.flush()
        rows = {
            "owner": User(email=f"{uuid4()}@owner.test", broker_firm_id=owner.id,
                          role="broker_admin", status="active", external_id=str(uuid4())),
            "other": User(email=f"{uuid4()}@a.test", broker_firm_id=staff_firms["both"],
                          role="broker_admin", status="active", external_id=str(uuid4())),
        }
        db.add_all(rows.values())
        db.commit()
        ids = {key: (row.id, row.external_id) for key, row in rows.items()}
        owner_id = owner.id
    try:
        # No firm context: the platform directory.
        with SessionLocal() as db:
            principal = auth._entra_principal("Bearer " + ids["owner"][1], db)
            db.commit()
        assert principal.user_id == ids["owner"][0]
        with SessionLocal() as db, pytest.raises(HTTPException) as refused:
            auth._entra_principal("Bearer " + ids["other"][1], db)
        assert refused.value.detail["code"] == "no_access"
        # On its own firm's host the token is from that firm's directory: the
        # unrecorded binding is not used there either.
        assert _exchange(_staff("both"), f"{DIRECTORY_A}:{ids['other'][1]}").status_code == 403
        with SessionLocal() as db:
            assert db.get(User, ids["owner"][0]).external_tid == PLATFORM_TID
            assert db.get(User, ids["other"][0]).external_tid is None
    finally:
        with SessionLocal() as db:
            db.query(AuthEvent).filter(AuthEvent.subject_id == ids["other"][0]).delete()
            db.query(User).filter(User.id.in_([value[0] for value in ids.values()])).delete()
            if created_owner:
                db.query(BrokerFirm).filter(BrokerFirm.id == owner_id).delete()
            db.commit()


def test_invitation_link_binds_a_microsoft_identity_once(staff_firms, entra_mode):
    from app.models.invitation import Invitation

    raw, user_id, invitation_id = _invitation(staff_firms["both"])
    oid = str(uuid4())
    api = _staff("both")
    unbound = _exchange(api, f"{DIRECTORY_A}:{oid}")
    assert unbound.status_code == 403
    assert unbound.json()["detail"]["code"] == "no_access"
    bound = api.post("/api/v1/broker/auth/exchange", json={
        "access_token": f"{DIRECTORY_A}:{oid}", "invite_token": raw,
    })
    assert bound.status_code == 200, bound.text
    assert bound.json()["user"]["id"] == user_id
    with SessionLocal() as db:
        account = db.get(User, user_id)
        assert (account.status, account.external_tid, account.external_id) == (
            "active", DIRECTORY_A, oid,
        )
        assert db.get(Invitation, invitation_id).status == "accepted"
    # Single use: the link binds nobody else, and the bound person signs in
    # without it from now on.
    reused = api.post("/api/v1/broker/auth/exchange", json={
        "access_token": f"{DIRECTORY_A}:{uuid4()}", "invite_token": raw,
    })
    assert (reused.status_code, reused.json()["detail"]) == (403, INVITATION_INVALID)
    assert _exchange(api, f"{DIRECTORY_A}:{oid}").status_code == 200


@pytest.mark.parametrize("case", ["expired", "other_firm", "company_role", "unknown"])
def test_invalid_invitation_links_bind_nothing(staff_firms, entra_mode, case):
    firm = staff_firms["entra"] if case == "other_firm" else staff_firms["both"]
    raw, user_id, _ = _invitation(
        firm, expired=case == "expired",
        role="client_hr" if case == "company_role" else "broker_viewer",
    )
    refused = _staff("both").post("/api/v1/broker/auth/exchange", json={
        "access_token": f"{DIRECTORY_A}:{uuid4()}",
        "invite_token": "x" * 43 if case == "unknown" else raw,
    })
    assert (refused.status_code, refused.json()["detail"]) == (403, INVITATION_INVALID)
    with SessionLocal() as db:
        account = db.get(User, user_id)
        assert (account.status, account.external_id) == ("invited", None)


def test_password_invitation_then_mandatory_authenticator(staff_firms, entra_mode):
    raw, user_id, _ = _invitation(staff_firms["both"])
    api = _staff("both")
    accepted = api.post("/api/v1/broker/auth/accept-invite", json={
        "invite_token": raw, "password": GOOD_PASSWORD, "display_name": "  Dana Lim  ",
    })
    assert accepted.status_code == 200, accepted.text
    pending = accepted.json()
    assert (pending["mfa_required"], pending["mfa_verified"]) == (True, False)
    assert (pending["user"]["id"], pending["user"]["display_name"]) == (user_id, "Dana Lim")
    claims = BA.decode(pending["access_token"])
    assert claims["idp"] == "local" and "oid" not in claims
    # Until an authenticator is enrolled, only the MFA endpoints answer.
    business = api.get("/api/v1/me", headers=header(pending))
    assert business.json()["detail"]["code"] == "broker_mfa_required"
    assert api.get("/api/v1/broker/auth/mfa", headers=header(pending)).json() == {
        "status": "none", "verified": False, "required": True,
    }
    verified = _enrolled(api, pending)
    assert api.get("/api/v1/me", headers=header(verified)).json()["user_id"] == user_id
    refreshed = api.post("/api/v1/broker/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert BA.decode(refreshed.json()["access_token"])["idp"] == "local"
    # The invitation is spent.
    again = api.post("/api/v1/broker/auth/accept-invite", json={
        "invite_token": raw, "password": GOOD_PASSWORD,
    })
    assert (again.status_code, again.json()["detail"]) == (403, INVITATION_INVALID)

    # Every later sign-in is a password, then a code (TOTP or recovery code).
    signed_out = _staff("both")
    challenged = signed_out.post("/api/v1/broker/auth/login", json={
        "email": pending["user"]["email"].upper(), "password": GOOD_PASSWORD,
    })
    assert challenged.status_code == 200, challenged.text
    assert challenged.json()["mfa_required"] is True
    assert "access_token" not in challenged.json()
    assert BA.COOKIE not in signed_out.cookies
    step = {"challenge_token": challenged.json()["challenge_token"]}
    wrong = signed_out.post("/api/v1/broker/auth/login/mfa", json={**step, "code": "000000"})
    assert wrong.status_code == 401
    assert not isinstance(wrong.json()["detail"], dict)
    elsewhere = _staff("local").post("/api/v1/broker/auth/login/mfa",
                                     json={**step, "code": verified["recovery_codes"][0]})
    assert elsewhere.json()["detail"]["code"] == "challenge_expired"
    session = signed_out.post("/api/v1/broker/auth/login/mfa",
                              json={**step, "code": verified["recovery_codes"][0]})
    assert session.status_code == 200, session.text
    assert session.json()["mfa_verified"] is True
    assert signed_out.get("/api/v1/me", headers=header(session.json())).status_code == 200
    expired = signed_out.post("/api/v1/broker/auth/login/mfa",
                              json={"challenge_token": "not-a-token", "code": "000000"})
    assert (expired.status_code, expired.json()["detail"]["code"]) == (401, "challenge_expired")


def test_accept_invite_enforces_the_password_policy(staff_firms):
    from app.models.invitation import Invitation

    raw, user_id, invitation_id = _invitation(staff_firms["local"])
    api = _staff("local")
    for weak in ("Short-1a", "aaaaaaaaaaaaaaaaaaaa", BREACHED_PASSWORD):
        refused = api.post("/api/v1/broker/auth/accept-invite",
                           json={"invite_token": raw, "password": weak})
        assert refused.status_code == 422, (weak, refused.text)
    with SessionLocal() as db:
        assert db.get(Invitation, invitation_id).status == "pending"
        assert db.get(User, user_id).status == "invited"
    _accepted(api, raw)


def test_accept_invite_needs_password_sign_in(staff_firms):
    raw, user_id, _ = _invitation(staff_firms["entra"])
    refused = _staff("entra").post("/api/v1/broker/auth/accept-invite",
                                   json={"invite_token": raw, "password": GOOD_PASSWORD})
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "sign_in_method_disabled"
    with SessionLocal() as db:
        assert db.get(User, user_id).status == "invited"


def test_password_sign_in_failures_are_generic_and_lock_out(staff_firms):
    _, email = _local_staff(staff_firms["local"])
    api = _staff("local")
    unknown = api.post("/api/v1/broker/auth/login",
                       json={"email": "nobody@staff.test", "password": GOOD_PASSWORD})
    failures = [
        api.post("/api/v1/broker/auth/login", json={"email": email, "password": "wrong"})
        for _ in range(5)
    ]
    assert unknown.status_code == 401
    assert all(r.status_code == 401 and r.json() == unknown.json() for r in failures)
    locked = api.post("/api/v1/broker/auth/login",
                      json={"email": email, "password": GOOD_PASSWORD})
    assert locked.status_code == 423


def test_password_sign_in_is_refused_where_the_firm_does_not_offer_it(staff_firms):
    _, email = _local_staff(staff_firms["entra"])
    api = _staff("entra")
    unknown = api.post("/api/v1/broker/auth/login",
                       json={"email": "nobody@staff.test", "password": GOOD_PASSWORD})
    refused = api.post("/api/v1/broker/auth/login",
                       json={"email": email, "password": GOOD_PASSWORD})
    assert refused.status_code == 401
    assert refused.json() == unknown.json()


def test_password_sessions_are_bound_to_their_firms_host(staff_firms):
    raw, _, _ = _invitation(staff_firms["both"])
    home = _staff("both")
    pending = _accepted(home, raw)
    session = _enrolled(home, pending)
    elsewhere = _staff("local")
    assert elsewhere.get("/api/v1/me", headers=header(session)).status_code == 401
    cookie = home.cookies.get(BA.COOKIE)
    stolen = elsewhere.post("/api/v1/broker/auth/refresh",
                            headers={"Cookie": f"{BA.COOKIE}={cookie}"})
    assert stolen.status_code == 401
    assert elsewhere.post("/api/v1/broker/auth/login", json={
        "email": pending["user"]["email"], "password": GOOD_PASSWORD,
    }).status_code == 401
    # Refused unspent: the cookie still refreshes on its own firm's host.
    assert home.post("/api/v1/broker/auth/refresh").status_code == 200


def test_switching_a_method_off_ends_its_sessions(staff_firms, entra_mode):
    """Turning password sign-in off ends password sessions at once (Microsoft
    sessions stay); requiring the authenticator after Microsoft ends the
    Microsoft sessions that have not passed it."""
    raw, _, _ = _invitation(staff_firms["both"])
    local_api, entra_api = _staff("both"), _staff("both")
    local_session = _enrolled(local_api, _accepted(local_api, raw))
    oid = str(uuid4())
    with SessionLocal() as db:
        db.add(User(email=f"{uuid4()}@a.test", broker_firm_id=staff_firms["both"],
                    role="broker_admin", status="active", external_id=oid,
                    external_tid=DIRECTORY_A))
        db.commit()
    entra_session = _exchange(entra_api, f"{DIRECTORY_A}:{oid}").json()
    assert entra_session["mfa_required"] is False

    def put(body: dict) -> dict:
        app.dependency_overrides[auth.get_current_user] = lambda: auth.CurrentUser(
            user_id="methods-admin", broker_firm_id=staff_firms["both"], client_id=None,
            role="firm_admin",
        )
        try:
            result = _staff("both").put("/api/v1/firm/sign-in-methods", json=body)
            assert result.status_code == 200, result.text
            return result.json()
        finally:
            app.dependency_overrides.pop(auth.get_current_user, None)

    entra = {"enabled": True, "tenant_id": DIRECTORY_A, "require_platform_mfa": False}
    saved = put({"entra": entra, "local": {"enabled": False}})
    assert saved["local"] == {"enabled": False}
    assert local_api.get("/api/v1/me", headers=header(local_session)).status_code == 401
    assert local_api.post("/api/v1/broker/auth/refresh").status_code == 401
    assert entra_api.get("/api/v1/me", headers=header(entra_session)).status_code == 200

    put({"entra": {**entra, "require_platform_mfa": True}, "local": {"enabled": True}})
    assert entra_api.get("/api/v1/me", headers=header(entra_session)).status_code == 401
    again = _exchange(entra_api, f"{DIRECTORY_A}:{oid}").json()
    assert (again["mfa_required"], again["mfa_verified"]) == (True, False)


def test_public_site_describes_the_hosts_sign_in(staff_firms, entra_mode):
    both = _staff("both").get("/api/v1/public/site")
    assert both.status_code == 200
    assert both.headers["cache-control"] == "public, max-age=60"
    site = both.json()
    assert site.pop("brand")["product_name"] == "Inspro"  # no brand of its own
    assert site == {
        "firm": {"name": "Sign-in both brokers", "slug": "signin-both"},
        "staff_sign_in": {
            "entra": {
                "tenant_id": DIRECTORY_A,
                "client_id": CLIENT_ID,
                "authority": f"https://login.microsoftonline.com/{DIRECTORY_A}",
                "scopes": ["openid", "profile", "email", f"api://{CLIENT_ID}/access_as_user"],
            },
            "local": True,
        },
    }
    local = _staff("local").get("/api/v1/public/site").json()
    assert local["staff_sign_in"] == {"entra": None, "local": True}
    # A host serving only the client portals offers no staff sign-in.
    portal = _host("portal.both.test").get("/api/v1/public/site").json()
    portal.pop("brand")
    assert portal == {"firm": {"name": "Sign-in both brokers", "slug": "signin-both"},
                      "staff_sign_in": {"entra": None, "local": False}}


def test_public_site_names_no_firm_for_an_unknown_host(staff_firms, entra_mode):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("INSPRO_FIRM_FALLBACK_DOMAIN", "brokers-fallback.test")
        clear_settings_cache()
        unknown = _host("nobody.brokers-fallback.test").get("/api/v1/public/site")
        known = _host("signin-local.brokers-fallback.test").get("/api/v1/public/site")
    clear_settings_cache()
    assert unknown.status_code == 200
    nobody = unknown.json()
    assert nobody.pop("brand")["product_name"] == "Inspro"
    assert nobody == {"firm": None, "staff_sign_in": {"entra": None, "local": False}}
    assert known.json()["firm"]["slug"] == "signin-local"
    assert known.json()["staff_sign_in"] == {"entra": None, "local": True}


def test_mock_mode_honours_a_broker_session_token(staff_firms, mock_mode):
    """Development: the site offers no sign-in (the SPA uses the demo user), and
    a request with no token is the demo user; a broker session token from
    password sign-in is that account, held to every session check."""
    from app.core.auth import DEMO_USER_ID

    api = _staff("both")
    assert api.get("/api/v1/public/site").json()["staff_sign_in"] == {
        "entra": None, "local": False,
    }
    raw, user_id, _ = _invitation(staff_firms["both"])
    pending = _accepted(api, raw)
    assert api.get("/api/v1/me", headers=header(pending)).json()["detail"]["code"] == (
        "broker_mfa_required"
    )
    session = _enrolled(api, pending)
    assert api.get("/api/v1/me", headers=header(session)).json()["user_id"] == user_id
    assert api.get("/api/v1/me").json()["user_id"] == DEMO_USER_ID
    assert api.post("/api/v1/broker/auth/logout", headers=header(session)).status_code == 204
    assert api.get("/api/v1/me", headers=header(session)).status_code == 401
