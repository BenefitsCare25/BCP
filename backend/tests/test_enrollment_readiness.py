"""Enrollment periods expose aggregate blockers and open only from safe state."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    BrokerFirm,
    Category,
    Client,
    Employee,
    EnrollmentWindow,
    FlexScheme,
    MemberAccount,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind
from app.models.flex_scheme import FlexSchemeStatus
from app.services.enrollment_readiness import enrollment_readiness_issues


def _session() -> tuple[Session, object]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(BrokerFirm(id="firm", name="Firm"))
    db.flush()
    db.add(Client(id="client", name="Client", broker_firm_id="firm"))
    db.flush()
    db.add(
        PolicyYear(
            id="year",
            client_id="client",
            year=2032,
            start_date=date(2032, 1, 1),
            end_date=date(2032, 12, 31),
            status="draft",
        )
    )
    db.flush()
    return db, engine


def _seed_benefit(db: Session) -> tuple[Product, Category, Employee]:
    product = Product(
        id="product", client_id="client", code="MED", display_name="Medical"
    )
    db.add(product)
    db.flush()
    db.add(
        Plan(
            id="plan",
            product_id=product.id,
            policy_year_id="year",
            code="1",
            display_name="Plan 1",
            status="confirmed",
        )
    )
    category = Category(
        id="category",
        policy_year_id="year",
        product_id=product.id,
        display_name="All staff",
        raw_description="All staff",
        participation_model="compulsory",
        participation_detail={"employee": "compulsory"},
        plan_assignments={
            "plan_code": "1",
            "rate_basis": "flat",
            "premium_rate": 120.0,
            "annual_premium": 120.0,
        },
        source=SourceKind.system_generated.value,
        status=CategoryStatus.confirmed.value,
        human_modified=False,
    )
    db.add(category)
    db.flush()
    employee = Employee(
        id="employee",
        client_id="client",
        policy_year_id="year",
        staff_id="S-1",
        attribute_values={},
        derived_attribute_values={},
        matched_categories=[{"category_id": category.id, "product_code": "MED"}],
        source="csv_import",
        status="active",
    )
    db.add(employee)
    db.flush()
    return product, category, employee


def _window(db: Session) -> EnrollmentWindow:
    window = EnrollmentWindow(
        id="window",
        policy_year_id="year",
        client_id="client",
        name="2032 Flex enrollment",
        opens_at=datetime(2031, 11, 1, tzinfo=UTC),
        closes_at=datetime(2031, 11, 30, tzinfo=UTC),
        uses_flex=True,
        member_self_service=True,
    )
    db.add(window)
    db.flush()
    return window


def test_flex_readiness_tracks_scheme_wallet_and_portal_access() -> None:
    db, engine = _session()
    try:
        _product, _category, employee = _seed_benefit(db)
        window = _window(db)
        assert {issue["code"] for issue in enrollment_readiness_issues(db, window)} == {
            "portal_access_incomplete",
            "flex_scheme_not_confirmed",
            "flex_wallets_incomplete",
        }

        account = MemberAccount(
            id="account",
            client_id="client",
            email="member@example.test",
            staff_id=employee.staff_id,
            status="active",
        )
        db.add(account)
        employee.member_account_id = account.id
        employee.flex_wallet_amount = 1000.0
        employee.flex_currency = "SGD"
        db.add(
            FlexScheme(
                id="scheme",
                policy_year_id="year",
                status=FlexSchemeStatus.confirmed,
                scheme={"meta": {"currency": "SGD"}, "tiers": []},
            )
        )
        db.flush()

        assert enrollment_readiness_issues(db, window) == []
    finally:
        db.close()
        engine.dispose()


def test_duplicate_same_code_products_block_opening() -> None:
    db, engine = _session()
    try:
        _seed_benefit(db)
        window = _window(db)
        duplicate = Product(
            id="duplicate", client_id=None, code="med", display_name="Medical catalog"
        )
        db.add(duplicate)
        db.flush()
        db.add(
            Category(
                id="duplicate-category",
                policy_year_id="year",
                product_id=duplicate.id,
                display_name="All staff duplicate",
                raw_description="All staff duplicate",
                plan_assignments={"plan_code": "1"},
                source=SourceKind.system_generated.value,
                status=CategoryStatus.confirmed.value,
                human_modified=False,
            )
        )
        db.flush()

        issues = enrollment_readiness_issues(db, window)
        duplicate_issue = next(
            issue for issue in issues if issue["code"] == "duplicate_product_codes"
        )
        assert duplicate_issue["count"] == 1
        assert duplicate_issue["products"] == ["MED"]
    finally:
        db.close()
        engine.dispose()
