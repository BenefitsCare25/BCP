"""Placement-slip export — configured products rendered back to .xlsx.

Covers the workbook shape (Overview + one sheet per product + Unassigned),
the category/rate table incl. per-tier sub-rows, the SOB plan fold, the
ProductTerm coverage override, and the export audit trail.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_placement_slip_export.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402
from io import BytesIO  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    Category,
    Client,
    Plan,
    PolicyYear,
    Product,
    ProductTerm,
    User,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000015e000"
PY_ID = "00000000-0000-0000-0000-00000015e001"
GHS_ID = "00000000-0000-0000-0000-00000015e010"
GTL_ID = "00000000-0000-0000-0000-00000015e011"
USER_ID = "00000000-0000-0000-0000-00000015e0ff"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID, role="broker_admin",
    )


def _sob(item_value: str) -> dict:
    return {
        "items": [
            {
                "number": "1", "name": "Daily Room & Board",
                "value": item_value, "note": None,
                "limits": [{"label": "Maximum no. of days", "value": "120 days"}],
                "sub_items": [], "properties": {},
            },
        ]
    }


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    from scripts.seed_demo import seed
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Slip Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.add(User(
            id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
            email="broker@slip.co", display_name="Broker One",
            role="broker_admin", status="active",
        ))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2034,
            start_date=date(2034, 1, 1), end_date=date(2034, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.add_all([
            Product(
                id=GHS_ID, client_id=CLIENT_ID, code="GHS",
                display_name="Group Hospital & Surgical", insurer="AIA",
            ),
            Product(
                id=GTL_ID, client_id=CLIENT_ID, code="GTL",
                display_name="Group Term Life", insurer="Singlife",
            ),
        ])
        s.flush()
        # GTL renews off-cycle — its sheet must show the term window, not the PY's.
        s.add(ProductTerm(
            policy_year_id=PY_ID, product_id=GTL_ID,
            coverage_start=date(2034, 4, 1), coverage_end=date(2035, 3, 31),
        ))
        s.add_all([
            Category(
                policy_year_id=PY_ID, product_id=GHS_ID, priority=1,
                display_name="All Executives", raw_description="All Executives",
                participation_detail={"employee": "compulsory", "raw": "Compulsory"},
                plan_assignments={
                    "plan_code": "1", "insured": "Slip Co Pte Ltd",
                    "num_employees": 40, "sum_insured": 100000.0,
                    "annual_premium": 12000.0,
                    "rate_tiers": {
                        "EO": {"rate": 250.0, "premium": 10000.0},
                        "SO": {"rate": 300.0, "premium": 2000.0},
                    },
                    "tier_labels": {"SO": "Spouse"},
                },
            ),
            Category(
                policy_year_id=PY_ID, product_id=GTL_ID, priority=2,
                display_name="All Staff", raw_description="All Staff",
                participation_model="voluntary",
                plan_assignments={
                    "plan_code": "1", "rate_basis": "age_banded",
                    "voluntary_rates": [
                        {"label": "16 - 30", "min": 16, "max": 30, "rate": 0.55},
                        {"label": "31 - 35", "min": 31, "max": 35, "rate": 0.72},
                    ],
                },
            ),
            Category(
                policy_year_id=PY_ID, product_id=None, priority=3,
                display_name="Mystery cohort", raw_description="Mystery cohort",
            ),
        ])
        # Two GHS plans with differing R&B values → two SOB columns; the GTL
        # plan carries cover data but no schedule → Basis of Cover table only.
        s.add_all([
            Plan(
                product_id=GHS_ID, policy_year_id=PY_ID, code="1",
                display_name="Plan 1", benefit_schedule=_sob("S$650 per day"),
            ),
            Plan(
                product_id=GHS_ID, policy_year_id=PY_ID, code="2",
                display_name="Plan 2", benefit_schedule=_sob("S$450 per day"),
            ),
            Plan(
                product_id=GTL_ID, policy_year_id=PY_ID, code="1",
                display_name="Plan 1",
                cover_description="24x basic monthly salary",
                annual_policy_limit="S$500,000",
            ),
        ])
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _download(client: TestClient):
    res = client.get(f"/api/v1/policy-years/{PY_ID}/reports/placement-slip")
    assert res.status_code == 200, res.text
    assert res.content[:2] == b"PK"
    return load_workbook(BytesIO(res.content))


def _cells(ws) -> list[list]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def test_workbook_shape_and_overview(client: TestClient) -> None:
    wb = _download(client)
    assert wb.sheetnames == ["Overview", "GHS", "GTL", "Unassigned"]

    rows = _cells(wb["Overview"])
    by_code = {r[0]: r for r in rows if r and r[0] in ("GHS", "GTL")}
    assert by_code["GHS"][1:] == [
        "Group Hospital & Surgical", "AIA",
        "01 Jan 2034 to 31 Dec 2034", 1, 2,
    ]
    # Coverage window comes from the ProductTerm override, not the PY span.
    assert by_code["GTL"][3] == "01 Apr 2034 to 31 Mar 2035"


def test_category_table_with_tier_rows(client: TestClient) -> None:
    ws = _download(client)["GHS"]
    rows = _cells(ws)
    header_i = next(i for i, r in enumerate(rows) if r[0] == "Insured")
    cat = rows[header_i + 1]
    assert cat[:2] == ["Slip Co Pte Ltd", "All Executives"]
    assert (cat[2], cat[3], cat[4]) == ("Compulsory", "1", 40)
    assert (cat[6], cat[8]) == (100000.0, 12000.0)
    # Tier sub-rows: canonical label for EO; the slip's own label for SO.
    tiers = {str(r[1]).strip(): (r[7], r[8]) for r in rows[header_i + 2 : header_i + 4]}
    assert tiers["EO — Employee Only"] == (250.0, 10000.0)
    assert tiers["SO — Spouse"] == (300.0, 2000.0)


def test_sob_fold_and_voluntary_rates(client: TestClient) -> None:
    wb = _download(client)
    ghs = _cells(wb["GHS"])
    sob_i = next(i for i, r in enumerate(ghs) if r[0] == "Schedule of Benefits")
    # Two plans with differing values stay two columns.
    assert ghs[sob_i + 1][:4] == ["No.", "Benefit", "Plan 1", "Plan 2"]
    assert ghs[sob_i + 2][:4] == [
        "1", "Daily Room & Board", "S$650 per day", "S$450 per day",
    ]
    assert ghs[sob_i + 3][1] == "    · Maximum no. of days: 120 days"

    gtl = _cells(wb["GTL"])
    vol_i = next(
        i for i, r in enumerate(gtl)
        if str(r[0] or "").startswith("Voluntary rates")
    )
    bands = {r[0]: r[1] for r in gtl[vol_i + 3 : vol_i + 5]}
    assert bands == {"16 - 30": 0.55, "31 - 35": 0.72}
    # Basis of Cover table for the plan without a schedule.
    boc_i = next(i for i, r in enumerate(gtl) if r[0] == "Basis of Cover")
    assert gtl[boc_i + 2][:3] == ["1", "24x basic monthly salary", "S$500,000"]


def test_unassigned_sheet_and_audit(client: TestClient) -> None:
    wb = _download(client)
    unassigned = _cells(wb["Unassigned"])
    assert any(r[1] == "Mystery cohort" for r in unassigned if len(r) > 1)

    with SessionLocal() as s:
        entry = (
            s.query(AuditLog)
            .filter(
                AuditLog.entity_type == "placement_slip",
                AuditLog.action == "export",
                AuditLog.entity_id == PY_ID,
            )
            .first()
        )
        assert entry is not None
