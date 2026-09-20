"""Submission guard separates paid, committed and pending use of a shared wallet."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Claim, Client, Employee, FlexScheme, PolicyYear
from app.services import flex_submission as service


@pytest.fixture
def case(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BrokerFirm(id="firm", name="Test firm"))
        db.flush()
        for key in ("client", "other"):
            db.add(Client(id=key, name=key, broker_firm_id="firm"))
        db.flush()
        for key in ("year", "other"):
            db.add(
                PolicyYear(
                    id=key,
                    client_id="client",
                    year=2026,
                    start_date=date(2026, 1 if key == "year" else 2, 1),
                    end_date=date(2026, 12, 31),
                )
            )
        db.flush()
        employee = Employee(id="ee", client_id="client", policy_year_id="year", staff_id="one")
        db.add_all(
            [
                employee,
                Employee(id="other", client_id="client", policy_year_id="year", staff_id="two"),
            ]
        )
        db.flush()
        scheme = FlexScheme(id="scheme", policy_year_id="year", scheme={"meta": {}})
        claim = Claim(
            id="new",
            client_id="client",
            policy_year_id="year",
            employee_id="ee",
            claim_kind="flex",
            status="draft",
            claim_type="Flex",
            flex_category_name="Dental",
            incurred_date=date(2026, 9, 1),
            amount_claimed=Decimal("20"),
            currency="SGD",
        )
        db.add_all([employee, scheme, claim])
        db.flush()
        monkeypatch.setattr(
            service,
            "build_member_statement",
            lambda *_: SimpleNamespace(
                flex=SimpleNamespace(flex_balance=100, wallet_amount=150),
            ),
        )
        yield db, employee, scheme, claim
    engine.dispose()


def previous(db, **changes):
    values = dict(
        id="old",
        client_id="client",
        policy_year_id="year",
        employee_id="ee",
        claim_kind="flex",
        status="paid",
        claim_type="Flex",
        flex_category_name="Optical",
        incurred_date=date(2026, 8, 1),
        amount_claimed=Decimal("100"),
        amount_approved=Decimal("100"),
        payment_amount=Decimal("100"),
        currency="SGD",
    )
    values.update(changes)
    db.add(Claim(**values))
    db.flush()


def configure(scheme, basis):
    scheme.scheme = {"meta": {"claim_submission_basis": basis}}


@pytest.mark.parametrize("operation", ["payment", "assessment"])
def test_payment_mutations_take_wallet_lock_without_submission_gate(monkeypatch, operation):
    from unittest.mock import MagicMock

    from app.api.v1 import claims as api
    from app.schemas.claims import ClaimAssessmentIn, ClaimPaymentIn

    claim = Claim(claim_kind="flex", status="paid", amount_approved=Decimal("100"))
    locks = []
    monkeypatch.setattr(api, "lock_claim_for_mutation", lambda *_: claim)
    monkeypatch.setattr(api, "is_replayed_claim_command", lambda *_: False)
    monkeypatch.setattr(api, "lock_claim_utilization_bucket", lambda *_: locks.append("wallet"))

    def stop(*_):
        raise HTTPException(409, "stop after lock")

    if operation == "payment":
        monkeypatch.setattr(api, "assert_transition", stop)
        action = api.record_claim_payment
        body = ClaimPaymentIn(amount=20, paid_on=date(2026, 9, 19))
    else:
        monkeypatch.setattr(api, "assert_settlement_amendable", stop)
        action = api.update_claim_assessment
        body = ClaimAssessmentIn(payment_amount=20)
    with pytest.raises(HTTPException, match="stop after lock"):
        action(body=body, claim=claim, user=None, db=MagicMock(), idempotency_key=None)
    assert locks == ["wallet"]


@pytest.mark.parametrize("original_kind", ["insured", "flex"])
def test_amendment_into_wallet_locks_before_changing_kind(monkeypatch, original_kind):
    from app.schemas.claims import ClaimAmendIn
    from app.services.claims import apply_claim_amendment

    claim = Claim(claim_kind=original_kind)

    def stop_before_mutation(*_):
        assert claim.claim_kind == original_kind
        raise HTTPException(409, "wallet lock taken")

    monkeypatch.setattr(service, "lock_flex_wallet", stop_before_mutation)
    with pytest.raises(HTTPException, match="wallet lock taken"):
        apply_claim_amendment(
            None, claim, ClaimAmendIn(claim_kind="flex"), None,
            allowed=frozenset({"claim_kind"}), actor="member",
        )


def test_default_keeps_existing_submission_behavior(case):
    db, employee, _, claim = case
    previous(db)
    service.assert_flex_submission_allowed(db, claim, employee)


@pytest.mark.parametrize("basis", ["paid", "approved", "reserved"])
def test_exhausted_wallet_blocks_across_categories(case, basis):
    db, employee, scheme, claim = case
    configure(scheme, basis)
    previous(db)
    with pytest.raises(HTTPException) as error:
        service.assert_flex_submission_allowed(db, claim, employee)
    assert error.value.detail["code"] == "flex_wallet_exhausted"


def test_paid_basis_uses_actual_payment_not_approval(case):
    db, employee, scheme, claim = case
    configure(scheme, "paid")
    previous(db, payment_amount=Decimal("50"))
    service.assert_flex_submission_allowed(db, claim, employee)


def test_new_broker_log_case_also_honors_exhaustion(case):
    db, employee, scheme, claim = case
    configure(scheme, "paid")
    previous(db)
    claim.status = "submitted"
    claim.case_type = "log"
    with pytest.raises(HTTPException) as error:
        service.assert_flex_submission_allowed(db, claim, employee, new_case=True)
    assert error.value.detail["code"] == "flex_wallet_exhausted"


@pytest.mark.parametrize(
    "basis,blocked", [("paid", False), ("approved", False), ("reserved", True)]
)
def test_pending_only_reserves_when_configured(case, basis, blocked):
    db, employee, scheme, claim = case
    configure(scheme, basis)
    previous(db, status="submitted", amount_approved=None, payment_amount=None)
    if blocked:
        with pytest.raises(HTTPException):
            service.assert_flex_submission_allowed(db, claim, employee)
    else:
        service.assert_flex_submission_allowed(db, claim, employee)


@pytest.mark.parametrize("change", [{"status": "needs_info"}, {"claim_kind": "insured"}])
def test_existing_replies_and_insurance_remain_available(case, change):
    db, employee, scheme, claim = case
    configure(scheme, "paid")
    previous(db)
    for field, value in change.items():
        setattr(claim, field, value)
    service.assert_flex_submission_allowed(db, claim, employee)


@pytest.mark.parametrize(
    "other",
    [
        {"employee_id": "other"},
        {"client_id": "other"},
        {"policy_year_id": "other"},
        {"status": "rejected"},
    ],
)
def test_other_wallets_and_reversed_claims_do_not_block(case, other):
    db, employee, scheme, claim = case
    configure(scheme, "paid")
    previous(db, **other)
    service.assert_flex_submission_allowed(db, claim, employee)


def test_unconverted_pending_claim_does_not_count_foreign_amount_as_sgd(case):
    db, employee, scheme, claim = case
    configure(scheme, "reserved")
    previous(db, status="submitted", currency="USD", amount_converted=None)
    with pytest.raises(HTTPException) as error:
        service.assert_flex_submission_allowed(db, claim, employee)
    assert error.value.status_code == 409


@pytest.mark.parametrize("value", [None, True, [], "unknown"])
def test_submission_setting_rejects_invalid_basis(value):
    assert service.submission_rule_errors({"meta": {"claim_submission_basis": value}})
