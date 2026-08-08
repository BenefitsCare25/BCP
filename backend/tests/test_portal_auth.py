"""Employee-portal OTP auth: provisioning, request-code, verify, token gating."""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_auth.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.credentials import credential_version  # noqa: E402
from app.core.portal_auth import hash_otp_code, issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Client,
    Employee,
    MemberAccount,
    MemberOtpCode,
    PolicyYear,
)
from app.models.member_account import (  # noqa: E402
    MEMBER_STATUS_ACTIVE,
    MEMBER_STATUS_INVITED,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

PY_ACTIVE = "00000000-0000-0000-0000-00000000pa01"
EMP_ALICE = "00000000-0000-0000-0000-00000000pa02"
EMP_NO_EMAIL = "00000000-0000-0000-0000-00000000pa03"
EMP_NO_EMAIL_2 = "00000000-0000-0000-0000-00000000pa04"
ALICE_EMAIL = "alice@acme.test"
DEMO_SLUG = "demo"
# The portal subdomain, stood in for by a header off-prod. `verify` requires it
# for the same reason `/login` does: it routes the Postgres search_path to the
# firm schema the leaver check reads, and it pins which company's account a
# mailed code may be redeemed against.
_TENANT = {"X-Inspro-Tenant-Slug": DEMO_SLUG}


def _broker() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
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
        # The portal resolves its tenant from the subdomain (stood in for by
        # X-Inspro-Tenant-Slug here), so set-password needs a slug on the client.
        session.get(Client, DEMO_CLIENT_ID).slug = DEMO_SLUG
        session.add(
            PolicyYear(
                id=PY_ACTIVE,
                # Year 2027, NOT 2026: seed() looks the demo 2026 policy year up
                # with .one_or_none(), so a second 2026 row would break every
                # later test module's seed() call on the suite-shared DB.
                client_id=DEMO_CLIENT_ID,
                year=2027,
                start_date=date(2027, 2, 1),
                end_date=date(2028, 1, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        session.add(
            Employee(
                id=EMP_ALICE,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_ACTIVE,
                staff_id="S-100",
                employee_name="Alice Tan",
                attribute_values={"email": ALICE_EMAIL, "grade": 12},
                derived_attribute_values={},
                source="csv_import",
                status="active",
            )
        )
        session.add(
            Employee(
                id=EMP_NO_EMAIL,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_ACTIVE,
                staff_id="S-101",
                employee_name="Bob No-Email",
                attribute_values={"grade": 8},
                derived_attribute_values={},
                source="csv_import",
                status="active",
            )
        )
        session.add(
            Employee(
                id=EMP_NO_EMAIL_2,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_ACTIVE,
                staff_id="S-777",
                employee_name="Bea No-Email",
                attribute_values={"grade": 8},
                derived_attribute_values={},
                source="csv_import",
                status="active",
            )
        )
        session.commit()
    yield
    # The suite shares one engine/DB across modules — remove everything this
    # module created so later modules' seed()/fixtures see the baseline state.
    with SessionLocal() as session:
        session.query(MemberAccount).filter(
            MemberAccount.client_id == DEMO_CLIENT_ID
        ).delete()
        py = session.get(PolicyYear, PY_ACTIVE)
        if py is not None:
            session.delete(py)  # cascades employees
        session.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def broker_client() -> TestClient:
    app.dependency_overrides[get_current_user] = _broker
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def anon_client() -> TestClient:
    return TestClient(app)


def _clear_otps() -> None:
    """Reset per-account cooldown/hourly-cap state between tests."""
    with SessionLocal() as session:
        session.query(MemberOtpCode).delete()
        session.commit()


def _request_code(anon: TestClient, email: str = ALICE_EMAIL) -> str | None:
    res = anon.post("/api/v1/portal/auth/request-code", json={"email": email})
    assert res.status_code == 202
    return res.json().get("debug_code")


# ── Provisioning + happy path ────────────────────────────────────────────────


def test_invite_then_otp_sign_in_flow(broker_client: TestClient, anon_client: TestClient):
    res = broker_client.post(f"/api/v1/employees/{EMP_ALICE}/member-account", json={})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["email"] == ALICE_EMAIL
    assert body["staff_id"] == "S-100"
    assert body["status"] == MEMBER_STATUS_INVITED

    # Employee row is stamped with the account binding.
    with SessionLocal() as session:
        emp = session.get(Employee, EMP_ALICE)
        assert emp.member_account_id == body["id"]

    # Inviting mails a ONE-TIME password (no OTP is issued any more) and records
    # delivery — `invite_sent_at` is what the bulk send targets on.
    with SessionLocal() as session:
        acc = session.get(MemberAccount, body["id"])
        assert acc.invite_sent_at is not None
        assert acc.password_hash is not None
        assert acc.invite_expires_at is not None
        assert (
            session.query(MemberOtpCode)
            .filter(MemberOtpCode.member_account_id == acc.id)
            .count()
            == 0
        )

    _clear_otps()
    code = _request_code(anon_client)
    assert code is not None and len(code) == 6  # dev+mock exposes debug_code

    res = anon_client.post(
        "/api/v1/portal/auth/verify",
        json={"email": ALICE_EMAIL, "code": code},
        headers=_TENANT,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    # The mailed password is rotation-due on arrival, so proving the mailbox
    # still can't skip choosing a password — verify hands back the same
    # forced-rotation challenge `/login` would.
    assert out["status"] == "password_reset_required"
    assert out["challenge_token"]

    res = anon_client.post(
        "/api/v1/portal/auth/set-password",
        json={"token": out["challenge_token"], "password": "Chosen-By-Member-42"},
        headers={"X-Inspro-Tenant-Slug": DEMO_SLUG},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["token"]
    assert out["member"]["email"] == ALICE_EMAIL

    # Setting a real password activates the account and retires the invite
    # deadline — leaving it set would expire the password just chosen.
    with SessionLocal() as session:
        acc = session.get(MemberAccount, body["id"])
        assert acc.status == MEMBER_STATUS_ACTIVE
        assert acc.last_sign_in_at is not None
        assert acc.invite_expires_at is None

    # Token works on the portal surface and resolves the member's own row.
    me = anon_client.get(
        "/api/v1/portal/me", headers={"Authorization": f"Bearer {out['token']}"}
    )
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["employee"]["id"] == EMP_ALICE
    assert me_body["policy_year"]["id"] == PY_ACTIVE
    assert me_body["flex_eligible"] is False


def test_create_account_without_roster_email_creates_password_member(
    broker_client: TestClient,
):
    # No email → an email-less password member (system login id + set-password
    # token), not a 400. They sign in with username + password.
    res = broker_client.post(f"/api/v1/employees/{EMP_NO_EMAIL}/member-account", json={})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["email"] is None
    assert body["system_login_id"] and body["system_login_id"].startswith("EM-")
    assert body["set_password_token"]
    assert body["has_password"] is False


def test_create_account_explicit_email_override(broker_client: TestClient):
    res = broker_client.post(
        f"/api/v1/employees/{EMP_NO_EMAIL_2}/member-account",
        json={"email": "Bob@Acme.Test"},
    )
    assert res.status_code == 201
    assert res.json()["email"] == "bob@acme.test"  # normalized


def test_create_duplicate_account_409(broker_client: TestClient):
    res = broker_client.post(
        f"/api/v1/employees/{EMP_ALICE}/member-account",
        json={"email": "alice2@acme.test"},
    )
    assert res.status_code == 409  # staff_id already has an account


def test_invalid_email_422(broker_client: TestClient):
    res = broker_client.post(
        f"/api/v1/employees/{EMP_ALICE}/member-account", json={"email": "not-an-email"}
    )
    assert res.status_code == 422


# ── request-code behaviour ───────────────────────────────────────────────────


def test_request_code_unknown_email_is_enumeration_safe(anon_client: TestClient):
    res = anon_client.post(
        "/api/v1/portal/auth/request-code", json={"email": "nobody@nowhere.test"}
    )
    assert res.status_code == 202
    assert res.json().get("debug_code") is None


def test_request_code_cooldown_suppresses_second_issue(anon_client: TestClient):
    _clear_otps()
    first = _request_code(anon_client)
    assert first is not None
    second = _request_code(anon_client)  # inside the 60s cooldown
    assert second is None


# ── verify behaviour ─────────────────────────────────────────────────────────


def test_verify_wrong_code_401(anon_client: TestClient):
    _clear_otps()
    _request_code(anon_client)
    res = anon_client.post(
        "/api/v1/portal/auth/verify",
        json={"email": ALICE_EMAIL, "code": "000000"},
        headers=_TENANT,
    )
    assert res.status_code == 401


def test_verify_lockout_after_max_attempts(anon_client: TestClient):
    _clear_otps()
    code = _request_code(anon_client)
    assert code is not None
    wrong = "999999" if code != "999999" else "111111"
    for _ in range(5):
        res = anon_client.post(
            "/api/v1/portal/auth/verify",
            json={"email": ALICE_EMAIL, "code": wrong},
            headers=_TENANT,
        )
        assert res.status_code == 401
    # The real code was burned by the failed attempts.
    res = anon_client.post(
        "/api/v1/portal/auth/verify",
        json={"email": ALICE_EMAIL, "code": code},
        headers=_TENANT,
    )
    assert res.status_code == 401


def test_verify_expired_code_401(anon_client: TestClient):
    _clear_otps()
    with SessionLocal() as session:
        account = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        session.add(
            MemberOtpCode(
                member_account_id=account.id,
                code_hash=hash_otp_code("123456"),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        session.commit()
    res = anon_client.post(
        "/api/v1/portal/auth/verify",
        json={"email": ALICE_EMAIL, "code": "123456"},
        headers=_TENANT,
    )
    assert res.status_code == 401


def test_code_single_use(anon_client: TestClient):
    _clear_otps()
    code = _request_code(anon_client)
    ok = anon_client.post(
        "/api/v1/portal/auth/verify",
        json={"email": ALICE_EMAIL, "code": code},
        headers=_TENANT,
    )
    assert ok.status_code == 200
    replay = anon_client.post(
        "/api/v1/portal/auth/verify",
        json={"email": ALICE_EMAIL, "code": code},
        headers=_TENANT,
    )
    assert replay.status_code == 401


# ── token gating ─────────────────────────────────────────────────────────────


def test_disabled_account_token_rejected(broker_client: TestClient, anon_client: TestClient):
    with SessionLocal() as session:
        account = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        account_id, client_id = account.id, account.client_id
    token, _ = issue_member_token(account_id, client_id)

    res = broker_client.patch(
        f"/api/v1/member-accounts/{account_id}", json={"status": "disabled"}
    )
    assert res.status_code == 200

    res = anon_client.get(
        "/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401

    # Disabled accounts can't request codes either (enumeration-safe 202, no code).
    assert _request_code(anon_client) is None

    # Re-enable for any later test.
    res = broker_client.patch(
        f"/api/v1/member-accounts/{account_id}", json={"status": "active"}
    )
    assert res.status_code == 200


def test_garbage_token_401(anon_client: TestClient):
    res = anon_client.get(
        "/api/v1/portal/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert res.status_code == 401


def test_missing_token_401(anon_client: TestClient):
    res = anon_client.get("/api/v1/portal/me")
    assert res.status_code == 401


def test_wrong_typ_token_401(anon_client: TestClient):
    import jwt as pyjwt

    from app.core.settings import get_settings

    with SessionLocal() as session:
        account = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        claims = {
            "sub": account.id,
            "client_id": account.client_id,
            "typ": "user",  # not a member token
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
    token = pyjwt.encode(claims, get_settings().portal_jwt_secret, algorithm="HS256")
    res = anon_client.get(
        "/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401


def test_resend_invite(broker_client: TestClient):
    _clear_otps()
    with SessionLocal() as session:
        account = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        account_id = account.id
    with SessionLocal() as session:
        before = session.get(MemberAccount, account_id).password_hash
    res = broker_client.post(f"/api/v1/member-accounts/{account_id}/resend-invite")
    assert res.status_code == 200
    with SessionLocal() as session:
        acc = session.get(MemberAccount, account_id)
        # A resend issues a NEW one-time password (no OTP), which is why it is a
        # per-employee action the UI confirms — the old password stops working.
        assert acc.password_hash != before
        assert acc.invite_sent_at is not None
        assert acc.invite_expires_at is not None
        assert (
            session.query(MemberOtpCode)
            .filter(MemberOtpCode.member_account_id == account_id)
            .count()
            == 0
        )


def test_list_member_accounts(broker_client: TestClient):
    res = broker_client.get("/api/v1/member-accounts")
    assert res.status_code == 200
    body = res.json()
    emails = {item["email"] for item in body["items"]}
    assert ALICE_EMAIL in emails
    assert body["total"] == len(body["items"])


def test_bulk_invite_never_sends_twice(broker_client: TestClient):
    """The whole point of the feature: pressing it again must not re-email."""
    first = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    )
    assert first.status_code == 200

    # Email-less employees are PROVISIONED (so they appear on the follow-up
    # list and can be handed a link 1:1) but nothing is sent to them.
    # (EMP_NO_EMAIL_2 was given an explicit email by an earlier test, so
    # EMP_NO_EMAIL is the only genuinely email-less employee here.)
    with SessionLocal() as session:
        for emp_id in (EMP_NO_EMAIL,):
            emp = session.get(Employee, emp_id)
            assert emp.member_account_id is not None
            acc = session.get(MemberAccount, emp.member_account_id)
            assert acc.email is None
            assert acc.invite_sent_at is None
            assert acc.system_login_id  # they sign in with this instead

    second = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    )
    assert second.status_code == 200
    body = second.json()
    assert body["queued"] == 0
    assert body["accounts_created"] == 0
    assert body["already_invited"] >= 1
    assert body["no_email"] >= 1


def test_bulk_invite_retries_only_undelivered(broker_client: TestClient):
    """A failed send stays unstamped, so the next run picks up exactly those —
    a retry of a first email, never a second one to anyone already served."""
    with SessionLocal() as session:
        alice = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        alice.invite_sent_at = None  # stand in for a send that failed
        alice.status = MEMBER_STATUS_INVITED
        alice.last_sign_in_at = None
        session.commit()

    res = broker_client.get(
        "/api/v1/member-accounts/rollout", params={"policy_year_id": PY_ACTIVE}
    )
    assert res.status_code == 200
    roll = res.json()
    assert roll["invite_pending"] == 1  # only the undelivered one
    assert roll["no_email"] >= 1
    assert {m["staff_id"] for m in roll["needs_attention"]} >= {"S-101"}

    res = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    )
    assert res.json()["queued"] == 1

    with SessionLocal() as session:
        alice = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        assert alice.invite_sent_at is not None


def test_shared_roster_email_is_reported_not_provisioned(broker_client: TestClient):
    """Two employees on one address: the second is REPORTED, never attached to
    the first one's mailbox. Real CDL data has this, and provisioning it blindly
    both violated the (client, email) uniqueness constraint AND would have sent
    one member's credentials — and so their benefits — to a colleague."""
    shared = "shared.mailbox@acme.test"
    with SessionLocal() as session:
        for emp_id in (EMP_NO_EMAIL, EMP_NO_EMAIL_2):
            emp = session.get(Employee, emp_id)
            emp.attribute_values = {**emp.attribute_values, "email": shared}
            emp.member_account_id = None
        session.query(MemberAccount).filter(
            MemberAccount.staff_id.in_(["S-101", "S-777"])
        ).delete(synchronize_session=False)
        session.commit()

    res = broker_client.get(
        "/api/v1/member-accounts/rollout", params={"policy_year_id": PY_ACTIVE}
    )
    roll = res.json()
    assert roll["duplicate"] == 1
    dup = [m for m in roll["needs_attention"] if m["reason"] == "duplicate"]
    assert len(dup) == 1 and dup[0]["email"] == shared

    # The run must not 500 on the constraint, and must leave the loser alone.
    res = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    )
    assert res.status_code == 200, res.text
    assert res.json()["duplicate"] == 1
    with SessionLocal() as session:
        holders = (
            session.query(MemberAccount).filter(MemberAccount.email == shared).all()
        )
        assert len(holders) == 1  # exactly one employee owns the address

    # And on the NEXT read — now that the colleague's account exists — the loser
    # must still be reported as a duplicate. Resolving an employee to an account
    # merely because it shares their email adopts a COLLEAGUE's account: the
    # employee is then counted as covered while their colleague's mailbox is
    # treated as theirs.
    roll = broker_client.get(
        "/api/v1/member-accounts/rollout", params={"policy_year_id": PY_ACTIVE}
    ).json()
    assert roll["duplicate"] == 1
    assert broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    ).json()["duplicate"] == 1

    # Restore the fixture for any later test in this module.
    with SessionLocal() as session:
        for emp_id in (EMP_NO_EMAIL, EMP_NO_EMAIL_2):
            emp = session.get(Employee, emp_id)
            attrs = dict(emp.attribute_values)
            attrs.pop("email", None)
            emp.attribute_values = attrs
            emp.member_account_id = None
        session.query(MemberAccount).filter(
            MemberAccount.staff_id.in_(["S-101", "S-777"])
        ).delete(synchronize_session=False)
        session.commit()


def test_rollout_counts_match_the_send(broker_client: TestClient):
    """The number on the button and the number acted on come from one
    classification — if they can drift, the card quietly under-reports."""
    res = broker_client.get(
        "/api/v1/member-accounts/rollout", params={"policy_year_id": PY_ACTIVE}
    )
    roll = res.json()
    assert (
        roll["invite_pending"]
        + roll["invited"]
        + roll["signed_in"]
        + roll["no_email"]
        + roll["duplicate"]
        + roll["disabled"]
        == roll["employees_total"]
    )
    queued = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    ).json()["queued"]
    assert queued == roll["invite_pending"]


def test_verify_activates_only_on_success(anon_client: TestClient):
    """A failed verify must not activate an invited account."""
    with SessionLocal() as session:
        account = MemberAccount(
            client_id=DEMO_CLIENT_ID,
            email="carol@acme.test",
            staff_id="S-102",
            status=MEMBER_STATUS_INVITED,
        )
        session.add(account)
        session.commit()
        account_id = account.id
    res = anon_client.post(
        "/api/v1/portal/auth/verify",
        json={"email": "carol@acme.test", "code": "123456"},
        headers=_TENANT,
    )
    assert res.status_code == 401
    with SessionLocal() as session:
        assert session.get(MemberAccount, account_id).status == MEMBER_STATUS_INVITED


def test_disabled_status_patch_validation(broker_client: TestClient):
    with SessionLocal() as session:
        account = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        account_id = account.id
    res = broker_client.patch(
        f"/api/v1/member-accounts/{account_id}", json={"status": "invited"}
    )
    assert res.status_code == 422


def test_token_survives_reissue_and_encodes_client(anon_client: TestClient):
    with SessionLocal() as session:
        account = (
            session.query(MemberAccount)
            .filter(MemberAccount.email == ALICE_EMAIL)
            .one()
        )
        account.status = MEMBER_STATUS_ACTIVE
        session.commit()
        account_id, client_id = account.id, account.client_id
        # Issuing an invite bumps `password_updated_at`, which IS the credential
        # version — so a token must be minted against the current one. (That is
        # the intended effect: a re-issued credential evicts older sessions.)
        version = credential_version(account)
    token, expires_at = issue_member_token(account_id, client_id, version)
    assert expires_at > datetime.now(UTC)
    res = anon_client.get(
        "/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert res.json()["member"]["id"] == account_id
