"""Broker MFA/session security against the suite's disposable database."""

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
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models import AuthEvent, AuthSession, User


@pytest.fixture
def actor(monkeypatch):
    uid = str(uuid4())
    oid = str(uuid4())
    with SessionLocal() as db:
        db.add(
            User(
                id=uid,
                external_id=oid,
                email=uid + "@example.test",
                status="active",
                role="broker_admin",
                broker_mfa_required=True,
            )
        )
        db.commit()
    settings = replace(get_settings(), auth_mode="entra")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "verify_entra_token", lambda token, _: {"oid": oid})
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
        auth, "verify_entra_token", lambda token, _: {"oid": actor[1], "email": email}
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
        session = BA.response_session(db.get(User, actor[0]), row)
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
    from app.models import BrokerFirm
    from app.models.invitation import Invitation

    with SessionLocal() as db:
        firm = BrokerFirm(name="Synthetic broker auth invitation")
        db.add(firm)
        db.flush()
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
