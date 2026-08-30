"""Provisional placement-slip plans must match the guided setup projection."""
from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.v1.placement_slips import _sync_provisional_plan_schedules
from app.db.base import Base
from app.models import BrokerFirm, Client, Plan, PolicyYear, Product
from app.models.category import CategoryStatus, SourceKind


def _answers() -> dict:
    return {
        "plans": [
            {"code": "1", "label": "Plan 1", "selected": True},
            {"code": "2", "label": "Plan 2", "selected": True},
        ],
        "sob": {
            "columns": [
                {"id": "col0", "label": "Plan 1", "plan_codes": ["1"]},
                {"id": "col1", "label": "Plan 2", "plan_codes": ["2"]},
            ],
            "items": [
                {
                    "number": "1",
                    "name": "Daily Room & Board",
                    "kind": "text",
                    "note": "Shared policy wording",
                    "limits": [
                        {"label": "Maximum no. of days", "value": "120 days"}
                    ],
                    "base_value": "1 Bed Private",
                    "overrides": {"col1": "1 Bed Restr."},
                    "properties": {},
                    "sub_items": [
                        {
                            "key": "(a)",
                            "name": "Pre-hospitalisation",
                            "kind": "text",
                            "note": "With referral",
                            "limits": [],
                            "base_value": "Includes medication",
                            "overrides": {},
                        }
                    ],
                }
            ],
        },
    }


def test_sync_projects_shared_qualifiers_and_inherited_fields() -> None:
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
        db.add_all(
            [
                Plan(
                    id="plan-1",
                    product_id=product.id,
                    policy_year_id="year",
                    code="1",
                    display_name="Plan 1",
                    benefit_schedule={"items": []},
                    source=SourceKind.system_generated.value,
                    status=CategoryStatus.needs_review.value,
                ),
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
                                    {
                                        "label": "Maximum no. of days",
                                        "value": None,
                                    },
                                    {
                                        "label": "Annual parsed cap",
                                        "value": "S$50,000",
                                    },
                                ],
                                "sub_items": [
                                    {
                                        "key": "(b)",
                                        "name": "Parsed-only treatment",
                                        "value": "As charged",
                                    }
                                ],
                            },
                            {
                                "number": "2",
                                "name": "Parsed-only emergency benefit",
                                "value": "S$5,000",
                                "limits": [
                                    {"label": "Per disability", "value": "1 claim"}
                                ],
                            }
                        ]
                    },
                    source=SourceKind.system_generated.value,
                    status=CategoryStatus.needs_review.value,
                ),
            ]
        )
        db.flush()

        updated = _sync_provisional_plan_schedules(
            db, "year", product, _answers()
        )

        assert updated == 2
        schedule = db.get(Plan, "plan-2").benefit_schedule
        item = schedule["items"][0]
        assert item["value"] == "1 Bed Restr."
        assert item["kind"] == "text"
        assert item["note"] == "Shared policy wording"
        assert item["limits"] == [
            {"label": "Maximum no. of days", "value": "120 days"},
            {"label": "Annual parsed cap", "value": "S$50,000"},
        ]
        assert item["sub_items"] == [
            {
                "key": "(a)",
                "name": "Pre-hospitalisation",
                "value": "Includes medication",
                "note": "With referral",
                "limits": [],
                "kind": "text",
            },
            {
                "key": "(b)",
                "name": "Parsed-only treatment",
                "value": "As charged",
            },
        ]
        assert schedule["items"][1] == {
            "number": "2",
            "name": "Parsed-only emergency benefit",
            "value": "S$5,000",
            "limits": [{"label": "Per disability", "value": "1 claim"}],
        }
    engine.dispose()


def test_sync_preserves_human_modified_plan() -> None:
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
        plan = Plan(
            id="plan-2",
            product_id=product.id,
            policy_year_id="year",
            code="2",
            display_name="Plan 2",
            benefit_schedule={"items": [{"name": "Broker-authored"}]},
            source=SourceKind.manual.value,
            status=CategoryStatus.confirmed.value,
            human_modified=True,
        )
        db.add(plan)
        db.flush()

        updated = _sync_provisional_plan_schedules(
            db, "year", product, _answers()
        )

        assert updated == 0
        assert plan.benefit_schedule == {"items": [{"name": "Broker-authored"}]}
    engine.dispose()
