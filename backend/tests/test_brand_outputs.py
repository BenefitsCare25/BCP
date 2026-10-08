"""System outputs carry the resolved brand: TOTP issuer, mail identity,
enrolment helpline and broker sheet names. Without brand rows they are
unchanged; a company override applies to that company only; another firm's
brand is never used."""

from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
from typing import ClassVar
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import mailer as mailer_module
from app.core import mfa
from app.core.totp import clean_issuer, provisioning_uri
from app.db.base import Base
from app.models import BrokerFirm, Client, MemberAccount
from app.models.brand import BrandProfile
from app.services import member_invite
from app.services.brand import (
    DEFAULT_BRAND,
    Brand,
    firm_card_prefix,
    resolve_brand,
    resolve_client_brand,
    resolve_staff_brand,
)
from app.services.enrollment_forms.config import (
    DEFAULT_HELPLINE,
    default_helpline,
    default_settings,
)
from app.services.panel_cards import platform_dependant_id, platform_member_id
from app.services.report_workbooks import SYSTEM_CATEGORY_SHEET, branded_title

PLATFORM_SENDER = "noreply@platform.test"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            BrokerFirm(id="alpha", name="Alpha Brokers", is_platform_owner=True),
            BrokerFirm(id="beta", name="Beta Brokers"),
            BrokerFirm(id="plain", name="Plain Brokers"),
        ])
        session.flush()
        session.add_all([
            Client(id="a1", name="A One", broker_firm_id="alpha", slug="a-one"),
            Client(id="a2", name="A Two", broker_firm_id="alpha", slug="a-two"),
            Client(id="b1", name="B One", broker_firm_id="beta", slug="b-one"),
            Client(id="p1", name="P One", broker_firm_id="plain", slug="p-one"),
        ])
        session.flush()
        session.add_all([
            BrandProfile(
                broker_firm_id="alpha", scope_key="firm", product_name="Alpha Benefits",
                short_name="Alpha", email_sender_name="Alpha Benefits Team",
                email_reply_to="help@alpha.test", support_email="care@alpha.test",
                support_phone="6000 0000", email_from_address="mail@alpha.test",
                email_from_verified_at=datetime.now(UTC),
            ),
            BrandProfile(
                broker_firm_id="alpha", client_id="a1", scope_key="a1",
                product_name="A One Portal", email_sender_name="A One HR",
            ),
            # Beta's From address is not verified, so mail must not use it.
            BrandProfile(
                broker_firm_id="beta", scope_key="firm", product_name="Beta Cover",
                short_name="Beta", email_sender_name="Beta Cover",
                email_from_address="mail@beta.test",
            ),
        ])
        session.commit()
        yield session
    engine.dispose()


class _Smtp:
    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self) -> _Smtp:
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def has_extn(self, _name: str) -> bool:
        return True

    def starttls(self) -> None:
        return None

    def login(self, *_args) -> None:
        return None

    def send_message(self, msg: EmailMessage) -> None:
        _Smtp.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("INSPRO_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("INSPRO_SMTP_FROM", PLATFORM_SENDER)
    monkeypatch.delenv("INSPRO_SMTP_USER", raising=False)
    monkeypatch.setattr(mailer_module.smtplib, "SMTP", _Smtp)
    _Smtp.sent = []
    return _Smtp.sent


# ── Resolution per account ───────────────────────────────────────────────────
def test_company_override_applies_to_that_company_only(db) -> None:
    assert resolve_client_brand(db, "a1").product_name == "A One Portal"
    assert resolve_client_brand(db, "a2").product_name == "Alpha Benefits"
    assert resolve_client_brand(db, "a1", company=False).product_name == "Alpha Benefits"
    assert resolve_client_brand(db, "b1").product_name == "Beta Cover"
    assert resolve_client_brand(db, "p1") is DEFAULT_BRAND
    assert resolve_client_brand(db, "missing") is DEFAULT_BRAND


def test_another_firms_company_override_is_never_layered(db) -> None:
    assert resolve_brand(db, "beta", "a1").product_name == "Beta Cover"


def test_staff_see_their_firm_and_master_admin_the_platform_owner(db) -> None:
    assert resolve_staff_brand(db, "beta").product_name == "Beta Cover"
    assert resolve_staff_brand(db, None).product_name == "Alpha Benefits"
    assert resolve_staff_brand(db, "plain") is DEFAULT_BRAND


# ── TOTP issuer ──────────────────────────────────────────────────────────────
def test_default_issuer_uri_is_unchanged() -> None:
    account = "member.one+x@example.test"
    legacy = (
        f"otpauth://totp/{quote(f'Inspro:{account}')}?secret=ABC"
        "&issuer=Inspro&digits=6&period=30"
    )
    assert provisioning_uri("ABC", account) == legacy
    assert provisioning_uri("ABC", account, DEFAULT_BRAND.product_name) == legacy


def test_issuer_is_sanitised_for_otpauth() -> None:
    assert clean_issuer("Acme: Benefits\n") == "Acme Benefits"
    assert clean_issuer(" : ") == "Inspro"
    uri = provisioning_uri("ABC", "x@y.test", "Acme/Co: Benefits")
    assert uri.startswith("otpauth://totp/Acme%2FCo%20Benefits%3Ax%40y.test?")
    assert "&issuer=Acme%2FCo%20Benefits&" in uri


def test_enrolment_uri_names_the_accounts_brand(db) -> None:
    issuer = resolve_client_brand(db, "a1").product_name
    _, uri = mfa.start_enrollment(db, "member", "m-1", "m@a1.test", issuer)
    assert "issuer=A%20One%20Portal&" in uri
    _, uri = mfa.start_enrollment(
        db, "user", "u-1", "u@b.test", resolve_staff_brand(db, "beta").product_name
    )
    assert "issuer=Beta%20Cover&" in uri
    assert "Alpha" not in uri


# ── System email identity ────────────────────────────────────────────────────
def test_default_brand_keeps_the_platform_sender(smtp) -> None:
    mailer_module.SmtpMailer(DEFAULT_BRAND).send_claim_update("m@test", "https://x.test/p")
    mailer_module.SmtpMailer().send_claim_digest("m@test", ["https://x.test/c"])
    assert [msg["From"] for msg in smtp] == [PLATFORM_SENDER, PLATFORM_SENDER]
    assert all(msg["Reply-To"] is None for msg in smtp)


def test_verified_firm_sender_name_and_reply_to(db, smtp) -> None:
    brand = resolve_client_brand(db, "a2")
    mailer_module.SmtpMailer(brand).send_workflow_notice("m@test", "Subject", "Body")
    (msg,) = smtp
    assert msg["From"] == "Alpha Benefits Team <mail@alpha.test>"
    assert msg["Reply-To"] == "help@alpha.test"


def test_company_sender_name_overrides_on_firm_address(db, smtp) -> None:
    mailer_module.SmtpMailer(resolve_client_brand(db, "a1")).send_claim_update(
        "m@test", "https://x.test/p"
    )
    assert smtp[0]["From"] == "A One HR <mail@alpha.test>"


def test_unverified_from_address_is_not_used(db, smtp) -> None:
    brand = resolve_client_brand(db, "b1")
    assert brand.email_from_address is None
    mailer_module.SmtpMailer(brand).send_staff_invite("s@test", "Beta Brokers", "https://x")
    (msg,) = smtp
    assert msg["From"] == f"Beta Cover <{PLATFORM_SENDER}>"
    assert msg["Reply-To"] is None


def test_platform_sender_display_name_is_replaced_by_the_brand() -> None:
    brand = Brand(email_sender_name="Acme", firm_id="f")
    assert mailer_module.sender_address(brand, f"Platform <{PLATFORM_SENDER}>") == (
        f"Acme <{PLATFORM_SENDER}>"
    )


def test_member_invite_is_sent_as_the_companys_brand(db, monkeypatch) -> None:
    used = []

    class Sink:
        def send_member_invite(self, *_args) -> None:
            return None

    def get_mailer(brand=None):
        used.append(brand)
        return Sink()

    monkeypatch.setattr(member_invite, "get_mailer", get_mailer)
    account = MemberAccount(client_id="a1", staff_id="S1", email="m@a1.test")
    brand = resolve_client_brand(db, "a1")
    assert member_invite.send_member_invite(account, "pw", "https://x", brand=brand)
    assert used == [brand]


# ── Documents ────────────────────────────────────────────────────────────────
def test_enrolment_helpline_default_follows_the_brand(db) -> None:
    assert default_helpline(DEFAULT_BRAND) == DEFAULT_HELPLINE
    assert default_settings([]).helpline == DEFAULT_HELPLINE
    alpha = default_settings([], resolve_client_brand(db, "a2")).helpline
    assert alpha == (
        "For any queries on this form, please contact Alpha Benefits at "
        "6000 0000 (care@alpha.test)."
    )
    beta = default_helpline(resolve_client_brand(db, "b1"))
    assert beta == (
        "For any queries on this form, please contact Beta Cover at "
        "helpdesk@inspro.com.sg."
    )


def test_broker_sheet_title_names_the_firm() -> None:
    assert branded_title(SYSTEM_CATEGORY_SHEET, "Inspro") == "Inspro Use - System Category"
    assert branded_title(SYSTEM_CATEGORY_SHEET, "Alpha") == "Alpha Use - System Category"
    long = branded_title(SYSTEM_CATEGORY_SHEET, "Very/Long:Brand Name Here")
    assert len(long) <= 31 and "/" not in long and ":" not in long
    assert branded_title("Employees", "Alpha") == "Employees"


# ── E-card prefix ─────────────────────────────────────────────────────────────
def test_card_prefix_is_firm_only_and_frozen_per_company(db) -> None:
    assert firm_card_prefix(db, "alpha") is None
    row = db.query(BrandProfile).filter_by(broker_firm_id="alpha", scope_key="firm").one()
    row.card_prefix = "ALP"
    db.add(BrandProfile(broker_firm_id="alpha", client_id="a2", scope_key="a2", card_prefix="ZZ"))
    db.flush()
    assert firm_card_prefix(db, "alpha") == "ALP"
    # A company row cannot change it.
    assert resolve_brand(db, "alpha", "a2").card_prefix == "ALP"


def test_a_card_prefix_changes_only_the_prefix() -> None:
    legacy = platform_member_id("a1", "S1")
    branded = platform_member_id("a1", "S1", "ALP")
    assert legacy.startswith("INS-") and branded.startswith("ALP-")
    assert legacy.split("-", 1)[1] == branded.split("-", 1)[1]
    dependant = platform_dependant_id("a1", "S1", "spouse", "ALP")
    assert dependant.startswith(branded + "-")
