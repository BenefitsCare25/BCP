"""Employee-portal accounts: provisioning, invite → password sign-in, token gating."""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_auth.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import passwords as PW  # noqa: E402
from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.credentials import credential_version  # noqa: E402
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Client,
    Employee,
    MemberAccount,
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
# What Alice chooses at set-password in the happy-path test below.
ALICE_PASSWORD = "Chosen-By-Member-42"
DEMO_SLUG = "demo"
# The portal subdomain, stood in for by a header off-prod. The sign-in routes
# require it: it routes the Postgres search_path to the firm schema the leaver
# check reads, and it scopes the username lookup to ONE company.
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


def _login(anon: TestClient, identifier: str, password: str):
    return anon.post(
        "/api/v1/portal/auth/login",
        json={"identifier": identifier, "password": password},
        headers=_TENANT,
    )


# ── Provisioning + happy path ────────────────────────────────────────────────


def test_invite_then_password_sign_in_flow(
    broker_client: TestClient, anon_client: TestClient, monkeypatch
):
    from app.api.v1 import member_accounts

    # Capture the mailed one-time password — the only copy that ever exists.
    mailed: list[str] = []

    def _send(account, password, slug, login_source=None, **_) -> bool:
        mailed.append(password)
        return True

    monkeypatch.setattr(member_accounts, "send_member_invite", _send)
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

    # Inviting mails a ONE-TIME password and records delivery —
    # `invite_sent_at` is what the bulk send targets on.
    assert len(mailed) == 1
    with SessionLocal() as session:
        acc = session.get(MemberAccount, body["id"])
        assert acc.invite_sent_at is not None
        assert acc.password_hash is not None
        assert PW.verify_password(acc.password_hash, mailed[0])
        assert acc.invite_expires_at is not None

    res = _login(anon_client, ALICE_EMAIL, mailed[0])
    assert res.status_code == 200, res.text
    out = res.json()
    # The mailed password is rotation-due on arrival, so it cannot open a
    # session on its own — `/login` hands back the forced-rotation challenge.
    assert out["status"] == "password_reset_required"
    assert out["challenge_token"]
    assert "token" not in out

    res = anon_client.post(
        "/api/v1/portal/auth/set-password",
        json={"token": out["challenge_token"], "password": ALICE_PASSWORD},
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

    # Disabled accounts can't sign in either: the CORRECT password gets the
    # same 401 as an unknown username (no enumeration of disabled accounts).
    refused = _login(anon_client, ALICE_EMAIL, ALICE_PASSWORD)
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Invalid credentials."

    # Re-enable for any later test.
    res = broker_client.patch(
        f"/api/v1/member-accounts/{account_id}", json={"status": "active"}
    )
    assert res.status_code == 200
    # Control: the same password works again once re-enabled, so the 401 above
    # was the disabled status and not a stale credential.
    assert _login(anon_client, ALICE_EMAIL, ALICE_PASSWORD).status_code == 200


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
        # A resend issues a NEW one-time password, which is why it is a
        # per-employee action the UI confirms — the old password stops working.
        assert acc.password_hash != before
        assert not PW.verify_password(acc.password_hash, ALICE_PASSWORD)
        assert acc.invite_sent_at is not None
        assert acc.invite_expires_at is not None


def test_list_member_accounts(broker_client: TestClient):
    res = broker_client.get("/api/v1/member-accounts")
    assert res.status_code == 200
    body = res.json()
    emails = {item["email"] for item in body["items"]}
    assert ALICE_EMAIL in emails
    assert body["total"] == len(body["items"])


def test_bulk_invite_without_mail_changes_nothing(broker_client: TestClient, monkeypatch):
    from app.api.v1 import member_accounts

    monkeypatch.setattr(member_accounts, "mail_deliverable", lambda: False)
    with SessionLocal() as session:
        before = [(a.id, a.password_hash, a.invite_sent_at) for a in session.query(MemberAccount)]
    response = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    )
    assert response.status_code == 503
    with SessionLocal() as session:
        after = [(a.id, a.password_hash, a.invite_sent_at) for a in session.query(MemberAccount)]
    assert after == before


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
    assert roll["duplicate"] == 2
    dup = [m for m in roll["needs_attention"] if m["reason"] == "duplicate"]
    assert len(dup) == 2 and all(m["email"] == shared for m in dup)

    # The run must not 500 on the constraint, and must leave the loser alone.
    res = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    )
    assert res.status_code == 200, res.text
    assert res.json()["duplicate"] == 2
    with SessionLocal() as session:
        holders = (
            session.query(MemberAccount).filter(MemberAccount.email == shared).all()
        )
        assert len(holders) == 0  # neither employee receives credentials at the shared mailbox

    # And on the NEXT read — now that the colleague's account exists — the loser
    # must still be reported as a duplicate. Resolving an employee to an account
    # merely because it shares their email adopts a COLLEAGUE's account: the
    # employee is then counted as covered while their colleague's mailbox is
    # treated as theirs.
    roll = broker_client.get(
        "/api/v1/member-accounts/rollout", params={"policy_year_id": PY_ACTIVE}
    ).json()
    assert roll["duplicate"] == 2
    assert broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    ).json()["duplicate"] == 2

    # An explicit assisted activation bypasses the roster mailbox, not account
    # identity or tenant checks. The link lets this employee choose a password.
    blocked = broker_client.post(f"/api/v1/employees/{EMP_NO_EMAIL}/member-account", json={})
    assert blocked.status_code == 409
    activation = broker_client.post(
        f"/api/v1/employees/{EMP_NO_EMAIL}/member-account",
        json={"delivery": "individual_link"},
    )
    assert activation.status_code == 201, activation.text
    assert activation.json()["email"] is None
    assert activation.json()["set_password_token"]
    assert activation.json()["system_login_id"]
    with SessionLocal() as session:
        account = session.get(MemberAccount, activation.json()["id"])
        assert account.password_hash is None
        assert account.invite_sent_at is None
        assert session.get(Employee, EMP_NO_EMAIL).member_account_id == account.id

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


def test_failed_login_does_not_activate_an_invited_account(anon_client: TestClient):
    """A failed sign-in must not activate an invited account."""
    with SessionLocal() as session:
        account = MemberAccount(
            client_id=DEMO_CLIENT_ID,
            email="carol@acme.test",
            staff_id="S-102",
            status=MEMBER_STATUS_INVITED,
            password_hash=PW.hash_password("Carols-Mailed-Pass-7"),
        )
        session.add(account)
        session.commit()
        account_id = account.id
    res = _login(anon_client, "carol@acme.test", "Not-Carols-Pass-1")
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


@pytest.mark.parametrize("email", ["member+benefits@example.com", "first.last@acme.com.sg"])
def test_bulk_email_validation_accepts_plain_mailboxes(email):
    from app.api.v1.member_accounts import _valid_invite_email

    assert _valid_invite_email(email)


@pytest.mark.parametrize("email", [
    "missing-at.example.com", "member@@example.com", "member@-example.com",
    "member@example..com", "first..last@example.com", ".member@example.com",
    "Member <member@example.com>", "member@example.com\r\nBcc:other@example.com",
    "member @example.com", "member@localhost", "a" * 65 + "@example.com",
])
def test_bulk_email_validation_rejects_invalid_addresses(email):
    from app.api.v1.member_accounts import _valid_invite_email

    assert not _valid_invite_email(email)


@pytest.fixture
def disabled_invite_roster(monkeypatch):
    from app.api.v1 import member_accounts
    from app.models.member_account import MEMBER_STATUS_DISABLED

    specs = [
        ("valid", "VALID@reactivation.test", True, "active", None),
        ("invalid", "invalid@@reactivation.test", True, "active", None),
        ("no-email", None, True, "active", None),
        ("used", "used@reactivation.test", True, "active", datetime.now(UTC)),
        ("shared", "shared@reactivation.test", True, "active", None),
        ("shared-peer", "shared@reactivation.test", False, "active", None),
        ("leaver", "leaver@reactivation.test", True, "terminated", None),
        ("pending", "pending@reactivation.test", False, "active", None),
    ]
    accounts = {}
    queued = []
    staff_ids = [f"REACTIVATE-{name}" for name, *_ in specs]
    with SessionLocal() as db:
        for name, email, existing, employee_status, signed_in in specs:
            account = None
            if existing:
                account = MemberAccount(client_id=DEMO_CLIENT_ID, staff_id=f"REACTIVATE-{name}",
                    email=email, status=MEMBER_STATUS_DISABLED, last_sign_in_at=signed_in)
                db.add(account)
                db.flush()
                accounts[name] = account.id
            db.add(Employee(client_id=DEMO_CLIENT_ID, policy_year_id=PY_ACTIVE,
                staff_id=f"REACTIVATE-{name}", employee_name=f"Reactivation test {name}",
                attribute_values={"email": email} if email else {}, derived_attribute_values={},
                source="csv_import", status=employee_status,
                member_account_id=account.id if account else None))
        db.commit()

    def capture_delivery(ids, client_id):
        queued.extend(ids)
        with member_accounts._SENDING_LOCK:
            member_accounts._SENDING.discard(client_id)

    monkeypatch.setattr(member_accounts, "_deliver_invites", capture_delivery)
    monkeypatch.setattr(member_accounts, "mail_deliverable", lambda: True)
    try:
        yield accounts, queued
    finally:
        with SessionLocal() as db:
            db.query(Employee).filter(Employee.staff_id.in_(staff_ids)).delete(synchronize_session=False)
            db.query(MemberAccount).filter(MemberAccount.staff_id.in_(staff_ids)).delete(synchronize_session=False)
            db.commit()


def test_send_all_requires_explicit_reenable_and_targets_only_eligible_accounts(
    broker_client, disabled_invite_roster,
):
    from app.models.member_account import MEMBER_STATUS_DISABLED

    accounts, queued = disabled_invite_roster
    rollout = broker_client.get("/api/v1/member-accounts/rollout",
        params={"policy_year_id": PY_ACTIVE}).json()
    assert rollout["disabled_invite_pending"] == 1
    ordinary = broker_client.post("/api/v1/member-accounts/bulk-invite",
        json={"policy_year_id": PY_ACTIVE})
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["accounts_reenabled"] == 0
    assert ordinary.json()["queued"] == rollout["invite_pending"]
    assert not set(accounts.values()).intersection(queued)
    with SessionLocal() as db:
        assert all(db.get(MemberAccount, account_id).status == MEMBER_STATUS_DISABLED
                   for account_id in accounts.values())

    queued.clear()
    response = broker_client.post("/api/v1/member-accounts/bulk-invite",
        json={"policy_year_id": PY_ACTIVE, "reenable_disabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["accounts_reenabled"] == 1
    assert accounts["valid"] in queued
    excluded = {account_id for name, account_id in accounts.items() if name != "valid"}
    assert not excluded.intersection(queued)
    with SessionLocal() as db:
        assert db.get(MemberAccount, accounts["valid"]).status == MEMBER_STATUS_INVITED
        for name, account_id in accounts.items():
            if name != "valid":
                assert db.get(MemberAccount, account_id).status == MEMBER_STATUS_DISABLED


@pytest.mark.parametrize("account_status", ["disabled", "invited"])
@pytest.mark.parametrize("other_year", [False, True])
def test_retained_account_email_owned_by_other_staff_is_never_queued(
    broker_client, disabled_invite_roster, account_status, other_year,
):
    from app.models.member_account import MEMBER_STATUS_DISABLED

    accounts, queued = disabled_invite_roster
    with SessionLocal() as db:
        account = db.get(MemberAccount, accounts["shared"])
        account.status = account_status
        employee = db.query(Employee).filter_by(staff_id="REACTIVATE-shared").one()
        employee.attribute_values = {"email": "new-owner-address@reactivation.test"}
        peer = db.query(Employee).filter_by(staff_id="REACTIVATE-shared-peer").one()
        if other_year:
            peer.policy_year_id = db.query(PolicyYear).filter(
                PolicyYear.client_id == DEMO_CLIENT_ID, PolicyYear.id != PY_ACTIVE,
            ).first().id
        peer.attribute_values = {"email": " SHARED@reactivation.test "}
        db.commit()

    rollout = broker_client.get("/api/v1/member-accounts/rollout",
        params={"policy_year_id": PY_ACTIVE}).json()
    assert rollout["disabled_invite_pending"] == 1  # only the valid fixture
    response = broker_client.post("/api/v1/member-accounts/bulk-invite",
        json={"policy_year_id": PY_ACTIVE, "reenable_disabled": True})
    assert response.status_code == 200, response.text
    assert accounts["shared"] not in queued
    with SessionLocal() as db:
        assert db.get(MemberAccount, accounts["shared"]).status == (
            MEMBER_STATUS_DISABLED if account_status == "disabled" else account_status
        )


def test_unconfigured_mail_never_reenables_accounts(
    broker_client, disabled_invite_roster, monkeypatch,
):
    from app.api.v1 import member_accounts
    from app.models.member_account import MEMBER_STATUS_DISABLED

    accounts, queued = disabled_invite_roster
    monkeypatch.setattr(member_accounts, "mail_deliverable", lambda: False)
    response = broker_client.post("/api/v1/member-accounts/bulk-invite",
        json={"policy_year_id": PY_ACTIVE, "reenable_disabled": True})
    assert response.status_code == 503
    assert queued == []
    with SessionLocal() as db:
        assert all(db.get(MemberAccount, account_id).status == MEMBER_STATUS_DISABLED
                   for account_id in accounts.values())


def test_viewers_cannot_reenable_and_bulk_invite(disabled_invite_roster):
    _, queued = disabled_invite_roster
    user = CurrentUser(user_id=_broker().user_id, broker_firm_id=DEMO_BROKER_FIRM_ID,
                       client_id=DEMO_CLIENT_ID, role="broker_viewer")
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).post("/api/v1/member-accounts/bulk-invite",
            json={"policy_year_id": PY_ACTIVE, "reenable_disabled": True})
        assert response.status_code == 403
        assert queued == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_background_delivery_rechecks_ownership_in_the_clients_firm(monkeypatch):
    from unittest.mock import Mock

    from app.api.v1 import member_accounts

    with SessionLocal() as db:
        account = MemberAccount(client_id=DEMO_CLIENT_ID, staff_id="DELIVERY-OWNER",
            email="delivery-shared@reactivation.test", status=MEMBER_STATUS_INVITED,
            password_hash="unchanged-test-hash")
        db.add(account)
        db.add(Employee(client_id=DEMO_CLIENT_ID, policy_year_id=PY_ACTIVE,
            staff_id="DELIVERY-PEER", employee_name="Delivery ownership fixture",
            attribute_values={"email": "delivery-shared@reactivation.test"},
            derived_attribute_values={}, source="csv_import", status="active"))
        db.commit()
        account_id = account.id

    issued = Mock()
    sent = Mock()
    scoped = []
    original_check = member_accounts._shared_roster_email

    def check_ownership(db, client_id, email, staff_id):
        assert scoped == [DEMO_BROKER_FIRM_ID]
        return original_check(db, client_id, email, staff_id)

    monkeypatch.setattr(member_accounts, "set_search_path",
                        lambda db, firm_id: scoped.append(firm_id))
    monkeypatch.setattr(member_accounts, "_shared_roster_email", check_ownership)
    monkeypatch.setattr(member_accounts, "issue_invite_credential", issued)
    monkeypatch.setattr(member_accounts, "send_member_invite", sent)
    member_accounts._deliver_invites([account_id], DEMO_CLIENT_ID)
    issued.assert_not_called()
    sent.assert_not_called()
    assert scoped == [DEMO_BROKER_FIRM_ID]
    with SessionLocal() as db:
        account = db.get(MemberAccount, account_id)
        assert account.password_hash == "unchanged-test-hash"
        assert account.invite_sent_at is None


# ── Link supersession, broker attribution, authenticator reset ───────────────


def _member(staff_id: str, **fields) -> str:
    with SessionLocal() as session:
        account = MemberAccount(client_id=DEMO_CLIENT_ID, staff_id=staff_id, **fields)
        session.add(account)
        session.commit()
        return account.id


def _confirmed_authenticator(account_id: str) -> None:
    from app.models import AuthMfa
    from app.models.auth import SUBJECT_MEMBER

    with SessionLocal() as session:
        session.add(AuthMfa(
            subject_type=SUBJECT_MEMBER, subject_id=account_id,
            totp_secret_enc="not-read-by-reset", confirmed_at=datetime.now(UTC),
        ))
        session.commit()


def _authenticators(account_id: str) -> int:
    from app.models import AuthMfa

    with SessionLocal() as session:
        return session.query(AuthMfa).filter(AuthMfa.subject_id == account_id).count()


@pytest.mark.parametrize("reissued", [False, True])
def test_a_new_set_password_link_cancels_the_earlier_ones(
    broker_client: TestClient, anon_client: TestClient, monkeypatch, reissued: bool,
):
    """Every reissue used to leave all earlier links redeemable until they
    expired, so a link in an old or forwarded message still set the password."""
    from app.api.v1 import portal_auth
    from app.core.portal_auth import issue_member_set_password_token

    monkeypatch.setattr(portal_auth, "is_breached", lambda password: False)
    account_id = _member(f"S-RELINK-{int(reissued)}", status=MEMBER_STATUS_INVITED)
    # A link handed out a minute ago through the same path (stamp + token).
    earlier = datetime.now(UTC) - timedelta(minutes=1)
    with SessionLocal() as session:
        account = session.get(MemberAccount, account_id)
        account.password_token_issued_at = earlier
        version = credential_version(account)
        session.commit()
    first = issue_member_set_password_token(account_id, version, issued_at=earlier)

    def redeem(token: str):
        return anon_client.post(
            "/api/v1/portal/auth/set-password",
            json={"token": token, "password": "Relinked-Member-42"},
            headers=_TENANT,
        )

    if not reissued:
        assert redeem(first).status_code == 200
        return
    second = broker_client.post(f"/api/v1/member-accounts/{account_id}/password-setup")
    assert second.status_code == 200, second.text
    refused = redeem(first)
    assert refused.status_code == 401
    assert refused.json()["detail"] == "A newer reset link has been issued. Use the latest one."
    assert redeem(second.json()["set_password_token"]).status_code == 200


def test_broker_issued_links_name_the_broker(broker_client: TestClient):
    """The security trail must say which broker handed a member a link — on
    account creation as well as on a later password-setup link."""
    from app.models import AuthEvent

    employee_id = "00000000-0000-0000-0000-00000000pa09"
    with SessionLocal() as session:
        session.add(Employee(
            id=employee_id, client_id=DEMO_CLIENT_ID, policy_year_id=PY_ACTIVE,
            staff_id="S-LINK-ACTOR", employee_name="Link Actor", attribute_values={},
            derived_attribute_values={}, source="csv_import", status="active",
        ))
        session.commit()
    created = broker_client.post(
        f"/api/v1/employees/{employee_id}/member-account",
        json={"delivery": "individual_link"},
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    assert broker_client.post(
        f"/api/v1/member-accounts/{account_id}/password-setup"
    ).status_code == 200
    with SessionLocal() as session:
        events = session.query(AuthEvent).filter(
            AuthEvent.subject_id == account_id,
            AuthEvent.event_type == "password_reset_request",
        ).all()
    assert [event.detail for event in events] == [
        {"reason": "broker_link", "actor_user_id": _broker().user_id},
    ] * 2
    assert {(event.client_id, event.broker_firm_id) for event in events} == {
        (DEMO_CLIENT_ID, DEMO_BROKER_FIRM_ID),
    }


def test_broker_reset_authenticator_clears_it_and_ends_sessions(
    broker_client: TestClient, anon_client: TestClient,
):
    """A member who lost their phone cannot pass the second factor until a
    broker removes the old authenticator; any session on that phone ends too."""
    from app.models import AuditLog, AuthEvent

    account_id = _member(
        "S-LOST-PHONE", status=MEMBER_STATUS_ACTIVE,
        password_hash=PW.hash_password("Lost-Phone-Member-42"),
        password_updated_at=datetime.now(UTC),
    )
    _confirmed_authenticator(account_id)
    with SessionLocal() as session:
        version = credential_version(session.get(MemberAccount, account_id))
    token, _ = issue_member_token(account_id, DEMO_CLIENT_ID, version)
    bearer = {"Authorization": f"Bearer {token}"}
    status_url = "/api/v1/portal/auth/security-status"
    assert anon_client.get(status_url, headers=bearer).json()["mfa_status"] == "confirmed"

    res = broker_client.post(f"/api/v1/member-accounts/{account_id}/mfa/reset")
    assert res.status_code == 204, res.text
    assert res.content == b""
    assert _authenticators(account_id) == 0
    assert anon_client.get(status_url, headers=bearer).status_code == 401
    with SessionLocal() as session:
        audit = session.query(AuditLog).filter(
            AuditLog.action == "mfa_reset", AuditLog.entity_id == account_id,
        ).one()
        event = session.query(AuthEvent).filter(
            AuthEvent.event_type == "mfa_reset", AuthEvent.subject_id == account_id,
        ).one()
    assert audit.entity_type == "member_account"
    assert audit.after == {"sessions_revoked": 1}
    assert audit.client_id == DEMO_CLIENT_ID
    # The security trail names the broker who removed it, on its own row.
    assert (event.surface, event.subject_type, event.outcome) == ("portal", "member", "success")
    assert (event.client_id, event.broker_firm_id) == (DEMO_CLIENT_ID, DEMO_BROKER_FIRM_ID)
    assert event.detail == {"sessions_revoked": 1, "actor_user_id": _broker().user_id}


def test_reset_authenticator_is_tenant_scoped(broker_client: TestClient):
    with SessionLocal() as session:
        other = Client(name="Authenticator Reset Other Co", broker_firm_id=DEMO_BROKER_FIRM_ID)
        session.add(other)
        session.flush()
        foreign = MemberAccount(
            client_id=other.id, staff_id="S-FOREIGN", status=MEMBER_STATUS_ACTIVE,
        )
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id
    _confirmed_authenticator(foreign_id)
    res = broker_client.post(f"/api/v1/member-accounts/{foreign_id}/mfa/reset")
    assert res.status_code == 404
    assert _authenticators(foreign_id) == 1


def test_viewers_cannot_reset_an_authenticator():
    account_id = _member("S-VIEWER-RESET", status=MEMBER_STATUS_ACTIVE)
    _confirmed_authenticator(account_id)
    viewer = CurrentUser(user_id=_broker().user_id, broker_firm_id=DEMO_BROKER_FIRM_ID,
                         client_id=DEMO_CLIENT_ID, role="broker_viewer")
    app.dependency_overrides[get_current_user] = lambda: viewer
    try:
        res = TestClient(app).post(f"/api/v1/member-accounts/{account_id}/mfa/reset")
        assert res.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert _authenticators(account_id) == 1


def test_member_account_actions_are_filed_under_the_members_company():
    """A system admin reaches every company's members with whichever company it
    has selected. The audit rows and the security trail must name the MEMBER's
    company, or the action shows up in the wrong company's activity."""
    from app.models import AuditLog, AuthEvent

    with SessionLocal() as session:
        other = Client(name="Stamped Member Co", broker_firm_id=DEMO_BROKER_FIRM_ID)
        session.add(other)
        session.flush()
        account = MemberAccount(
            client_id=other.id, staff_id="S-STAMPED", status=MEMBER_STATUS_ACTIVE,
        )
        session.add(account)
        session.commit()
        other_id, account_id = other.id, account.id
    _confirmed_authenticator(account_id)
    admin = CurrentUser(user_id="00000000-0000-0000-0000-0000000000d1",
                        broker_firm_id=None, client_id=DEMO_CLIENT_ID, role="system_admin")
    app.dependency_overrides[get_current_user] = lambda: admin
    try:
        api = TestClient(app)
        base = f"/api/v1/member-accounts/{account_id}"
        assert api.post(f"{base}/regenerate-login-id").status_code == 200
        assert api.post(f"{base}/mfa/reset").status_code == 204
        assert api.patch(base, json={"status": "disabled"}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    with SessionLocal() as session:
        audits = session.query(AuditLog).filter(AuditLog.entity_id == account_id).all()
        event = session.query(AuthEvent).filter(
            AuthEvent.event_type == "mfa_reset", AuthEvent.subject_id == account_id,
        ).one()
    assert {(a.action, a.client_id, a.cross_tenant_access) for a in audits} == {
        ("member_account.login_id_regenerated", other_id, True),
        ("mfa_reset", other_id, True),
        ("member_account.status_changed", other_id, True),
    }
    assert (event.client_id, event.broker_firm_id) == (other_id, DEMO_BROKER_FIRM_ID)
    assert event.detail["actor_user_id"] == admin.user_id


# ── Rollout: "using the portal" means having signed in ───────────────────────

PY_HANDOVER = "00000000-0000-0000-0000-00000000pa20"


def test_a_handover_password_is_not_using_the_portal(broker_client: TestClient, monkeypatch):
    """A broker's direct password set activates the account before the member
    has ever signed in. It used to be counted as using the portal; it is a
    credential handed over, so it reads as invited, the send leaves it alone,
    and only a real sign-in moves it."""
    from app.api.v1 import member_accounts

    monkeypatch.setattr(member_accounts, "is_breached", lambda password: False)
    with SessionLocal() as session:
        # Its own (draft) year, so the counts and the send see this one member
        # only; a company has a single active year.
        session.add(PolicyYear(
            id=PY_HANDOVER, client_id=DEMO_CLIENT_ID, year=2029,
            start_date=date(2029, 1, 1), end_date=date(2029, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        account = MemberAccount(
            client_id=DEMO_CLIENT_ID, staff_id="S-HANDOVER", email="handover@acme.test",
            status=MEMBER_STATUS_INVITED,
        )
        session.add(account)
        session.flush()
        session.add(Employee(
            client_id=DEMO_CLIENT_ID, policy_year_id=PY_HANDOVER, staff_id="S-HANDOVER",
            employee_name="Hana Handover", attribute_values={"email": "handover@acme.test"},
            derived_attribute_values={}, source="csv_import", status="active",
            member_account_id=account.id,
        ))
        session.commit()
        account_id = account.id

    def buckets() -> tuple[int, int, int]:
        roll = broker_client.get(
            "/api/v1/member-accounts/rollout", params={"policy_year_id": PY_HANDOVER}
        ).json()
        return roll["invite_pending"], roll["invited"], roll["signed_in"]

    assert buckets() == (1, 0, 0)
    handed = broker_client.post(
        f"/api/v1/member-accounts/{account_id}/set-password",
        json={"password": "Handover-Member-4821"},
    )
    assert handed.status_code == 200, handed.text
    assert handed.json()["status"] == MEMBER_STATUS_ACTIVE
    assert buckets() == (0, 1, 0)

    with SessionLocal() as session:
        handover_hash = session.get(MemberAccount, account_id).password_hash
    sent = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_HANDOVER}
    )
    assert sent.status_code == 200, sent.text
    assert (sent.json()["queued"], sent.json()["already_invited"]) == (0, 1)
    with SessionLocal() as session:
        account = session.get(MemberAccount, account_id)
        # No fresh one-time password replaced the one the member was given.
        assert account.password_hash == handover_hash
        assert account.invite_sent_at is None
        account.last_sign_in_at = datetime.now(UTC)
        session.commit()
    assert buckets() == (0, 0, 1)


# ── Authenticator state on the broker's account rows ─────────────────────────


def test_accounts_say_whether_an_authenticator_is_enrolled(broker_client: TestClient):
    """The panel offers "Reset authenticator" only when there is one to reset.
    A pending enrolment signs nobody in, so it does not count."""
    from app.models import AuthMfa
    from app.models.auth import SUBJECT_MEMBER

    enrolled = _member("S-MFA-ON", status=MEMBER_STATUS_ACTIVE)
    _confirmed_authenticator(enrolled)
    pending = _member("S-MFA-PENDING", status=MEMBER_STATUS_ACTIVE)
    with SessionLocal() as session:
        session.add(AuthMfa(
            subject_type=SUBJECT_MEMBER, subject_id=pending, totp_secret_enc="unconfirmed",
        ))
        session.commit()
    _member("S-MFA-OFF", status=MEMBER_STATUS_ACTIVE)

    def listed() -> dict[str, bool]:
        items = broker_client.get("/api/v1/member-accounts").json()["items"]
        return {
            item["staff_id"]: item["mfa_enrolled"]
            for item in items if item["staff_id"].startswith("S-MFA-")
        }

    assert listed() == {"S-MFA-ON": True, "S-MFA-PENDING": False, "S-MFA-OFF": False}
    single = broker_client.post(f"/api/v1/member-accounts/{enrolled}/regenerate-login-id")
    assert single.json()["mfa_enrolled"] is True
    assert broker_client.post(f"/api/v1/member-accounts/{enrolled}/mfa/reset").status_code == 204
    assert listed()["S-MFA-ON"] is False


def test_listing_accounts_reads_authenticators_in_one_query(broker_client: TestClient):
    """One authenticator query for the whole roster, however many are enrolled."""
    from sqlalchemy import event

    def authenticator_queries() -> int:
        seen: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if "auth_mfa" in statement:
                seen.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert broker_client.get("/api/v1/member-accounts").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    for n in range(3):
        _confirmed_authenticator(_member(f"S-MFA-BATCH-{n}", status=MEMBER_STATUS_ACTIVE))
    assert authenticator_queries() == 1


# ── No web address, no invite ────────────────────────────────────────────────

ORIGIN_UNAVAILABLE = {
    "code": "firm_origin_unavailable", "message": "This broker has no active web address yet.",
}


@pytest.fixture
def no_broker_address(monkeypatch):
    """The demo company's broker has no address an invite link could use.
    Yields the (never expected to be called) mail sender."""
    from unittest.mock import Mock

    from app.api.v1 import member_accounts
    from app.core.tenant_resolution import FirmOriginUnavailable

    def unavailable(db, client):
        raise FirmOriginUnavailable(client.broker_firm_id)

    sent = Mock(return_value=True)
    monkeypatch.setattr(member_accounts, "portal_sign_in_url", unavailable)
    monkeypatch.setattr(member_accounts, "send_member_invite", sent)
    monkeypatch.setattr(member_accounts, "mail_deliverable", lambda: True)
    staff_ids = ["NOADDR-NEW", "NOADDR-OLD"]
    with SessionLocal() as db:
        db.add(Employee(client_id=DEMO_CLIENT_ID, policy_year_id=PY_ACTIVE,
            staff_id="NOADDR-NEW", employee_name="No address, new",
            attribute_values={"email": "noaddr.new@acme.test"}, derived_attribute_values={},
            source="csv_import", status="active"))
        db.add(MemberAccount(client_id=DEMO_CLIENT_ID, staff_id="NOADDR-OLD",
            email="noaddr.old@acme.test", status=MEMBER_STATUS_INVITED,
            password_hash="unchanged-test-hash"))
        db.commit()
    try:
        yield sent
    finally:
        with SessionLocal() as db:
            db.query(Employee).filter(Employee.staff_id.in_(staff_ids)).delete(
                synchronize_session=False
            )
            db.query(MemberAccount).filter(MemberAccount.staff_id.in_(staff_ids)).delete(
                synchronize_session=False
            )
            db.commit()


def _noaddr(staff_id: str) -> MemberAccount | None:
    with SessionLocal() as db:
        return db.query(MemberAccount).filter(MemberAccount.staff_id == staff_id).one_or_none()


def test_invites_are_refused_while_the_broker_has_no_address(broker_client, no_broker_address):
    """Every broker-triggered send stops with 409 before anything changes: no
    account created, no credential replaced, no mail."""
    with SessionLocal() as db:
        employee_id = db.query(Employee.id).filter(Employee.staff_id == "NOADDR-NEW").scalar()
    created = broker_client.post(f"/api/v1/employees/{employee_id}/member-account", json={})
    assert created.status_code == 409, created.text
    assert created.json()["detail"] == ORIGIN_UNAVAILABLE
    assert _noaddr("NOADDR-NEW") is None

    existing = _noaddr("NOADDR-OLD")
    assert existing is not None
    resent = broker_client.post(f"/api/v1/member-accounts/{existing.id}/resend-invite")
    assert resent.status_code == 409
    assert resent.json()["detail"] == ORIGIN_UNAVAILABLE
    unchanged = _noaddr("NOADDR-OLD")
    assert unchanged is not None
    assert (unchanged.password_hash, unchanged.invite_sent_at) == ("unchanged-test-hash", None)

    bulk = broker_client.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_ACTIVE}
    )
    assert bulk.status_code == 409
    assert bulk.json()["detail"] == ORIGIN_UNAVAILABLE
    assert _noaddr("NOADDR-NEW") is None
    no_broker_address.assert_not_called()


def test_background_invites_wait_for_the_brokers_address(no_broker_address):
    """A run queued before the address went away sends nothing and changes
    nothing, so every target is picked up by the next run."""
    from app.api.v1 import member_accounts

    existing = _noaddr("NOADDR-OLD")
    assert existing is not None
    member_accounts._deliver_invites([existing.id], DEMO_CLIENT_ID)
    no_broker_address.assert_not_called()
    unchanged = _noaddr("NOADDR-OLD")
    assert unchanged is not None
    assert (unchanged.password_hash, unchanged.invite_sent_at) == ("unchanged-test-hash", None)
    assert DEMO_CLIENT_ID not in member_accounts._SENDING
