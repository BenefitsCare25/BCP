"""Placement-slip setup materialization must keep one product identity per code."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.v1.product_setups import _adopt_slip_artifacts
from app.core.auth import CurrentUser
from app.db.base import Base
from app.models import (
    AuditLog,
    BrokerFirm,
    Category,
    Client,
    FlexPricing,
    Plan,
    PolicyYear,
    Product,
    ProductTerm,
)
from app.models.category import CategoryStatus, SourceKind


def _category(category_id: str, product_id: str) -> Category:
    return Category(
        id=category_id,
        policy_year_id="year",
        product_id=product_id,
        display_name="All staff",
        raw_description="All staff",
        plan_assignments={"plan_code": "1", "annual_premium": 120.0},
        source=SourceKind.system_generated.value,
        source_ref="placement_slip://slip/GCGP/row_1",
        status=CategoryStatus.needs_review.value,
        human_modified=False,
    )


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BrokerFirm(id="firm", name="Firm"))
        session.flush()
        session.add(Client(id="client", name="Client", broker_firm_id="firm"))
        session.flush()
        session.add(
            PolicyYear(
                id="year",
                client_id="client",
                year=2030,
                start_date=date(2030, 1, 1),
                end_date=date(2030, 12, 31),
                status="draft",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="operator",
        broker_firm_id="firm",
        client_id="client",
        role="broker_admin",
    )


def test_adopts_slip_categories_terms_pricing_and_removes_generated_plans(
    db: Session,
) -> None:
    source = Product(id="global-product", client_id=None, code="GCGP", display_name="GCGP")
    target = Product(
        id="client-product", client_id="client", code="GCGP", display_name="GCGP"
    )
    db.add_all([source, target])
    db.flush()
    category = _category("slip-category", source.id)
    db.add(category)
    db.add(
        Plan(
            id="slip-plan",
            product_id=source.id,
            policy_year_id="year",
            code="1",
            display_name="Plan 1",
            source=SourceKind.system_generated.value,
            status=CategoryStatus.needs_review.value,
        )
    )
    db.add(
        ProductTerm(
            id="slip-term",
            policy_year_id="year",
            product_id=source.id,
            coverage_start=date(2030, 1, 1),
            coverage_end=date(2030, 12, 31),
        )
    )
    db.add(
        FlexPricing(
            id="pricing",
            policy_year_id="year",
            client_id="client",
            pricing={"products": {source.id: {"age_bands": [], "price_tags": {}}}},
        )
    )
    db.flush()

    _adopt_slip_artifacts(db, _user(), target, "year")

    assert db.get(Category, category.id).product_id == target.id
    assert db.get(Plan, "slip-plan") is None
    assert db.get(ProductTerm, "slip-term").product_id == target.id
    pricing = db.get(FlexPricing, "pricing").pricing["products"]
    assert source.id not in pricing
    assert target.id in pricing
    assert db.query(AuditLog).filter_by(action="adopt_placement_slip_artifacts").one()


def test_refuses_to_merge_competing_category_sets(db: Session) -> None:
    source = Product(id="source-2", client_id=None, code="GCSP", display_name="GCSP")
    target = Product(
        id="target-2", client_id="client", code="GCSP", display_name="GCSP"
    )
    db.add_all([source, target])
    db.flush()
    db.add_all([_category("source-category", source.id), _category("target-category", target.id)])
    db.flush()

    with pytest.raises(HTTPException) as exc:
        _adopt_slip_artifacts(db, _user(), target, "year")

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "duplicate_product_configuration"
    assert db.get(Category, "source-category").product_id == source.id
    assert db.get(Category, "target-category").product_id == target.id
