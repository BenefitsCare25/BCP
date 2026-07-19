"""Placement + quotation slip exports — configured products rendered to .xlsx.

Covers the slip-shaped sheet layout (header label block, Basis of Cover,
tiered/flat rate tables, voluntary age-banded rates, annual-premium total,
SOB plan fold), the ProductTerm coverage override, quotation-mode blanking
(insurer + every rate/premium cell), and the export audit trail.
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
        # plan carries cover data but no schedule → Plan Details table only.
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


def _download(client: TestClient, kind: str = "placement"):
    res = client.get(f"/api/v1/policy-years/{PY_ID}/reports/{kind}-slip")
    assert res.status_code == 200, res.text
    assert res.content[:2] == b"PK"
    return load_workbook(BytesIO(res.content))


def _cells(ws) -> list[list]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _row_index(rows: list[list], label: str, col: int = 0) -> int:
    return next(i for i, r in enumerate(rows) if r[col] == label)


def _blankish(v) -> bool:
    return v in (None, "")


def test_workbook_shape_and_overview(client: TestClient) -> None:
    wb = _download(client)
    assert wb.sheetnames == ["Overview", "GHS", "GTL", "Unassigned"]

    rows = _cells(wb["Overview"])
    assert rows[0][0] == "Placement Slip — Configured Products"
    by_code = {r[0]: r for r in rows if r and r[0] in ("GHS", "GTL")}
    assert by_code["GHS"][1:] == [
        "Group Hospital & Surgical", "AIA",
        "01 Jan 2034 to 31 Dec 2034", 1, 2,
    ]
    # Coverage window comes from the ProductTerm override, not the PY span.
    assert by_code["GTL"][3] == "01 Apr 2034 to 31 Mar 2035"


def test_header_label_block(client: TestClient) -> None:
    rows = _cells(_download(client)["GHS"])
    assert rows[0][0] == "Group Hospital & Surgical"

    def value_of(label: str):
        return rows[_row_index(rows, label)][2]

    assert value_of("Policyholder :") == "Slip Co"
    assert value_of("Insured :") == "Slip Co Pte Ltd"
    assert value_of("Period of Insurance :") == "01 Jan 2034 to 31 Dec 2034"
    assert value_of("Insurer :") == "AIA"
    # Unknown slip fields are emitted as labelled blanks for the broker.
    for label in ("Group :", "Pool :", "Eligibility :",
                  "Type of Administration :"):
        assert _blankish(value_of(label))
    # The unstored terms appear as labelled blank rows too.
    assert any(r[0] == "Non Evidence Limit :" for r in rows)


def test_basis_of_cover_and_tiered_rates(client: TestClient) -> None:
    rows = _cells(_download(client)["GHS"])
    basis_i = _row_index(rows, "Basis of Cover :")
    assert rows[basis_i + 1][1:8] == [
        "Insured", "Category", "Participation",
        "Plan", "* No. of employees", "* Sum Insured (S$)", None,
    ]
    assert rows[basis_i + 2][1:7] == [
        "Slip Co Pte Ltd", "All Executives", "Compulsory", "1", 40, 100000,
    ]

    rate_i = _row_index(rows, "Rate :")
    # Tier codes sit above their Rate/Premium column pairs.
    assert (rows[rate_i][3], rows[rate_i][5]) == ("EO", "SO")
    assert rows[rate_i + 1][1:8] == [
        "Insured", "Category", "Plan", "Rate", "Premium", "Rate", "Premium",
    ]
    assert rows[rate_i + 2][1:8] == [
        "Slip Co Pte Ltd", "All Executives", "1", 250, 10000, 300, 2000,
    ]

    total_i = _row_index(rows, "Annual Premium (GST-exclusive) :")
    assert rows[total_i][2] == 12000


def test_sob_fold_and_plan_details(client: TestClient) -> None:
    wb = _download(client)
    ghs = _cells(wb["GHS"])
    sob_i = _row_index(ghs, "SCHEDULE OF BENEFITS / INSURER / PLAN")
    # Two plans with differing values stay two columns.
    assert ghs[sob_i + 1][:4] == ["No.", "Benefit", "Plan 1", "Plan 2"]
    assert ghs[sob_i + 2][:4] == [
        "1", "Daily Room & Board", "S$650 per day", "S$450 per day",
    ]
    assert ghs[sob_i + 3][1] == "    · Maximum no. of days: 120 days"

    gtl = _cells(wb["GTL"])
    details_i = _row_index(gtl, "Plan Details")
    assert gtl[details_i + 2][:3] == ["1", "24x basic monthly salary", "S$500,000"]


def test_voluntary_rates_block(client: TestClient) -> None:
    gtl = _cells(_download(client)["GTL"])
    # Off-cycle term drives the sheet's period.
    assert gtl[_row_index(gtl, "Period of Insurance :")][2] == "01 Apr 2034 to 31 Mar 2035"
    vol_i = _row_index(gtl, "Voluntary Rates", col=1)
    assert gtl[vol_i + 1][1:3] == [
        "Based on Age Last Birthday", "Rate per 1,000 Sum assured (S$)",
    ]
    bands = {r[1]: r[2] for r in gtl[vol_i + 2 : vol_i + 4]}
    assert bands == {"16 - 30": 0.55, "31 - 35": 0.72}


def test_quotation_mode_blanks_insurer_and_rates(client: TestClient) -> None:
    wb = _download(client, kind="quotation")
    overview = _cells(wb["Overview"])
    assert overview[0][0] == "Quotation Slip — Configured Products"
    ghs_row = next(r for r in overview if r[0] == "GHS")
    assert _blankish(ghs_row[2])  # insurer left for the quoting insurer

    ghs = _cells(wb["GHS"])
    assert _blankish(ghs[_row_index(ghs, "Insurer :")][2])
    rate_i = _row_index(ghs, "Rate :")
    data = ghs[rate_i + 2]
    # Structure kept (insured/category/plan), every rate/premium cell blank.
    assert data[1:4] == ["Slip Co Pte Ltd", "All Executives", "1"]
    assert all(_blankish(v) for v in data[4:8])
    assert _blankish(ghs[_row_index(ghs, "Annual Premium (GST-exclusive) :")][2])

    gtl = _cells(wb["GTL"])
    vol_i = _row_index(gtl, "Voluntary Rates", col=1)
    assert all(_blankish(r[2]) for r in gtl[vol_i + 2 : vol_i + 4])
    # Basis of Cover figures stay — the insurer quotes off them.
    basis_i = _row_index(ghs, "Basis of Cover :")
    assert ghs[basis_i + 2][6] == 100000


def test_flat_premium_rows_reconcile_with_total() -> None:
    """The 'Annual Premium' total is the sum of the premiums actually PRINTED —
    block copies collapse to one shown+counted row, distinct premiums sum, and
    derived per-row premiums (equal SI) each show AND count (never blanked)."""
    from openpyxl import Workbook

    from app.services.placement_slip_export import _derived_premium, _write_flat_rates

    def cat(pa: dict) -> Category:
        return Category(
            policy_year_id=PY_ID, display_name="x", raw_description="x",
            plan_assignments=pa,
        )

    def total_and_shown(cats: list[Category]) -> tuple[float | None, list[float]]:
        ws = Workbook().active
        total = _write_flat_rates(
            ws, cats, earnings_based=False, with_label=False, blank=False
        )
        shown = [
            r[5] for r in ws.iter_rows(min_row=2, values_only=True)
            if isinstance(r[5], (int, float))
        ]
        return total, shown

    # GCGP-style: 4 cohorts all carry the plan's 186,732 → printed + counted once.
    total, shown = total_and_shown(
        [cat({"insured": "A", "annual_premium": 186732.0}) for _ in range(4)]
    )
    assert total == 186732.0 and shown == [186732.0]

    # Distinct per-category premiums all show and sum.
    total, shown = total_and_shown([
        cat({"insured": "A", "annual_premium": 100.0}),
        cat({"insured": "A", "annual_premium": 200.0}),
    ])
    assert total == 300.0 and sorted(shown) == [100.0, 200.0]

    assert _derived_premium(
        {"rate_basis": "per_1000_si", "premium_rate": 0.072, "sum_insured": 4_000_000.0}
    ) == 288.0
    # Derived premiums are genuine per-row figures: two equal-SI cohorts each
    # print 72 and the total is 144 — rows reconcile with the total.
    total, shown = total_and_shown([
        cat({"insured": "A", "rate_basis": "per_1000_si",
             "premium_rate": 0.072, "sum_insured": 1_000_000.0})
        for _ in range(2)
    ])
    assert total == 144.0 and shown == [72.0, 72.0]

    # An annotated note (string) prints once but still contributes its numeric
    # annual_premium to the total; the repeated block copy is blanked.
    total, shown = total_and_shown([
        cat({"insured": "A", "annual_premium": 3169.8,
             "premium_note": "$3,169.80 (min S$500)"}),
        cat({"insured": "A", "annual_premium": 3169.8,
             "premium_note": "$3,169.80 (min S$500)"}),
    ])
    assert total == 3169.8 and shown == []  # both cells hold the string note


def test_policy_number_operational_and_exported(client: TestClient) -> None:
    """Policy numbers are insurer-issued AFTER placement: settable on the
    current (active) year — as is all configuration now (the lock was removed);
    the placement slip shows it, the quotation leaves it blank (goes to
    prospective insurers)."""
    with SessionLocal() as s:
        s.get(PolicyYear, PY_ID).status = PolicyYearStatus.active
        s.commit()
    try:
        res = client.put(
            f"/api/v1/policy-years/{PY_ID}/product-terms/{GHS_ID}",
            json={"policy_number": "POL-2034-001"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["policy_number"] == "POL-2034-001"
        # Coverage dates are editable on an active year too (no lock).
        assert client.put(
            f"/api/v1/policy-years/{PY_ID}/product-terms/{GHS_ID}",
            json={"coverage_start": "2034-02-01", "coverage_end": "2034-12-31"},
        ).status_code == 200

        ghs = _cells(_download(client)["GHS"])
        assert ghs[_row_index(ghs, "Policy No. :")][2] == "POL-2034-001"
        ghs_q = _cells(_download(client, kind="quotation")["GHS"])
        assert _blankish(ghs_q[_row_index(ghs_q, "Policy No. :")][2])
    finally:
        client.put(
            f"/api/v1/policy-years/{PY_ID}/product-terms/{GHS_ID}",
            json={
                "policy_number": None,
                "coverage_start": None,
                "coverage_end": None,
            },
        )
        with SessionLocal() as s:
            s.get(PolicyYear, PY_ID).status = PolicyYearStatus.draft
            s.commit()


def test_unassigned_sheet_and_audit(client: TestClient) -> None:
    wb = _download(client)
    _download(client, kind="quotation")
    unassigned = _cells(wb["Unassigned"])
    assert unassigned[0][0] == "Unassigned categories"
    assert any(r[2] == "Mystery cohort" for r in unassigned if len(r) > 2)

    with SessionLocal() as s:
        reports = {
            (e.after or {}).get("report")
            for e in s.query(AuditLog)
            .filter(
                AuditLog.entity_type == "placement_slip",
                AuditLog.action == "export",
                AuditLog.entity_id == PY_ID,
            )
            .all()
        }
        assert {"placement-slip", "quotation-slip"} <= reports
