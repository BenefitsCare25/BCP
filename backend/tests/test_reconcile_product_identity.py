"""Guardrails for the one-time duplicate product identity repair."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Category, Client, PolicyYear, Product, ProductSetup
from app.models.category import CategoryStatus, SourceKind
from app.models.product_setup import ProductSetupOrigin, ProductSetupStatus
from scripts.reconcile_product_identity import reconcile


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
                year=2033,
                start_date=date(2033, 1, 1),
                end_date=date(2033, 12, 31),
                status="draft",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _seed_duplicate(db: Session) -> tuple[Category, Category]:
    source_product = Product(
        id="catalog-product", client_id=None, code="GCGP", display_name="GCGP"
    )
    target_product = Product(
        id="client-product", client_id="client", code="GCGP", display_name="GCGP"
    )
    db.add_all([source_product, target_product])
    db.flush()
    db.add(
        ProductSetup(
            id="setup",
            policy_year_id="year",
            product_code="GCGP",
            answers={},
            status=ProductSetupStatus.confirmed,
            origin=ProductSetupOrigin.placement_slip,
            materialized_product_id=target_product.id,
        )
    )
    common = {
        "policy_year_id": "year",
        "display_name": "Thailand employees",
        "raw_description": "Thailand employees",
        "participation_model": "voluntary",
        "participation_detail": {"employee": "voluntary"},
        "status": CategoryStatus.needs_review.value,
        "rule_status": "needs_review",
    }
    source = Category(
        id="placement-category",
        product_id=source_product.id,
        matching_rule={"in": ["job_category", ["J1", "J2"]]},
        plan_assignments={
            "plan_code": "1",
            "premium_rate": 378.0,
            "annual_premium": 186_732.0,
            "dependant_rate": 396.9,
        },
        source=SourceKind.ai_extracted.value,
        source_ref="placement_slip://slip/GCGP/row_24",
        human_modified=False,
        **common,
    )
    target = Category(
        id="setup-category",
        product_id=target_product.id,
        matching_rule={"and": [{"in": ["job_category", ["J1", "J2"]]}]},
        plan_assignments={
            "plan_code": "1",
            "premium_rate": 378.0,
            "annual_premium": 186_732.0,
        },
        source=SourceKind.manual.value,
        source_ref="product_setup",
        human_modified=True,
        **common,
    )
    db.add_all([source, target])
    db.commit()
    return source, target


def _dry_run(db: Session) -> dict[str, object]:
    return reconcile(
        db,
        broker_firm_id="firm",
        policy_year_id="year",
        code="GCGP",
        operator_id="test-operator",
        apply=False,
        backup_path=None,
    )


def test_dry_run_accepts_untouched_ai_row_and_equivalent_human_duplicate(
    db: Session,
) -> None:
    source, target = _seed_duplicate(db)

    result = _dry_run(db)

    assert result["ready"] is True
    assert result["verified_human_duplicates_to_remove"] == 1
    assert db.get(Category, source.id).product_id == "catalog-product"
    assert db.get(Category, target.id).product_id == "client-product"


def test_dry_run_refuses_materially_different_human_duplicate(db: Session) -> None:
    _source, target = _seed_duplicate(db)
    target.plan_assignments = {
        **(target.plan_assignments or {}),
        "annual_premium": 999.0,
    }
    db.commit()

    with pytest.raises(RuntimeError, match="not semantically identical"):
        _dry_run(db)


def test_dry_run_refuses_human_modified_placement_row(db: Session) -> None:
    source, _target = _seed_duplicate(db)
    source.human_modified = True
    db.commit()

    with pytest.raises(RuntimeError, match="untouched placement-slip"):
        _dry_run(db)
