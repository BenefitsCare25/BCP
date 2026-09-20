"""Insurer filtering must count claims once and preserve company boundaries."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.v1 import dashboard
from app.core.auth import CurrentUser
from app.db.base import Base
from app.models import BrokerFirm, Claim, Client, Employee, Plan, PolicyYear, Product, ProductSetup
from app.models.policy_year import PolicyYearStatus
from app.services.claim_filters import claim_insurer_filter
from app.services.product_insurer import placement_insurers


@pytest.fixture
def portfolio(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        today = date(2026, 9, 19)
        monkeypatch.setattr(dashboard, "business_today", lambda: today)
        db.add(BrokerFirm(id="firm", name="Broker"))
        db.flush()
        clients = [
            Client(id=name, name=name, broker_firm_id="firm")
            for name in ("multi", "other", "hidden")
        ]
        db.add_all(clients)
        db.flush()
        for client in clients:
            db.add(
                PolicyYear(
                    id=client.id,
                    client_id=client.id,
                    year=2026,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                    status=PolicyYearStatus.active,
                )
            )
            db.flush()
            db.add(
                Employee(
                    id=client.id, client_id=client.id, policy_year_id=client.id, staff_id=client.id
                )
            )
        db.flush()
        for ident, owner, code, insurer, copies in (
            ("a", "multi", "GHS", "Alpha", 2),
            ("b", "multi", "GP", "Beta", 1),
            ("c", "other", "GHS", "Beta", 1),
            ("d", "hidden", "GHS", "Secret", 1),
        ):
            db.add(
                Product(id=ident, client_id=owner, code=code, display_name=code, insurer=insurer)
            )
            db.flush()
            for number in range(copies):
                db.add(
                    Plan(
                        product_id=ident,
                        policy_year_id=owner,
                        code=str(number),
                        display_name=str(number),
                    )
                )
        db.flush()
        for ident, owner, code, status, kind, deadline in (
            ("review-alpha", "multi", "GHS", "submitted", "insured", None),
            ("review-beta", "multi", "GP", "ai_verified", "insured", None),
            ("flex", "multi", None, "submitted", "flex", None),
            (
                "overdue-alpha",
                "multi",
                "GHS",
                "sent_to_insurer",
                "insured",
                today - timedelta(days=1),
            ),
            ("due-alpha", "multi", "GHS", "sent_to_insurer", "insured", today),
            ("other-review", "other", "GHS", "submitted", "insured", None),
            ("hidden-review", "hidden", "GHS", "submitted", "insured", None),
        ):
            db.add(
                Claim(
                    id=ident,
                    client_id=owner,
                    policy_year_id=owner,
                    employee_id=owner,
                    product_code=code,
                    claim_type=code or "Flex",
                    claim_kind=kind,
                    status=status,
                    incurred_date=today,
                    amount_claimed=Decimal("10"),
                    insurer_deadline_on=deadline,
                )
            )
        db.commit()
        monkeypatch.setattr(dashboard, "accessible_clients", lambda **kwargs: clients[:2])
        yield db
    engine.dispose()


def summary(db, insurer=None):
    return dashboard.get_summary(
        policy_year_id=None,
        insurer=insurer,
        db=db,
        user=CurrentUser(
            user_id="user", role="broker_admin", broker_firm_id="firm", client_id="multi"
        ),
    )


def test_multi_insurer_counts_and_options(portfolio):
    all_companies = summary(portfolio)
    assert all_companies.insurers == ["Alpha", "Beta"]
    assert all_companies.firm.company_count == 2
    assert all_companies.firm.claims_to_review == 4
    alpha = summary(portfolio, " ALPHA ")
    assert alpha.insurers == ["Alpha", "Beta"]
    assert [c.id for c in alpha.companies] == ["multi"]
    assert alpha.firm.member_count == 1
    assert alpha.firm.claims_to_review == alpha.firm.insured_claims_to_review == 1
    assert alpha.firm.wallet_claims_to_review == 0
    assert alpha.firm.claims_with_insurer == 2
    assert alpha.firm.claims_overdue == 1  # due today is not overdue
    beta = summary(portfolio, "Beta")
    assert beta.firm.company_count == 2
    assert beta.firm.claims_to_review == 2


def test_unknown_and_inaccessible_insurer_cannot_reveal_companies(portfolio):
    for name in ("Secret", "missing", "%"):
        result = summary(portfolio, name)
        assert result.companies == []
        assert result.firm.company_count == result.firm.claims_to_review == 0
        assert "Secret" not in result.insurers


def test_claim_filter_matches_company_code_without_plan_fanout(portfolio):
    placements = placement_insurers(portfolio, ["multi", "other"])
    ids = set(portfolio.scalars(select(Claim.id).where(claim_insurer_filter("Alpha", placements))))
    assert ids == {"review-alpha", "overdue-alpha", "due-alpha"}


def test_renewal_resolves_original_year_placement_not_shared_catalog(portfolio):
    portfolio.add(
        PolicyYear(
            id="prior",
            client_id="multi",
            year=2025,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=PolicyYearStatus.archived,
        )
    )
    portfolio.flush()
    portfolio.add_all(
        [
            Plan(product_id="a", policy_year_id="prior", code="old", display_name="Old"),
            ProductSetup(
                policy_year_id="prior",
                product_code="ghs",
                answers={"header": {"insurer": "Previous"}},
            ),
            ProductSetup(
                policy_year_id="multi",
                product_code="GHS",
                answers={"header": {"insurer": ["Renewal", "Quotation only"]}},
            ),
            Claim(
                id="old-claim",
                client_id="multi",
                policy_year_id="prior",
                employee_id="multi",
                product_code=" ghs ",
                claim_type="GHS",
                claim_kind="insured",
                status="submitted",
                incurred_date=date(2025, 6, 1),
                amount_claimed=Decimal("10"),
            ),
        ]
    )
    portfolio.flush()
    placements = placement_insurers(portfolio, ["prior", "multi", "other"])
    assert placements[("prior", "GHS")] == "Previous"
    assert placements[("multi", "GHS")] == "Renewal"
    old_ids = set(
        portfolio.scalars(select(Claim.id).where(claim_insurer_filter("previous", placements)))
    )
    new_ids = set(
        portfolio.scalars(select(Claim.id).where(claim_insurer_filter("Renewal", placements)))
    )
    assert old_ids == {"old-claim"}
    assert new_ids == {"review-alpha", "overdue-alpha", "due-alpha"}
    assert summary(portfolio).insurers == ["Beta", "Renewal"]
    assert summary(portfolio, "Renewal").firm.claims_to_review == 1
    assert summary(portfolio, "Alpha").companies == []


@pytest.mark.parametrize("answers", [{"header": {"insurer": ""}}, {"header": "malformed"}, {}])
def test_authoritative_blank_suppresses_legacy_insurer(portfolio, answers):
    portfolio.add(ProductSetup(policy_year_id="multi", product_code="GHS", answers=answers))
    portfolio.flush()
    assert summary(portfolio).insurers == ["Beta"]
    assert summary(portfolio, "Alpha").companies == []
    placements = placement_insurers(portfolio, ["multi"])
    assert (
        list(portfolio.scalars(select(Claim.id).where(claim_insurer_filter("Alpha", placements))))
        == []
    )


def test_shared_global_catalog_insurer_is_never_a_placement(portfolio):
    product = portfolio.get(Product, "a")
    product.client_id = None
    portfolio.flush()
    assert summary(portfolio).insurers == ["Beta"]
    assert summary(portfolio, "Alpha").companies == []
    # An explicit per-year setup may legitimately place the shared product.
    portfolio.add(
        ProductSetup(
            policy_year_id="multi", product_code="GHS", answers={"header": {"insurer": "Explicit"}}
        )
    )
    portfolio.flush()
    assert summary(portfolio, "Explicit").firm.claims_to_review == 1
