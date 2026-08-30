"""Utilization: bucket math, grouping, flex chain, zero baseline, the
limit-exceeded approve guard (+ acknowledge), and member isolation.

`build_member_statement` is monkeypatched to a canned statement (GHS with a
S$1,000 annual limit + a per-year Dental item + a per-day item, and a flex
wallet with price tags) so the sums are exercised on a known coverage shape.
"""

from __future__ import annotations

import os
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_utilization.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Claim, Employee, MemberAccount, PolicyYear  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    FlexBenefitCategoryLine,
    FlexCoverageLine,
    StatementEmployee,
)
from app.services import utilization as utilization_service  # noqa: E402
from app.services.utilization import (  # noqa: E402
    build_utilization,
    parse_limit_amount,
    remaining_for_claim,
)
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-00000000ut01"
EMP_A = "00000000-0000-0000-0000-00000000ut02"
EMP_B = "00000000-0000-0000-0000-00000000ut03"
ACC_A = "00000000-0000-0000-0000-00000000ut04"
ACC_B = "00000000-0000-0000-0000-00000000ut05"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY,
                client_id=DEMO_CLIENT_ID,
                year=2029,  # NOT 2026 — seed() one_or_none's the demo 2026 year
                start_date=date(2029, 4, 1),
                end_date=date(2030, 3, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        for emp_id, staff, name, acc in (
            (EMP_A, "UT-1", "Uma", ACC_A),
            (EMP_B, "UT-2", "Ben", ACC_B),
        ):
            session.add(
                MemberAccount(
                    id=acc,
                    client_id=DEMO_CLIENT_ID,
                    email=f"{name.lower()}@ut.test",
                    staff_id=staff,
                    status="active",
                )
            )
            session.flush()
            session.add(
                Employee(
                    id=emp_id,
                    client_id=DEMO_CLIENT_ID,
                    policy_year_id=PY,
                    staff_id=staff,
                    employee_name=name,
                    member_account_id=acc,
                    attribute_values={},
                    derived_attribute_values={},
                    source="csv_import",
                    status="active",
                )
            )
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(Claim).delete()
        session.query(MemberAccount).filter(MemberAccount.client_id == DEMO_CLIENT_ID).delete()
        py = session.get(PolicyYear, PY)
        if py is not None:
            session.delete(py)
        session.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


def _statement(employee) -> BenefitStatementOut:
    return BenefitStatementOut(
        employee=StatementEmployee(
            id=employee.id,
            staff_id=employee.staff_id,
            employee_name=employee.employee_name,
        ),
        policy_year_id=employee.policy_year_id,
        is_matched=True,
        coverage=[
            CoverageLine(
                product_code="GHS",
                product_name="Group Hospital & Surgical",
                plan_code="P1",
                annual_policy_limit="S$1,000",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "Dental", "value": "S$500 per year"},
                        {"number": "2", "name": "Room & Board", "value": "S$650/day"},
                        {"number": "3", "name": "Outpatient GP", "value": "As charged"},
                    ]
                },
            ),
            CoverageLine(
                product_code="GTL",
                product_name="Group Term Life",
                plan_code="A",
                annual_policy_limit=None,  # no numeric limit → no guard
            ),
            CoverageLine(
                product_code="GCGP",
                product_name="Group Clinical GP",
                plan_code="P1",
                benefit_schedule={
                    "items": [
                        {
                            "number": "1",
                            "name": "TCM & Chiropractor",
                            "value": "S$300 per policy year",
                        },
                    ]
                },
            ),
        ],
        dependants=[],
        flex=FlexCoverageLine(
            tier_name="Tier 1",
            wallet_amount=1000.0,
            currency="SGD",
            price_tags_total=200.0,
            flex_balance=800.0,
            benefit_categories=[
                FlexBenefitCategoryLine(name="Dental", claimable=True, sub_limit=300.0),
                FlexBenefitCategoryLine(name="Optical", claimable=True),
                FlexBenefitCategoryLine(name="Gym", claimable=False),
            ],
        ),
    )


@pytest.fixture(autouse=True)
def _canned_statement(monkeypatch):
    monkeypatch.setattr(
        utilization_service, "build_member_statement", lambda db, emp: _statement(emp)
    )


@pytest.fixture(autouse=True)
def _clean_claims():
    yield
    with SessionLocal() as session:
        session.query(Claim).delete()
        session.commit()


def _mk_claim(
    *,
    employee_id: str = EMP_A,
    kind: str = "insured",
    product: str | None = "GHS",
    benefit_key: str | None = None,
    flex_category: str | None = None,
    amount: float = 100.0,
    approved: float | None = None,
    status: str = "submitted",
) -> str:
    with SessionLocal() as s:
        claim = Claim(
            client_id=DEMO_CLIENT_ID,
            policy_year_id=PY,
            employee_id=employee_id,
            claim_kind=kind,
            product_code=product if kind == "insured" else None,
            benefit_key=benefit_key,
            flex_category_name=flex_category,
            claim_type="outpatient",
            incurred_date=date(2029, 6, 1),
            amount_claimed=amount,
            amount_approved=approved,
            currency="SGD",
            status=status,
        )
        s.add(claim)
        s.commit()
        return claim.id


def _util(employee_id: str = EMP_A):
    with SessionLocal() as s:
        return build_utilization(s, s.get(Employee, employee_id))


def _bucket(util, product: str, benefit_key: str | None = None):
    return next(
        b for b in util.insured if b.product_code == product and b.benefit_key == benefit_key
    )


# ── Parsing ───────────────────────────────────────────────────────────────────


def test_parse_limit_amount():
    assert parse_limit_amount("S$1,000,000") == 1_000_000.0
    assert parse_limit_amount("As charged") is None
    assert parse_limit_amount(None) is None


# ── Bucket math ───────────────────────────────────────────────────────────────


def test_zero_baseline():
    util = _util()
    ghs = _bucket(util, "GHS")
    assert (ghs.approved, ghs.pending, ghs.claim_count) == (0.0, 0.0, 0)
    assert ghs.limit == 1000.0 and ghs.remaining == 1000.0
    gtl = _bucket(util, "GTL")
    assert gtl.limit is None and gtl.remaining is None
    assert util.flex.available == 800.0  # flex_balance untouched
    assert util.flex.approved == 0.0
    dental = next(c for c in util.flex.categories if c.name == "Dental")
    assert dental.remaining == 300.0
    assert all(c.name != "Gym" for c in util.flex.categories)  # non-claimable hidden

    # Plan-aware claim choices need a bucket BEFORE the first claim exists; the
    # submission form cannot show a TCM member what is left if utilization only
    # creates the row after they have already filed one.
    tcm = _bucket(util, "GCGP", "TCM & Chiropractor")
    assert (
        sum(
            b.product_code == "GCGP" and b.benefit_key == "TCM & Chiropractor" for b in util.insured
        )
        == 1
    )
    assert (tcm.limit, tcm.approved, tcm.pending, tcm.remaining) == (
        300.0,
        0.0,
        0.0,
        300.0,
    )


def test_structured_policy_year_setting_overrides_free_text(monkeypatch):
    def statement(_db, employee):
        value = deepcopy(_statement(employee))
        line = value.coverage[0]
        line.annual_policy_limit = "S$9,999"
        line.benefit_schedule = {
            "claim_limit": {
                "basis": "policy_year",
                "amount": 1200,
                "currency": "SGD",
                "display": "S$1,200 per policy year",
                "claim_scope_codes": [],
                "status": "verified",
                "source": "manual",
            },
            "items": [
                {
                    "number": "1",
                    "name": "Hospital cash",
                    "value": "S$80/day",
                    "claim_limit": {
                        "basis": "policy_year",
                        "amount": 900,
                        "currency": "SGD",
                        "display": "S$900 per policy year",
                        "claim_scope_codes": ["ghs_hospitalisation"],
                        "status": "verified",
                        "source": "manual",
                    },
                }
            ],
        }
        return value

    monkeypatch.setattr(utilization_service, "build_member_statement", statement)
    util = _util()
    product = _bucket(util, "GHS")
    benefit = _bucket(util, "GHS", "Hospital cash")
    assert (product.limit, product.remaining) == (1200.0, 1200.0)
    assert (benefit.limit, benefit.remaining) == (900.0, 900.0)
    assert benefit.claim_scope_codes == ["ghs_hospitalisation"]
    assert benefit.limit_is_enforceable is True


def test_nonannual_and_not_limit_settings_are_informative_only(monkeypatch):
    def statement(_db, employee):
        value = deepcopy(_statement(employee))
        line = value.coverage[0]
        line.benefit_schedule = {
            "claim_limit": {
                "basis": "policy_year",
                "amount": 9999,
                "currency": "SGD",
                "display": "S$9,999",
                "claim_scope_codes": [],
                "status": "not_limit",
                "source": "manual",
            },
            "items": [
                {
                    "number": "1",
                    "name": "Specialist visit",
                    "value": "S$80/visit",
                    "claim_limit": {
                        "basis": "per_visit",
                        "amount": 5000,
                        "currency": "SGD",
                        "display": "S$80 per visit",
                        "claim_scope_codes": ["standard"],
                        "status": "verified",
                        "source": "manual",
                    },
                }
            ],
        }
        return value

    monkeypatch.setattr(utilization_service, "build_member_statement", statement)
    util = _util()
    product = _bucket(util, "GHS")
    benefit = _bucket(util, "GHS", "Specialist visit")
    assert product.limit is None and product.remaining is None
    assert benefit.limit is None and benefit.remaining is None
    assert benefit.limit_basis == "per_visit"
    assert benefit.limit_display == "S$80 per visit"
    assert benefit.limit_is_enforceable is False


def test_bucket_math_and_grouping():
    _mk_claim(benefit_key="Dental", amount=100.0, approved=100.0, status="approved")
    _mk_claim(benefit_key="Dental", amount=50.0, status="ai_flagged")  # pending
    _mk_claim(benefit_key="Room & Board", amount=75.0, status="submitted")
    _mk_claim(amount=30.0, status="rejected")  # excluded
    _mk_claim(amount=20.0, status="draft")  # excluded

    util = _util()
    ghs = _bucket(util, "GHS")
    assert ghs.approved == 100.0
    assert ghs.pending == 125.0  # 50 + 75; never subtracted
    assert ghs.remaining == 900.0  # 1000 - approved only
    assert ghs.claim_count == 3

    dental = _bucket(util, "GHS", "Dental")
    assert sum(b.product_code == "GHS" and b.benefit_key == "Dental" for b in util.insured) == 1
    assert dental.approved == 100.0 and dental.pending == 50.0
    assert dental.limit == 500.0 and dental.remaining == 400.0

    rnb = _bucket(util, "GHS", "Room & Board")
    assert rnb.limit is None  # "S$650/day" is per-unit, not an annual limit
    assert rnb.pending == 75.0 and rnb.remaining is None


def test_orphaned_product_bucket():
    _mk_claim(product="GPA", amount=40.0, status="submitted")  # not on statement
    util = _util()
    orphan = _bucket(util, "GPA")
    assert orphan.orphaned is True
    assert orphan.pending == 40.0 and orphan.limit is None


def test_flex_chain():
    _mk_claim(kind="flex", flex_category="Dental", amount=100.0, approved=100.0, status="approved")
    _mk_claim(kind="flex", flex_category="Optical", amount=60.0, status="submitted")

    flex = _util().flex
    assert flex.wallet_amount == 1000.0
    assert flex.flex_balance == 800.0  # wallet - price tags
    assert flex.approved == 100.0
    assert flex.pending == 60.0
    assert flex.available == 700.0  # flex_balance - approved claims only

    dental = next(c for c in flex.categories if c.name == "Dental")
    assert dental.approved == 100.0 and dental.remaining == 200.0
    optical = next(c for c in flex.categories if c.name == "Optical")
    assert optical.pending == 60.0 and optical.remaining is None  # no sub-limit


# ── remaining_for_claim (the guard input) ─────────────────────────────────────


def test_remaining_for_claim_uses_tightest_bucket():
    _mk_claim(benefit_key="Dental", amount=450.0, approved=450.0, status="approved")
    claim_id = _mk_claim(benefit_key="Dental", amount=100.0, status="submitted")
    with SessionLocal() as s:
        remaining = remaining_for_claim(s, s.get(Claim, claim_id), s.get(Employee, EMP_A))
    # Product remaining 550, Dental item remaining 50 → tightest wins.
    assert remaining == 50.0


def test_remaining_none_when_no_limit():
    claim_id = _mk_claim(product="GTL", amount=100.0, status="submitted")
    with SessionLocal() as s:
        assert remaining_for_claim(s, s.get(Claim, claim_id), s.get(Employee, EMP_A)) is None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@pytest.fixture
def broker() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _member_headers(account_id: str) -> dict[str, str]:
    token, _ = issue_member_token(account_id, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def test_broker_utilization_endpoint(broker: TestClient):
    _mk_claim(amount=100.0, approved=100.0, status="approved")
    res = broker.get(f"/api/v1/employees/{EMP_A}/utilization")
    assert res.status_code == 200, res.text
    body = res.json()
    ghs = next(
        b for b in body["insured"] if b["product_code"] == "GHS" and b["benefit_key"] is None
    )
    assert ghs["approved"] == 100.0 and ghs["remaining"] == 900.0


def test_portal_utilization_is_member_scoped():
    _mk_claim(employee_id=EMP_A, amount=100.0, approved=100.0, status="approved")
    anon = TestClient(app)

    res_a = anon.get("/api/v1/portal/utilization", headers=_member_headers(ACC_A))
    assert res_a.status_code == 200, res_a.text
    ghs_a = next(
        b
        for b in res_a.json()["insured"]
        if b["product_code"] == "GHS" and b["benefit_key"] is None
    )
    assert ghs_a["approved"] == 100.0

    # Ben sees his own (empty) usage — Uma's claims never leak.
    res_b = anon.get("/api/v1/portal/utilization", headers=_member_headers(ACC_B))
    assert res_b.status_code == 200
    ghs_b = next(
        b
        for b in res_b.json()["insured"]
        if b["product_code"] == "GHS" and b["benefit_key"] is None
    )
    assert ghs_b["approved"] == 0.0 and ghs_b["claim_count"] == 0


# ── Approve guard ─────────────────────────────────────────────────────────────


def test_approve_beyond_limit_409_then_acknowledge(broker: TestClient):
    _mk_claim(amount=950.0, approved=950.0, status="approved")
    claim_id = _mk_claim(amount=200.0, status="submitted")  # remaining is 50

    res = broker.post(f"/api/v1/claims/{claim_id}/decision", json={"action": "approve"})
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "limit_exceeded"
    assert detail["remaining"] == 50.0 and detail["approving"] == 200.0
    with SessionLocal() as s:
        assert s.get(Claim, claim_id).status == "submitted"  # unchanged

    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "acknowledge": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    assert res.json()["amount_approved"] == 200.0


def test_approve_within_limit_no_guard(broker: TestClient):
    claim_id = _mk_claim(amount=200.0, status="submitted")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 150.0},
    )
    assert res.status_code == 200, res.text
    assert res.json()["amount_approved"] == 150.0


def test_approve_partial_amount_bypasses_guard(broker: TestClient):
    _mk_claim(amount=950.0, approved=950.0, status="approved")
    claim_id = _mk_claim(amount=200.0, status="submitted")
    # Approving only the remaining 50 needs no acknowledgement.
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 50.0},
    )
    assert res.status_code == 200, res.text


def test_flex_approve_guard_uses_category_sub_limit(broker: TestClient):
    claim_id = _mk_claim(kind="flex", flex_category="Dental", amount=350.0, status="submitted")
    # Wallet available is 800 but the Dental sub-limit remaining is 300.
    res = broker.post(f"/api/v1/claims/{claim_id}/decision", json={"action": "approve"})
    assert res.status_code == 409
    assert res.json()["detail"]["remaining"] == 300.0

    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "acknowledge": True},
    )
    assert res.status_code == 200


def test_reject_never_guarded(broker: TestClient):
    _mk_claim(amount=950.0, approved=950.0, status="approved")
    claim_id = _mk_claim(amount=5000.0, status="submitted")
    res = broker.post(f"/api/v1/claims/{claim_id}/decision", json={"action": "reject"})
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"


def test_claims_can_never_take_the_wallet_below_zero():
    """A flex wallet pays UP TO the limit — a member with S$500 left who presents
    a S$700 bill utilises S$500 and pays the rest themselves. "Overspent by
    S$200" is not a state the product can be in, so `available` floors at 0 and
    `flex_ledger.MemberFlex.balance` splits identically: one member, one answer
    to "what have I got left". Reachable on paper only because pro-ration binds
    forward — it can shrink an allowance below what was already reimbursed."""
    _mk_claim(kind="flex", flex_category="Dental", amount=900.0, approved=900.0, status="approved")
    flex = _util().flex
    assert flex.flex_balance == 800.0
    assert flex.approved == 900.0
    assert flex.available == 0.0


def test_cover_costing_more_than_the_wallet_stays_signed(monkeypatch):
    """A DIFFERENT state, and the one the enrolment guard and the bulk
    `flex_overdraft` warning exist for: the member holds elected cover priced
    above their allowance. Flooring that too would hide it behind a wallet that
    merely looks empty."""

    def _overdrawn(db, emp):
        st = _statement(emp)
        st.flex.price_tags_total = 1100.0
        st.flex.flex_balance = -100.0
        return st

    monkeypatch.setattr(utilization_service, "build_member_statement", _overdrawn)
    flex = _util().flex
    assert flex.flex_balance == -100.0
    assert flex.available == -100.0


def test_a_category_sub_limit_never_reports_a_negative_remaining():
    """Same rule as the wallet: a sub-limit pays UP TO its cap, so "SGD -200
    left" is not a quantity anyone has. Reachable when a claim is approved past
    the cap (the guard allows an acknowledged override) or when pro-ration
    shrinks an allowance below what was already reimbursed."""
    _mk_claim(kind="flex", flex_category="Dental", amount=700.0, approved=700.0, status="approved")
    dental = next(c for c in _util().flex.categories if c.name == "Dental")
    assert dental.sub_limit == 300.0
    assert dental.approved == 700.0
    assert dental.remaining == 0.0


def test_an_insured_limit_never_reports_a_negative_remaining():
    """The same rule as the flex wallet, applied product-wide so no two surfaces
    can disagree. An approval past the limit is a documented broker override
    (`acknowledge=true`); the member is not "$X over limit" — the policy paid its
    cap. What was actually approved stays on the claim record and the reports."""
    _mk_claim(benefit_key="Dental", amount=900.0, approved=900.0, status="approved")
    dental = _bucket(_util(), "GHS", "Dental")
    assert dental.limit == 500.0
    assert dental.approved == 900.0
    assert dental.remaining == 0.0
    # The product ROLL-UP has its own, larger limit (S$1,000), so 900 approved
    # genuinely leaves 100 there — the floor bites per bucket, not globally.
    assert _bucket(_util(), "GHS").remaining == 100.0


# ── Pending claim ids ─────────────────────────────────────────────────────────


def test_pending_claim_ids_are_served_with_the_figure():
    """The claims `pending` was summed from, on every bucket that counted them.

    Served rather than re-filtered by the client: membership is `status not in
    SETTLED_STATUSES`, a set defined BY SUBTRACTION, so a status list mirrored
    into TypeScript starts offering a different set from the number it sits
    under. The roll-up AND the per-benefit bucket both carry the claim, because
    its amount is counted into both.
    """
    flagged = _mk_claim(benefit_key="Dental", amount=50.0, status="ai_flagged")
    needs = _mk_claim(benefit_key="Dental", amount=25.0, status="needs_info")
    other = _mk_claim(benefit_key="Room & Board", amount=75.0, status="submitted")
    _mk_claim(benefit_key="Dental", amount=100.0, approved=100.0, status="approved")
    _mk_claim(benefit_key="Dental", amount=30.0, status="rejected")
    _mk_claim(benefit_key="Dental", amount=20.0, status="draft")

    util = _util()
    ghs = _bucket(util, "GHS")
    assert set(ghs.pending_claim_ids) == {flagged, needs, other}
    assert ghs.pending == 150.0

    dental = _bucket(util, "GHS", "Dental")
    assert set(dental.pending_claim_ids) == {flagged, needs}
    # Settled, rejected and draft claims are not "pending" and must not appear.
    assert dental.pending == 75.0


def test_a_settled_claim_leaves_the_pending_ids():
    """`approved` is no longer terminal (`sent_to_insurer`/`paid` follow it), and
    every settled status must drop out of the list as well as out of the figure —
    which is exactly what a mirrored, hand-written status set gets wrong."""
    paid = _mk_claim(amount=40.0, approved=40.0, status="paid")
    sent = _mk_claim(amount=50.0, approved=50.0, status="sent_to_insurer")
    live = _mk_claim(amount=60.0, status="submitted")
    ghs = _bucket(_util(), "GHS")
    assert ghs.pending_claim_ids == [live]
    assert paid not in ghs.pending_claim_ids
    assert sent not in ghs.pending_claim_ids
    assert (ghs.approved, ghs.pending) == (90.0, 60.0)


def test_nothing_in_flight_serves_an_empty_list():
    assert _bucket(_util(), "GHS").pending_claim_ids == []


def test_an_orphaned_bucket_carries_its_pending_ids():
    """An orphaned PRODUCT bucket has `benefit_key=None`, so the member's usage
    tab renders it among the product rows and itemises its pending figure like
    any other — without the ids that breakdown would silently disappear."""
    claim = _mk_claim(product="GPA", amount=40.0, status="submitted")
    orphan = _bucket(_util(), "GPA")
    assert orphan.orphaned is True
    assert orphan.pending_claim_ids == [claim]
