"""Flex wallet ledger, utilisation summary and the leaver reports.

The invariant under test throughout: the ledger, its summary and the leaver
sheet are three views of ONE resolution (`flex_pricing_resolver.summarize_
employee`). If they ever disagree about a member's balance, the derived-not-
stored design has failed at the only thing it was chosen for.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_flex_ledger.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import UTC, date, datetime  # noqa: E402
from io import BytesIO  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Claim,
    Client,
    Employee,
    PolicyYear,
    User,
)
from app.models.claim import CLAIM_STATUS_APPROVED, CLAIM_STATUS_PAID  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-0000000f1000"
PY_ID = "00000000-0000-0000-0000-0000000f1001"
USER_ID = "00000000-0000-0000-0000-0000000f10ff"

EMP_WALLET = "00000000-0000-0000-0000-0000000f1101"
EMP_LEAVER = "00000000-0000-0000-0000-0000000f1102"
EMP_NOFLEX = "00000000-0000-0000-0000-0000000f1103"

NOW = datetime.now(UTC)


def _user(role: str = "broker_admin") -> CurrentUser:
    return CurrentUser(
        user_id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID, role=role,
    )


def _emp(eid: str, staff: str, name: str, **kw) -> Employee:
    defaults = dict(
        client_id=CLIENT_ID, policy_year_id=PY_ID, staff_id=staff,
        employee_name=name, attribute_values={}, derived_attribute_values={},
        matched_categories=[], source="csv_import", status="active",
    )
    defaults.update(kw)
    return Employee(id=eid, **defaults)


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    from scripts.seed_demo import seed
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Flexi Co", slug="flexi-co",
                     broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.add(User(id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
                   email="ops@flexi.co", display_name="Fay Ops",
                   role="broker_admin", status="active"))
        s.flush()
        s.add(PolicyYear(id=PY_ID, client_id=CLIENT_ID, year=2039,
                         start_date=date(2039, 1, 1), end_date=date(2039, 12, 31),
                         status=PolicyYearStatus.active))
        s.flush()
        s.add_all([
            _emp(EMP_WALLET, "FX-1", "Wanda Wallet",
                 flex_wallet_amount=2000.0, flex_currency="SGD",
                 flex_tier_name="Manager", flex_family_status="EO",
                 flex_assigned_at=NOW,
                 attribute_values={
                     "entity": "Flexi Co Pte Ltd",
                     "id_no": "S1111111A",
                     "category": "Manager",
                     "date_of_hire": "2020-01-06",
                 }),
            _emp(EMP_LEAVER, "FX-2", "Leo Leaver",
                 status="terminated", terminated_effective=date(2039, 6, 30),
                 flex_wallet_amount=1000.0, flex_currency="SGD",
                 flex_assigned_at=NOW,
                 attribute_values={
                     "entity": "Flexi Co Pte Ltd",
                     "id_no": "S2222222B",
                     "category": "Officer",
                 }),
            # No wallet, no leave, no pricing → flex does not apply.
            _emp(EMP_NOFLEX, "FX-3", "Nora Noflex",
                 attribute_values={"entity": "Flexi Co Pte Ltd"}),
        ])
        s.flush()
        s.add_all([
            # Settled — spends the wallet.
            Claim(client_id=CLIENT_ID, policy_year_id=PY_ID,
                  employee_id=EMP_WALLET, claim_kind="flex",
                  flex_category_name="Wellness", claim_type="Wellness",
                  incurred_date=date(2039, 2, 1), amount_claimed=300.0,
                  amount_approved=300.0, currency="SGD",
                  status=CLAIM_STATUS_APPROVED, decided_at=NOW,
                  reference_no="FLEXICO-000001"),
            # PAID — still spent. Before SETTLED_STATUSES this fell into
            # "pending" and handed the wallet back.
            Claim(client_id=CLIENT_ID, policy_year_id=PY_ID,
                  employee_id=EMP_WALLET, claim_kind="flex",
                  flex_category_name="Dental", claim_type="Dental",
                  incurred_date=date(2039, 3, 1), amount_claimed=200.0,
                  amount_approved=200.0, payment_amount=200.0,
                  paid_on=date(2039, 4, 1), currency="SGD",
                  status=CLAIM_STATUS_PAID, decided_at=NOW,
                  reference_no="FLEXICO-000002"),
            # In flight — reported, never subtracted.
            Claim(client_id=CLIENT_ID, policy_year_id=PY_ID,
                  employee_id=EMP_WALLET, claim_kind="flex",
                  flex_category_name="Optical", claim_type="Optical",
                  incurred_date=date(2039, 5, 1), amount_claimed=150.0,
                  currency="SGD", status="submitted",
                  reference_no="FLEXICO-000003"),
            Claim(client_id=CLIENT_ID, policy_year_id=PY_ID,
                  employee_id=EMP_LEAVER, claim_kind="flex",
                  flex_category_name="Wellness", claim_type="Wellness",
                  incurred_date=date(2039, 5, 20), amount_claimed=120.0,
                  amount_approved=120.0, currency="SGD",
                  status=CLAIM_STATUS_APPROVED, decided_at=NOW,
                  reference_no="FLEXICO-000004"),
        ])
        s.commit()
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _sheet(resp):
    assert resp.status_code == 200, resp.text
    ws = load_workbook(BytesIO(resp.content)).active
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], rows[1:]


def _get(client, path, **params):
    return _sheet(
        client.get(f"/api/v1/policy-years/{PY_ID}/reports/{path}", params=params)
    )


# ── Ledger ───────────────────────────────────────────────────────────────────

def test_ledger_emits_a_dated_row_per_movement(client):
    header, rows = _get(client, "wallet-utilisation")
    mine = [r for r in rows if r[header.index("Staff ID")] == "FX-1"]
    descriptions = [r[header.index("Description")] for r in mine]
    assert "Wallet Allocation" in descriptions
    assert descriptions.count("Claim Payment") == 2  # approved + paid
    assert all(r[header.index("Date of Transaction")] is not None for r in mine)


def test_ledger_keeps_the_incumbents_unbuilt_columns_blank(client):
    """They are blank in the incumbent's own live file too — kept for a
    column-for-column diff, never invented."""
    header, rows = _get(client, "wallet-utilisation")
    for col in ("B/F Allocation Amt", "Deals Amt", "Salary Deduction"):
        assert col in header
        assert all(r[header.index(col)] in (None, "") for r in rows)


def test_ledger_skips_members_flex_does_not_apply_to(client):
    _, rows = _get(client, "wallet-utilisation")
    assert "FX-3" not in {r[1] for r in rows}


# ── Summary ──────────────────────────────────────────────────────────────────

def test_summary_subtracts_settled_claims_and_not_pending_ones(client):
    """300 approved + 200 paid = 500 spent; the 150 in flight is reported
    separately and never subtracted."""
    header, rows = _get(client, "wallet-utilisation-summary")
    row = next(r for r in rows if r[header.index("Staff ID")] == "FX-1")
    assert row[header.index("Claims Payment Amt")] == 500.0
    assert row[header.index("Pending Claims Payment Amt")] == 150.0
    assert row[header.index("Total Allocation Amt")] == 2000.0
    assert row[header.index("Balance Available Allocation Amt")] == 1500.0


def test_summary_terms_add_up_to_the_total(client):
    """The printed terms must reconcile with the printed total — the defect the
    broker coverage pane had when the leave trade was left out."""
    header, rows = _get(client, "wallet-utilisation-summary")
    row = next(r for r in rows if r[header.index("Staff ID")] == "FX-1")

    def cell(name):
        return row[header.index(name)] or 0

    terms = (
        cell("Selection Amt")
        + cell("Claims Payment Amt")
        + cell("Buy Leave Amt")
        - cell("Sell Leave Amt")
    )
    assert round(terms, 2) == cell("Total Utilized Amt")
    assert round(
        cell("Total Allocation Amt") - cell("Total Utilized Amt"), 2
    ) == cell("Balance Available Allocation Amt")


def test_summary_and_ledger_agree(client):
    """Two views of one resolution — the whole reason the ledger is derived."""
    lheader, lrows = _get(client, "wallet-utilisation")
    sheader, srows = _get(client, "wallet-utilisation-summary")
    ledger_claims = sum(
        r[lheader.index("Claims Payment Amt")] or 0
        for r in lrows
        if r[lheader.index("Staff ID")] == "FX-1"
    )
    summary_row = next(r for r in srows if r[sheader.index("Staff ID")] == "FX-1")
    assert ledger_claims == summary_row[sheader.index("Claims Payment Amt")]


def test_summary_masks_nric_by_default(client):
    header, rows = _get(client, "wallet-utilisation-summary")
    row = next(r for r in rows if r[header.index("Staff ID")] == "FX-1")
    assert row[header.index("Identification No.")] != "S1111111A"


# ── Leavers ──────────────────────────────────────────────────────────────────

def test_leaver_summary_lists_only_in_period_leavers(client):
    header, rows = _get(client, "leaver-summary")
    assert {r[header.index("Staff ID")] for r in rows} == {"FX-2"}


def test_leaver_benefit_end_is_their_last_day_not_the_year_end(client):
    """The whole point of the sheet: cover stops when they leave."""
    header, rows = _get(client, "leaver-summary")
    row = rows[0]
    assert row[header.index("Benefit End Date")] == datetime(2039, 6, 30)
    assert row[header.index("Last Day of Service")] == datetime(2039, 6, 30)


def test_leaver_summary_carries_the_final_wallet_position(client):
    header, rows = _get(client, "leaver-summary")
    row = rows[0]
    assert row[header.index("Total Allocation Amt")] == 1000.0
    assert row[header.index("Claims Payment Amt")] == 120.0
    assert row[header.index("Balance Available Allocation Amt")] == 880.0


def test_leaver_details_lists_their_claims_only(client):
    header, rows = _get(client, "leaver-details")
    refs = {r[header.index("Reference No.")] for r in rows}
    assert refs == {"FLEXICO-000004"}
    assert rows[0][header.index("Claim Category")] == "Flexible Benefits"


def test_leaver_reports_are_audited(client):
    from app.models import AuditLog
    _get(client, "leaver-summary")
    with SessionLocal() as s:
        reports = {
            (r.after or {}).get("report")
            for r in s.query(AuditLog).filter(
                AuditLog.entity_type == "insurer_report"
            ).all()
        }
    assert "leaver-summary" in reports


# ── Bundles ──────────────────────────────────────────────────────────────────

def test_bundle_listing_names_the_files_and_insurers(client):
    res = client.get(f"/api/v1/policy-years/{PY_ID}/reports/bundles")
    assert res.status_code == 200, res.text
    by_key = {b["key"]: b for b in res.json()}
    assert by_key["wallet-utilisation"]["file_count"] == 2
    assert by_key["insurer-submission"]["requires_insurer"] is True
    assert by_key["wallet-utilisation"]["requires_insurer"] is False


def test_bundle_download_zips_every_member(client):
    from zipfile import ZipFile

    res = client.get(
        f"/api/v1/policy-years/{PY_ID}/reports/bundles/wallet-utilisation"
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    names = ZipFile(BytesIO(res.content)).namelist()
    assert len(names) == 2
    assert any(n.startswith("utilisation-report-") for n in names)
    assert any(n.startswith("utilisation-summary-report-") for n in names)


def test_insurer_bundle_refuses_without_an_insurer(client):
    """An empty zip would read as "there is nothing to submit"."""
    res = client.get(
        f"/api/v1/policy-years/{PY_ID}/reports/bundles/insurer-submission"
    )
    assert res.status_code == 400


def test_insurer_bundle_refuses_an_unconfigured_insurer(client):
    res = client.get(
        f"/api/v1/policy-years/{PY_ID}/reports/bundles/insurer-submission",
        params={"insurer": "Not An Insurer"},
    )
    assert res.status_code == 404


def test_unknown_bundle_404s(client):
    res = client.get(f"/api/v1/policy-years/{PY_ID}/reports/bundles/nonsense")
    assert res.status_code == 404


def test_bundle_viewer_cannot_pull_unmasked(client):
    app.dependency_overrides[get_current_user] = lambda: _user("broker_viewer")
    try:
        res = client.get(
            f"/api/v1/policy-years/{PY_ID}/reports/bundles/wallet-utilisation",
            params={"masked": "false"},
        )
        assert res.status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = lambda: _user()
