"""One-time invite credentials: generation, expiry, and URL shape.

Unit-level companions to the endpoint tests in `test_portal_auth.py`. The rules
under test are the ones whose failure is silent — a generated password the
tenant's own policy would reject, an expiry that outlives the credential it
bounds, a sign-in link that drops the tenant or points at another broker's
address, or mail sent for a broker that has no address yet.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import passwords as PW
from app.core.settings import clear_settings_cache
from app.core.tenant_resolution import FirmOriginUnavailable
from app.db.base import Base
from app.models import (
    BrokerFirm,
    Claim,
    ClaimMessage,
    ClaimNotification,
    Client,
    MemberAccount,
)
from app.models.platform import TenantDomain
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


def test_newest_set_password_link_wins_at_one_second_resolution():
    """Issuing a link refuses every link issued in an EARLIER second. `iat` is
    whole seconds, so a link from the same second as the newest stays valid,
    and an account stamped before the column existed accepts any link (links
    already in members' mailboxes keep working)."""
    from app.core.credentials import password_token_current, stamp_password_token

    account = _account()
    assert password_token_current(account, 0)

    issued = stamp_password_token(account)
    assert account.password_token_issued_at == issued
    second = int(issued.timestamp())
    assert password_token_current(account, second)
    assert password_token_current(account, second + 5)
    assert not password_token_current(account, second - 1)

    account.password_token_issued_at = issued.replace(tzinfo=None)  # as SQLite returns it
    assert password_token_current(account, second)
    assert not password_token_current(account, second - 1)


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


@pytest.fixture
def links_db() -> Iterator[Session]:
    """A private in-memory database holding an owner firm with no domain, a
    broker with its own client domain, and a broker with no address at all."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        db.add_all([
            BrokerFirm(id="owner", name="Owner", slug="owner", is_platform_owner=True),
            BrokerFirm(id="brokera", name="Broker A", slug="brokera"),
            BrokerFirm(id="brokerb", name="Broker B", slug="brokerb"),
        ])
        db.flush()
        db.add(TenantDomain(broker_firm_id="brokera", hostname="benefits.brokera.test",
                            surface="client", is_primary=True, status="active"))
        db.add_all([
            Client(id="owned", name="CDL", slug="cdl", broker_firm_id="owner"),
            Client(id="unaliased", name="No alias", broker_firm_id="owner"),
            Client(id="hosted", name="CDL at A", slug="cdl", broker_firm_id="brokera"),
            Client(id="homeless", name="CDL at B", slug="cdl", broker_firm_id="brokerb"),
        ])
        db.commit()
        yield db
    engine.dispose()


def test_sign_in_url_tolerates_a_trailing_slash_on_the_origin(_settings_env, links_db):
    """`INSPRO_FRONTEND_ORIGIN` is often configured with a trailing slash; the
    emailed link must still carry the company in the path, not `//portal/...`."""
    _settings_env.setenv("INSPRO_FRONTEND_ORIGIN", "https://inspro-portal.example/")
    clear_settings_cache()

    assert portal_sign_in_url(links_db, links_db.get(Client, "owned")) == (
        "https://inspro-portal.example/portal/cdl/sign-in"
    )


def test_sign_in_url_always_carries_the_tenant(_settings_env, links_db):
    """Without the tenant the portal cannot resolve the company, and the member
    is told their details weren't recognised — indistinguishable from a wrong
    password, on the one screen where that misdiagnosis costs the most."""
    _settings_env.setenv("INSPRO_FRONTEND_ORIGIN", "https://inspro-portal.example")
    clear_settings_cache()
    # The company rides in the PATH, so the address the member is emailed is the
    # one they keep using. The old `?company=` form still resolves client-side
    # (`captureTenantSlugFromUrl` promotes it into the path) — unopened invites
    # are live credentials for INVITE_TTL_DAYS.
    assert portal_sign_in_url(links_db, links_db.get(Client, "owned")) == (
        "https://inspro-portal.example/portal/cdl/sign-in"
    )
    # No slug is still a real state (a client with no alias): the link stays
    # valid and the portal asks which company, rather than 404ing.
    assert portal_sign_in_url(links_db, links_db.get(Client, "unaliased")) == (
        "https://inspro-portal.example/portal/sign-in"
    )


def test_links_open_on_the_companys_own_broker_address(_settings_env, links_db):
    """A broker's members are sent to that broker's domain, never the platform's
    — the same alias at another broker is another company."""
    from app.services.email_template_recipients import company_sign_in_url

    _settings_env.setenv("INSPRO_FRONTEND_ORIGIN", "https://inspro-portal.example")
    clear_settings_cache()
    hosted = links_db.get(Client, "hosted")
    assert portal_sign_in_url(links_db, hosted) == (
        "https://benefits.brokera.test/portal/cdl/sign-in"
    )
    assert company_sign_in_url(hosted, "employee") == (
        "https://benefits.brokera.test/portal/cdl/sign-in"
    )
    assert company_sign_in_url(hosted, "hr") == (
        "https://benefits.brokera.test/hr/sign-in?company=cdl"
    )


def test_no_link_for_a_broker_without_an_address(_settings_env, links_db, monkeypatch):
    """Production never falls back to the platform's address for another
    broker's members: the send stops instead."""
    from app.core import tenant_resolution
    from app.services.email_template_recipients import company_sign_in_url

    prod = replace(tenant_resolution.get_settings(), env="prod")
    monkeypatch.setattr(tenant_resolution, "get_settings", lambda: prod)
    homeless = links_db.get(Client, "homeless")
    with pytest.raises(FirmOriginUnavailable):
        portal_sign_in_url(links_db, homeless)
    with pytest.raises(FirmOriginUnavailable):
        company_sign_in_url(homeless, "hr")


def test_claim_mail_waits_for_the_brokers_address(_settings_env, links_db, monkeypatch):
    """A worker holding mail for a broker with no address sends nothing, spends
    no attempt and keeps the item queued — then delivers once the address exists."""
    from app.core import tenant_resolution
    from app.services import claim_notifications as service

    prod = replace(tenant_resolution.get_settings(), env="prod")
    monkeypatch.setattr(tenant_resolution, "get_settings", lambda: prod)
    factory = sessionmaker(links_db.get_bind(), expire_on_commit=False)
    sent: list[str] = []

    class Mailer:
        def send_claim_update(self, email: str, url: str) -> None:
            sent.append(url)

    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service, "get_mailer", lambda *_: Mailer())
    links_db.add(MemberAccount(id="member", client_id="homeless", staff_id="S-1",
                               email="member@b.test", failed_attempts=0))
    queued = service.enqueue_claim_notification(
        links_db,
        Claim(id="claim", client_id="homeless", submitted_by_member_id="member"),
        ClaimMessage(id="message", author_type="broker", event=None),
    )
    links_db.commit()
    assert queued is not None

    assert service.process_one_claim_notification(None) is False
    assert sent == []
    with factory() as db:
        held = db.get(ClaimNotification, queued.id)
        assert (held.status, held.attempts, held.last_error) == (
            "queued", 0, "firm_origin_unavailable",
        )
        assert held.available_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(
            minutes=service.ORIGIN_RETRY_MINUTES - 1
        )
        db.add(TenantDomain(broker_firm_id="brokerb", hostname="portal.brokerb.test",
                            surface="client", is_primary=True, status="active"))
        held.available_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    assert service.process_one_claim_notification(None) is True
    assert sent == ["https://portal.brokerb.test/portal/cdl/sign-in"]
    with factory() as db:
        delivered = db.get(ClaimNotification, queued.id)
        assert (delivered.status, delivered.attempts) == ("sent", 1)
