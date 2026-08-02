"""One-time invite credentials: generation, expiry, and URL shape.

Unit-level companions to the endpoint tests in `test_portal_auth.py`. The rules
under test are the ones whose failure is silent — a generated password the
tenant's own policy would reject, an expiry that outlives the credential it
bounds, or a sign-in link that drops the tenant.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core import passwords as PW
from app.core.settings import clear_settings_cache
from app.models import MemberAccount
from app.services.member_invite import (
    INVITE_TTL_DAYS,
    clear_invite_expiry,
    generate_one_time_password,
    invite_expired,
    issue_invite_credential,
    login_username,
    portal_sign_in_url,
    restore_credential,
    snapshot_credential,
)


def _account(**kw) -> MemberAccount:
    defaults = dict(client_id="c1", staff_id="S-1", email="a@b.test")
    return MemberAccount(**{**defaults, "failed_attempts": 0, **kw})


def test_generated_password_meets_the_tenants_own_floor():
    # A credential the tenant's policy would refuse is a rollout that dies at
    # the last step for a reason nobody can see, so generation scales to it.
    for floor in (0, 60, 90, 120):
        password = generate_one_time_password(floor)
        ok, reason = PW.password_meets_policy(password, floor)
        assert ok, f"floor={floor}: {reason}"


def test_generated_passwords_are_unique_and_unambiguous():
    passwords = {generate_one_time_password(60) for _ in range(50)}
    assert len(passwords) == 50
    # Typed off an email, so no glyph pairs a reader can confuse.
    assert not set("".join(passwords)) & set("O0Il1")


def test_issue_stamps_rotation_due_so_the_mailed_password_is_single_use():
    account = _account()
    password = issue_invite_credential(account)
    assert PW.verify_password(account.password_hash, password)
    # Already due → the first sign-in is diverted into set-password.
    assert account.must_rotate_after <= datetime.now(UTC)
    assert account.invite_expires_at > datetime.now(UTC)


def test_expiry_is_bounded_and_cleared_by_a_real_password():
    account = _account()
    issue_invite_credential(account)
    assert account.invite_expires_at <= datetime.now(UTC) + timedelta(
        days=INVITE_TTL_DAYS
    )
    assert not invite_expired(account)

    account.invite_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert invite_expired(account)

    # Once the member picks their own password the deadline must go, or it
    # would expire the password they just chose.
    clear_invite_expiry(account)
    assert not invite_expired(account)


def test_naive_expiry_is_treated_as_utc():
    """SQLite hands back naive datetimes; comparing one to an aware `now`
    raises, which would 500 every sign-in rather than fail the check."""
    account = _account()
    account.invite_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        minutes=1
    )
    assert invite_expired(account)


def test_failed_send_restores_the_previous_credential():
    """A send that fails must not leave a password nobody received."""
    account = _account()
    issue_invite_credential(account)  # a first invite, delivered
    prior = snapshot_credential(account)

    second = issue_invite_credential(account)  # a resend that will fail
    assert PW.verify_password(account.password_hash, second)

    restore_credential(account, prior)
    assert account.password_hash == prior.password_hash
    assert not PW.verify_password(account.password_hash, second)
    assert account.invite_expires_at == prior.invite_expires_at


def test_username_follows_the_companys_login_source():
    """What the invite email and the broker panel PRINT follows the company's
    `portal_login_source` setting. Both read this one resolver, so they can
    never tell a member two different things — and neither can hardcode the
    email, which is what made a company set to "System-generated ID" still see
    an email address on the employee's Portal access panel."""
    full = _account(system_login_id="EM-7Q2M8K")
    assert login_username(full, "email") == "a@b.test"
    assert login_username(full, "system_id") == "EM-7Q2M8K"
    assert login_username(full, "staff_id") == "S-1"
    assert login_username(full, None) == "a@b.test"  # unset behaves as email


def test_username_never_renders_blank():
    """A company set to `email` still has email-less members; telling one their
    username is "" is worse than telling them their system id."""
    emailless = _account(email=None, system_login_id="EM-7Q2M8K")
    assert login_username(emailless, "email") == "EM-7Q2M8K"
    assert login_username(_account(email=None), "email") == "S-1"
    assert login_username(_account(system_login_id=None), "system_id") == "a@b.test"


@pytest.fixture
def _settings_env(monkeypatch):
    yield monkeypatch
    clear_settings_cache()


def test_sign_in_url_always_carries_the_tenant(_settings_env):
    """Without the tenant the portal cannot resolve the company, and the member
    is told their details weren't recognised — indistinguishable from a wrong
    password, on the one screen where that misdiagnosis costs the most."""
    _settings_env.setenv("INSPRO_TENANT_MODE", "header")
    _settings_env.setenv("INSPRO_FRONTEND_ORIGIN", "https://inspro-portal.example")
    clear_settings_cache()
    assert portal_sign_in_url("cdl") == (
        "https://inspro-portal.example/portal/sign-in?company=cdl"
    )

    _settings_env.setenv("INSPRO_TENANT_MODE", "subdomain")
    _settings_env.setenv("INSPRO_BASE_DOMAIN", "inspro.sg")
    clear_settings_cache()
    assert portal_sign_in_url("cdl") == "https://cdl.portal.inspro.sg/portal/sign-in"
