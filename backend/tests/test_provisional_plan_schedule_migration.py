"""Data repair for already-persisted provisional benefit schedules."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Client, Plan, PolicyYear, Product, ProductSetup
from app.models.category import CategoryStatus, SourceKind
from app.models.product_setup import ProductSetupOrigin, ProductSetupStatus
from tests.test_provisional_plan_schedules import _answers


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "b4e6a8c0d2f1_backfill_provisional_plan_schedules.py"
    )
    spec = importlib.util.spec_from_file_location("provisional_schedule_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_repairs_existing_plan_and_is_idempotent(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        db.add(Client(id="client", name="Client", broker_firm_id="firm"))
        db.flush()
        db.add(
            PolicyYear(
                id="year",
                client_id="client",
                year=2030,
                start_date=date(2030, 1, 1),
                end_date=date(2030, 12, 31),
                status="draft",
            )
        )
        product = Product(id="product", code="GHS", display_name="Hospital")
        db.add(product)
        db.flush()
        db.add(
            ProductSetup(
                id="setup",
                policy_year_id="year",
                product_code="GHS",
                template_version=1,
                answers=_answers(),
                status=ProductSetupStatus.draft,
                origin=ProductSetupOrigin.placement_slip,
                origin_ref="slip",
            )
        )
        db.add(
            Plan(
                id="plan-2",
                product_id=product.id,
                policy_year_id="year",
                code="2",
                display_name="Plan 2",
                benefit_schedule={
                    "items": [
                        {
                            "number": "1",
                            "name": "Daily Room & Board",
                            "value": "1 Bed Restr.",
                            "limits": [
                                {"label": "Maximum no. of days", "value": None}
                            ],
                        },
                        {
                            "number": "2",
                            "name": "Parsed-only emergency benefit",
                            "value": "S$5,000",
                        }
                    ]
                },
                source=SourceKind.system_generated.value,
                source_ref="placement_slip://slip/GHS/sob_row_1",
                status=CategoryStatus.needs_review.value,
            )
        )
        db.commit()

    migration = _migration()
    with engine.begin() as connection:
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        first = connection.exec_driver_sql(
            "SELECT benefit_schedule FROM plans WHERE id = 'plan-2'"
        ).scalar_one()
        migration.upgrade()
        second = connection.exec_driver_sql(
            "SELECT benefit_schedule FROM plans WHERE id = 'plan-2'"
        ).scalar_one()

    assert first == second
    with Session(engine) as db:
        schedule = db.get(Plan, "plan-2").benefit_schedule
        item = schedule["items"][0]
        assert item["limits"] == [
            {"label": "Maximum no. of days", "value": "120 days"}
        ]
        assert item["kind"] == "text"
        assert item["sub_items"][0]["value"] == "Includes medication"
        assert schedule["items"][1] == {
            "number": "2",
            "name": "Parsed-only emergency benefit",
            "value": "S$5,000",
        }
    engine.dispose()
