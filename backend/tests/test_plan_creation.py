from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user
from app.db.session import SessionLocal
from app.main import app
from app.models import Client, Plan, PolicyYear, Product, ProductSetup, User

CLIENT_ID = "00000000-0000-0000-0000-00000000ac01"
POLICY_YEAR_ID = "00000000-0000-0000-0000-00000000ac02"
PRODUCT_ID = "00000000-0000-0000-0000-00000000ac03"
USER_ID = "00000000-0000-0000-0000-00000000ac04"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=USER_ID,
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _seed_plan_setup():
    from scripts.seed_demo import seed

    seed()
    with SessionLocal() as db:
        db.add(
            Client(
                id=CLIENT_ID,
                name="Plan Creation Co",
                broker_firm_id=DEMO_BROKER_FIRM_ID,
            )
        )
        db.add(
            User(
                id=USER_ID,
                broker_firm_id=DEMO_BROKER_FIRM_ID,
                email="plans@example.test",
                display_name="Plan Tester",
                role="broker_admin",
                status="active",
            )
        )
        db.flush()
        db.add(
            PolicyYear(
                id=POLICY_YEAR_ID,
                client_id=CLIENT_ID,
                year=2039,
                start_date=date(2039, 1, 1),
                end_date=date(2039, 12, 31),
                status="active",
            )
        )
        db.add(
            Product(
                id=PRODUCT_ID,
                client_id=CLIENT_ID,
                code="GHS",
                display_name="Group Hospital & Surgical",
            )
        )
        db.flush()
        db.add(
            ProductSetup(
                policy_year_id=POLICY_YEAR_ID,
                product_code="GHS",
                answers={
                    "plans": [
                        {"code": "1", "label": "Plan 1", "selected": True},
                        {"code": "2", "label": "Plan 2", "selected": True},
                    ],
                    "sob": {
                        "columns": [
                            {
                                "id": "shared",
                                "label": "All plans",
                                "plan_codes": ["1", "2"],
                            }
                        ],
                        "items": [],
                    },
                },
            )
        )
        db.add_all(
            [
                Plan(
                    product_id=PRODUCT_ID,
                    policy_year_id=POLICY_YEAR_ID,
                    code=str(number),
                    display_name=f"Plan {number}",
                )
                for number in (1, 2)
            ]
        )
        db.commit()
    app.dependency_overrides[get_current_user] = _user
    yield
    app.dependency_overrides.clear()


def test_create_plan_updates_plan_list_and_saved_setup() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/plans",
            json={
                "product_id": PRODUCT_ID,
                "policy_year_id": POLICY_YEAR_ID,
                "display_name": "Executive Plan",
                "report_label": "Private Hospital",
            },
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "3"
    assert body["display_name"] == "Executive Plan"
    assert body["source"] == "manual"

    with SessionLocal() as db:
        setup = db.query(ProductSetup).filter_by(
            policy_year_id=POLICY_YEAR_ID,
            product_code="GHS",
        ).one()
        plans = setup.answers["plans"]
        assert plans[-1] == {
            "code": "3",
            "label": "Executive Plan",
            "selected": True,
        }
        assert setup.answers["sob"]["columns"][0]["plan_codes"] == ["1", "2", "3"]
