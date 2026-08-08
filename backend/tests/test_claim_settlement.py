"""The insurer settlement leg: references, dispatch, payment, SLA, reports.

The regression that matters most here is the UTILIZATION one. `approved` used
to be terminal, so every money test compared to it directly. Now a claim moves
on to `sent_to_insurer` and `paid`, and any surviving `== approved` comparison
silently hands the member back a limit they have already spent.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_claim_settlement.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import UTC, date, datetime, timedelta  # noqa: E402
from io import BytesIO  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Claim,
    ClaimMessage,
    Client,
    Employee,
    MemberAccount,
    PolicyYear,
    User,
)
from app.models.claim import (  # noqa: E402
    CLAIM_STATUS_APPROVED,
    CLAIM_STATUS_PAID,
    CLAIM_STATUS_SENT_TO_INSURER,
    CLAIM_STATUS_SUBMITTED,
    LIVE_STATUSES,
    SETTLED_STATUSES,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.claim_settlement import (  # noqa: E402
    days_over_deadline,
    insurer_days,
    mint_reference_no,
    reference_prefix,
)
from app.services.utilization import PENDING_STATUSES  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-0000000c5000"
PY_ID = "00000000-0000-0000-0000-0000000c5001"
USER_ID = "00000000-0000-0000-0000-0000000c50ff"
EMP = "00000000-0000-0000-0000-0000000c5101"
ACC = "00000000-0000-0000-0000-0000000c5201"

NOW = datetime.now(UTC)


def _user(role: str = "broker_admin") -> CurrentUser:
    return CurrentUser(
        user_id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID, role=role,
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    from scripts.seed_demo import seed
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Settle Co", slug="settle-co",
                     broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.add(User(id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
                   email="ops@settle.co", display_name="Sam Ops",
                   role="broker_admin", status="active"))
        s.flush()
        s.add(PolicyYear(id=PY_ID, client_id=CLIENT_ID, year=2038,
                         start_date=date(2038, 1, 1), end_date=date(2038, 12, 31),
                         status=PolicyYearStatus.active))
        s.add(MemberAccount(id=ACC, client_id=CLIENT_ID, staff_id="ST-1",
                            email="mem@settle.co", status="active"))
        s.flush()
        s.add(Employee(id=EMP, client_id=CLIENT_ID, policy_year_id=PY_ID,
                       staff_id="ST-1", employee_name="Mel Member",
                       member_account_id=ACC, attribute_values={},
                       derived_attribute_values={}, matched_categories=[],
                       source="csv_import", status="active"))
        s.commit()
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _claim(session, **kw) -> Claim:
    defaults = dict(
        client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP,
        claim_kind="insured", product_code="GHS", claim_type="Hospital",
        incurred_date=date(2038, 3, 1), amount_claimed=500.0, currency="SGD",
        status=CLAIM_STATUS_APPROVED, amount_approved=500.0,
        decided_at=NOW - timedelta(days=5), origin="portal",
    )
    defaults.update(kw)
    c = Claim(**defaults)
    session.add(c)
    session.flush()
    return c


# ── The status-set invariants ────────────────────────────────────────────────

def test_settled_statuses_are_not_pending():
    """The whole point of SETTLED_STATUSES: PENDING is derived by subtraction,
    so a new settled status lands in it by default and silently un-spends the
    member's limit."""
    assert CLAIM_STATUS_SENT_TO_INSURER not in PENDING_STATUSES
    assert CLAIM_STATUS_PAID not in PENDING_STATUSES
    assert SETTLED_STATUSES.isdisjoint(PENDING_STATUSES)


def test_settled_statuses_are_live():
    """A settled claim still consumes the limit and still blocks a duplicate
    invoice."""
    assert SETTLED_STATUSES <= LIVE_STATUSES


def test_settlement_does_not_give_the_limit_back():
    """The behavioural half of the invariant above.

    Approve 500, send it, then have the insurer pay it. At no point does the
    approved total drop — before the fix, moving off `approved` reclassified
    the money as `pending`, which utilization reports beside the limit and
    never subtracts.
    """
    from app.services.utilization import _bucket_sums

    def sums_for(status: str) -> dict[str, float | int]:
        claim = Claim(
            client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP,
            claim_kind="insured", product_code="GHS", claim_type="Hospital",
            incurred_date=date(2038, 3, 1), amount_claimed=500.0,
            amount_approved=500.0, status=status,
        )
        return _bucket_sums([claim])[("GHS", None)]

    for status in (
        CLAIM_STATUS_APPROVED,
        CLAIM_STATUS_SENT_TO_INSURER,
        CLAIM_STATUS_PAID,
    ):
        row = sums_for(status)
        assert row["approved"] == 500.0, status
        assert row["pending"] == 0.0, status


# ── Reference numbers ────────────────────────────────────────────────────────

def test_reference_is_minted_once_and_never_changes(client):
    with SessionLocal() as s:
        c = _claim(s, status="draft", amount_approved=None, decided_at=None)
        first = mint_reference_no(s, c)
        s.commit()
        again = mint_reference_no(s, c)
    assert first.startswith("SETTLECO-")
    # Idempotent: a needs_info resubmission runs back through submit_claim, and
    # a reference that changed is one the member can no longer quote.
    assert again == first


def test_references_increment_per_client(client):
    with SessionLocal() as s:
        a = mint_reference_no(s, _claim(s, status="draft"))
        s.commit()
        b = mint_reference_no(s, _claim(s, status="draft"))
        s.commit()
    assert int(b.rsplit("-", 1)[1]) == int(a.rsplit("-", 1)[1]) + 1


def test_reference_prefix_falls_back_rather_than_being_blank():
    """A bare number is not recognisable as a claim reference on a phone call."""
    assert reference_prefix(None) == "CLM"
    assert reference_prefix(Client(name="!!!", slug=None)) == "CLM"


# ── Dispatch and payment ─────────────────────────────────────────────────────

def test_send_to_insurer_sets_a_deadline_even_when_none_is_given(client):
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    res = client.post(f"/api/v1/claims/{cid}/send-to-insurer", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == CLAIM_STATUS_SENT_TO_INSURER
    # An unbounded claim is one nobody chases.
    assert body["insurer_deadline_on"] is not None


def test_only_an_approved_claim_can_be_sent(client):
    with SessionLocal() as s:
        c = _claim(s, status=CLAIM_STATUS_SUBMITTED, amount_approved=None)
        s.commit()
        cid = c.id
    res = client.post(f"/api/v1/claims/{cid}/send-to-insurer", json={})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "invalid_transition"


def test_payment_records_the_insurers_own_figure(client):
    """A shortfall between approved and paid is the reason to reconcile."""
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    client.post(f"/api/v1/claims/{cid}/send-to-insurer", json={})
    res = client.post(
        f"/api/v1/claims/{cid}/payment",
        json={"paid_on": "2038-04-10", "amount": 420.0},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == CLAIM_STATUS_PAID
    assert body["payment_amount"] == 420.0
    # What we approved is NOT overwritten.
    assert body["amount_approved"] == 500.0


def test_a_zero_settlement_is_accepted(client):
    """Fully offset against an excess is a real advice; refusing it would
    strand the claim in `sent_to_insurer` forever."""
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    client.post(f"/api/v1/claims/{cid}/send-to-insurer", json={})
    res = client.post(
        f"/api/v1/claims/{cid}/payment",
        json={"paid_on": "2038-04-10", "amount": 0},
    )
    assert res.status_code == 200, res.text
    assert res.json()["payment_amount"] == 0


def test_a_zero_payment_notice_says_zero(client):
    """`or` instead of `is not None` told the member they were paid the full
    approved amount when the insurer paid nothing."""
    from app.models.claim_message import EVENT_PAID
    from app.services.claim_messages import _system_copy

    with SessionLocal() as s:
        c = _claim(s, status=CLAIM_STATUS_PAID, amount_approved=500.0,
                   payment_amount=0.0, paid_on=date(2038, 4, 10))
        s.flush()
        _subject, body = _system_copy(c, EVENT_PAID, None)
    assert "500" not in body


def test_references_are_unique_per_client(client):
    """`mint_reference_no` reads the max then writes one past it, which races.
    The unique index is what actually stops a duplicate reaching the ledger."""
    from sqlalchemy.exc import IntegrityError

    with SessionLocal() as s:
        first = _claim(s, status="draft")
        ref = mint_reference_no(s, first)
        s.commit()
        clash = _claim(s, status="draft")
        clash.reference_no = ref
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_payment_notifies_the_member_separately_from_approval(client):
    """"Approved" and "the money is in my account" are weeks apart and only the
    second ends the member's wait."""
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    client.post(f"/api/v1/claims/{cid}/send-to-insurer", json={})
    client.post(f"/api/v1/claims/{cid}/payment", json={"paid_on": "2038-04-10"})
    with SessionLocal() as s:
        events = [
            m.event for m in s.query(ClaimMessage).filter(
                ClaimMessage.claim_id == cid
            ).all()
        ]
    assert "paid" in events


def test_dispatch_posts_no_member_notice(client):
    """Forwarding to the insurer is our workflow, not a new decision."""
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    client.post(f"/api/v1/claims/{cid}/send-to-insurer", json={})
    with SessionLocal() as s:
        assert s.query(ClaimMessage).filter(
            ClaimMessage.claim_id == cid
        ).count() == 0


# ── SLA counters ─────────────────────────────────────────────────────────────

def test_insurer_days_keeps_counting_on_an_unpaid_claim():
    """Blank here would make an overdue claim look like one never sent."""
    c = Claim(
        client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP,
        claim_kind="insured", claim_type="Hospital",
        incurred_date=date(2038, 3, 1), amount_claimed=1.0,
        sent_to_insurer_at=datetime.now(UTC) - timedelta(days=9),
    )
    assert insurer_days(c) == 9


def test_the_insurer_clock_stops_when_they_decline():
    """A declined claim never gets a `paid_on`. Falling back to today made its
    overdue count climb every night forever, parking a settled claim at the top
    of the broker's overdue list."""
    from datetime import timedelta as _td

    decided = datetime.now(UTC) - _td(days=40)
    c = Claim(
        client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP,
        claim_kind="insured", claim_type="Hospital",
        incurred_date=date(2038, 3, 1), amount_claimed=1.0,
        status="rejected",
        sent_to_insurer_at=datetime.now(UTC) - _td(days=60),
        insurer_deadline_on=(datetime.now(UTC) - _td(days=30)).date(),
        decided_at=decided,
    )
    # 60 days sent → 40 days decided = 20, and it stays 20 tomorrow.
    assert insurer_days(c) == 20
    assert days_over_deadline(c) == -10


def test_days_over_deadline_is_signed():
    c = Claim(
        client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP,
        claim_kind="insured", claim_type="Hospital",
        incurred_date=date(2038, 3, 1), amount_claimed=1.0,
        insurer_deadline_on=date(2038, 5, 1), paid_on=date(2038, 4, 20),
    )
    assert days_over_deadline(c) == -11  # still in time


# ── Assessment detail ────────────────────────────────────────────────────────

def test_assessment_is_a_partial_update(client):
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    client.patch(
        f"/api/v1/claims/{cid}/assessment",
        json={"admission_date": "2038-03-01", "discharge_date": "2038-03-04"},
    )
    # A second form editing only the sector must not blank the dates.
    res = client.patch(
        f"/api/v1/claims/{cid}/assessment", json={"hospital_type": "government"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hospital_type"] == "government"
    assert body["admission_date"] == "2038-03-01"
    assert body["discharge_date"] == "2038-03-04"


def test_assessment_rejects_a_bad_sector_and_inverted_dates(client):
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    bad = client.patch(
        f"/api/v1/claims/{cid}/assessment", json={"hospital_type": "clinic"}
    )
    assert bad.status_code == 422
    inverted = client.patch(
        f"/api/v1/claims/{cid}/assessment",
        json={"admission_date": "2038-03-05", "discharge_date": "2038-03-01"},
    )
    assert inverted.status_code == 422


def test_assessment_checks_dates_against_the_stored_pair(client):
    """The endpoint is a PARTIAL update, so an inverted pair is most easily
    created one field at a time — the body alone always looks fine."""
    with SessionLocal() as s:
        c = _claim(s)
        s.commit()
        cid = c.id
    ok = client.patch(
        f"/api/v1/claims/{cid}/assessment", json={"admission_date": "2038-03-05"}
    )
    assert ok.status_code == 200, ok.text
    # Only the discharge date is in this body, and it precedes the admission
    # date already on the row.
    bad = client.patch(
        f"/api/v1/claims/{cid}/assessment", json={"discharge_date": "2038-03-01"}
    )
    assert bad.status_code == 422


# ── Reports ──────────────────────────────────────────────────────────────────

def _sheet(resp):
    assert resp.status_code == 200, resp.text
    ws = load_workbook(BytesIO(resp.content)).active
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], rows[1:]


def test_insurance_claims_report_carries_the_servicing_columns(client):
    with SessionLocal() as s:
        c = _claim(s, reference_no="SETTLECO-000900")
        s.commit()
        cid = c.id
    client.post(f"/api/v1/claims/{cid}/send-to-insurer", json={})
    client.post(f"/api/v1/claims/{cid}/payment", json={"paid_on": "2038-04-10"})
    header, rows = _sheet(
        client.get(f"/api/v1/policy-years/{PY_ID}/reports/insurance-claims")
    )
    for col in (
        "Reference No.", "Date Sent to Insurer", "Deadline Date for Insurer",
        "Payment Date", "No. of days for Tracking Insurer", "Days Over Deadline",
        "First Document Receive Date", "Verified Date",
    ):
        assert col in header
    row = next(r for r in rows if r[header.index("Reference No.")] == "SETTLECO-000900")
    assert row[header.index("Status")] == "Paid"


def test_outpatient_sheet_drops_the_inpatient_only_columns(client):
    """Three columns blank on every row read as missing data, not as N/A."""
    header, _ = _sheet(client.get(
        f"/api/v1/policy-years/{PY_ID}/reports/insurance-claims",
        params={"scope": "outpatient"},
    ))
    assert "Admission Date" not in header
    assert "Hospital Type" not in header
    assert "Referral Letter" in header


def test_inpatient_scope_filters_by_product_not_sub_type(client):
    with SessionLocal() as s:
        _claim(s, product_code="GOGP", reference_no="SETTLECO-000901")
        s.commit()
    header, rows = _sheet(client.get(
        f"/api/v1/policy-years/{PY_ID}/reports/insurance-claims",
        params={"scope": "inpatient"},
    ))
    refs = {r[header.index("Reference No.")] for r in rows}
    assert "SETTLECO-000901" not in refs


def test_employee_claims_sheet_covers_flex_and_insured(client):
    with SessionLocal() as s:
        _claim(s, claim_kind="flex", product_code=None,
               flex_category_name="Wellness", claim_type="Wellness",
               reference_no="SETTLECO-000902")
        s.commit()
    header, rows = _sheet(
        client.get(f"/api/v1/policy-years/{PY_ID}/reports/employee-claims")
    )
    categories = {r[header.index("Claim Category")] for r in rows}
    assert {"Flexible Benefits", "Insurance"} <= categories


def test_flex_claims_are_absent_from_the_insurance_sheet(client):
    """A flex claim is funded by the member's wallet — there is no insurer."""
    header, rows = _sheet(
        client.get(f"/api/v1/policy-years/{PY_ID}/reports/insurance-claims")
    )
    refs = {r[header.index("Reference No.")] for r in rows}
    assert "SETTLECO-000902" not in refs


def test_needs_info_reports_as_pending_documents(client):
    with SessionLocal() as s:
        _claim(s, status="needs_info", amount_approved=None,
               reference_no="SETTLECO-000903")
        s.commit()
    header, rows = _sheet(
        client.get(f"/api/v1/policy-years/{PY_ID}/reports/insurance-claims")
    )
    row = next(r for r in rows if r[header.index("Reference No.")] == "SETTLECO-000903")
    assert row[header.index("Status")] == "Pending Documents"


def test_tax_and_cpf_render_blank_when_unassessed(client):
    """NULL is "not assessed", which payroll acts on differently from "No"."""
    with SessionLocal() as s:
        _claim(s, reference_no="SETTLECO-000904")
        s.commit()
    header, rows = _sheet(
        client.get(f"/api/v1/policy-years/{PY_ID}/reports/insurance-claims")
    )
    row = next(r for r in rows if r[header.index("Reference No.")] == "SETTLECO-000904")
    assert row[header.index("TAX")] in (None, "")


def test_viewer_cannot_pull_unmasked_claims(client):
    app.dependency_overrides[get_current_user] = lambda: _user("broker_viewer")
    try:
        res = client.get(
            f"/api/v1/policy-years/{PY_ID}/reports/insurance-claims",
            params={"masked": "false"},
        )
        assert res.status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = lambda: _user()
