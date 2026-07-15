"""Claim lifecycle: member submission validations, member isolation, broker
decisions, and the status machine.

`build_member_statement` is monkeypatched to a canned statement — plan
hydration has its own test coverage; here we exercise the claims rules on a
known coverage shape (GHS with two benefit items + a flex wallet with one
claimable category).
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_claims_lifecycle.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.core.settings import clear_settings_cache  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Claim,
    Dependant,
    Employee,
    MemberAccount,
    PolicyYear,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    FlexBenefitCategoryLine,
    FlexCoverageLine,
    StatementEmployee,
)
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-00000000cl01"
EMP_A = "00000000-0000-0000-0000-00000000cl02"
EMP_C = "00000000-0000-0000-0000-00000000cl03"
DEP_A = "00000000-0000-0000-0000-00000000cl04"
ACC_A = "00000000-0000-0000-0000-00000000cl05"
ACC_C = "00000000-0000-0000-0000-00000000cl06"

PDF = b"%PDF-1.4 test receipt bytes"


def _statement_for(employee: Employee) -> BenefitStatementOut:
    dep = DependantSummary(id=DEP_A, name="Alice Jr", relationship="child")
    return BenefitStatementOut(
        employee=StatementEmployee(
            id=employee.id, staff_id=employee.staff_id, employee_name=employee.employee_name
        ),
        policy_year_id=employee.policy_year_id,
        is_matched=True,
        coverage=[
            CoverageLine(
                product_code="GHS",
                product_name="Group Hospital & Surgical",
                plan_code="P1",
                annual_policy_limit="S$1,000,000",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "Room & Board", "value": "S$650/day"},
                        {"number": "2", "name": "Outpatient GP", "value": "As charged"},
                    ]
                },
                covers_dependants=employee.id == EMP_A,
                covered_dependants=[dep] if employee.id == EMP_A else [],
            )
        ],
        dependants=[dep] if employee.id == EMP_A else [],
        flex=FlexCoverageLine(
            tier_name="Tier 1",
            wallet_amount=1000.0,
            currency="SGD",
            benefit_categories=[
                FlexBenefitCategoryLine(name="Dental", claimable=True, sub_limit=500.0),
                FlexBenefitCategoryLine(name="Gym", claimable=False),
            ],
        ),
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    # Route retained storage at a temp dir for the module.
    storage_dir = tmp_path_factory.mktemp("claims_storage")
    os.environ["INSPRO_STORAGE_DIR"] = str(storage_dir)
    clear_settings_cache()

    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY,
                client_id=DEMO_CLIENT_ID,
                year=2027,  # NOT 2026 — seed() one_or_none's the demo 2026 year
                start_date=date(2027, 4, 1),
                end_date=date(2028, 3, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        for emp_id, staff, name, acc in (
            (EMP_A, "CL-1", "Alice", ACC_A),
            (EMP_C, "CL-2", "Carol", ACC_C),
        ):
            session.add(
                MemberAccount(
                    id=acc, client_id=DEMO_CLIENT_ID, email=f"{name.lower()}@cl.test",
                    staff_id=staff, status="active",
                )
            )
            session.add(
                Employee(
                    id=emp_id, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                    staff_id=staff, employee_name=name, member_account_id=acc,
                    attribute_values={}, derived_attribute_values={},
                    source="csv_import", status="active",
                )
            )
        session.flush()
        session.add(
            Dependant(
                id=DEP_A, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                employee_id=EMP_A,
                attribute_values={"name": "Alice Jr", "relationship": "child"},
                link_method="staff_id", status="active",
            )
        )
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(Claim).delete()
        session.query(MemberAccount).filter(
            MemberAccount.client_id == DEMO_CLIENT_ID
        ).delete()
        py = session.get(PolicyYear, PY)
        if py is not None:
            session.delete(py)
        session.commit()
    os.environ.pop("INSPRO_STORAGE_DIR", None)
    clear_settings_cache()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _canned_statement(monkeypatch):
    from app.services import claims as claims_service

    monkeypatch.setattr(
        claims_service, "build_member_statement", lambda db, emp: _statement_for(emp)
    )
    # coverage-options imports it separately.
    from app.api.v1 import portal_claims

    monkeypatch.setattr(
        portal_claims, "build_member_statement", lambda db, emp: _statement_for(emp)
    )


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
    """This module tests the claim state machine, not the AI pipeline
    (test_claims_review_pipeline.py covers that) — submit/rerun leave the
    claim parked at ai_review_pending."""
    from app.api.v1 import claims as broker_claims
    from app.api.v1 import portal_claims

    monkeypatch.setattr(portal_claims, "run_review", lambda *a, **k: None)
    monkeypatch.setattr(broker_claims, "run_review", lambda *a, **k: None)


@pytest.fixture
def anon() -> TestClient:
    return TestClient(app)


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


def _auth(account_id: str) -> dict[str, str]:
    token, _ = issue_member_token(account_id, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def _draft(
    anon: TestClient,
    account: str = ACC_A,
    **overrides,
) -> dict:
    body = {
        "claim_kind": "insured",
        "product_code": "GHS",
        "benefit_key": "Outpatient GP",
        "claim_type": "outpatient",
        "incurred_date": "2027-06-15",
        "provider_name": "Raffles Medical",
        "amount_claimed": 85.0,
        "currency": "SGD",
    }
    body.update(overrides)
    res = anon.post("/api/v1/portal/claims", json=body, headers=_auth(account))
    assert res.status_code == 201, res.text
    return res.json()


def _upload(anon: TestClient, claim_id: str, content: bytes = PDF, account: str = ACC_A):
    return anon.post(
        f"/api/v1/portal/claims/{claim_id}/documents",
        files={"file": ("receipt.pdf", content, "application/pdf")},
        headers=_auth(account),
    )


def _submit(anon: TestClient, claim_id: str, account: str = ACC_A):
    return anon.post(
        f"/api/v1/portal/claims/{claim_id}/submit", headers=_auth(account)
    )


# ── Happy path ───────────────────────────────────────────────────────────────


def test_draft_upload_submit_flow(anon: TestClient):
    claim = _draft(anon)
    assert claim["status"] == "draft"

    res = _upload(anon, claim["id"], PDF + b" flow-1")
    assert res.status_code == 200, res.text
    assert res.json()["file_name"] == "receipt.pdf"

    res = _submit(anon, claim["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ai_review_pending"
    assert body["submitted_at"] is not None
    assert len(body["documents"]) == 1

    listing = anon.get("/api/v1/portal/claims", headers=_auth(ACC_A))
    assert listing.status_code == 200
    assert claim["id"] in {c["id"] for c in listing.json()["items"]}


def test_coverage_options(anon: TestClient):
    res = anon.get("/api/v1/portal/coverage-options", headers=_auth(ACC_A))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["insured"][0]["product_code"] == "GHS"
    assert "Outpatient GP" in body["insured"][0]["benefit_items"]
    assert body["flex"]["categories"] == [
        {"name": "Dental", "sub_limit": 500.0, "note": None}
    ]  # non-claimable Gym excluded
    assert body["dependants"][0]["id"] == DEP_A


def test_flex_claim_flow(anon: TestClient):
    claim = _draft(
        anon,
        claim_kind="flex",
        product_code=None,
        benefit_key=None,
        flex_category_name="Dental",
        claim_type="dental",
        amount_claimed=120.0,
    )
    assert _upload(anon, claim["id"], PDF + b" flex-1").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200


# ── Submission validations ───────────────────────────────────────────────────


def test_submit_without_receipt_422(anon: TestClient):
    claim = _draft(anon)
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "receipt" in res.text.lower()


def test_incurred_date_outside_policy_year_422(anon: TestClient):
    claim = _draft(anon, incurred_date="2026-01-15")  # before 2027-04-01
    assert _upload(anon, claim["id"], PDF + b" oob-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "policy year" in res.text.lower()


def test_unknown_product_422(anon: TestClient):
    claim = _draft(anon, product_code="GTL", benefit_key=None)
    assert _upload(anon, claim["id"], PDF + b" gtl-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "no GTL coverage" in res.text


def test_unknown_benefit_item_422(anon: TestClient):
    claim = _draft(anon, benefit_key="Acupuncture")
    assert _upload(anon, claim["id"], PDF + b" acu-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422


def test_non_claimable_flex_category_422(anon: TestClient):
    claim = _draft(
        anon,
        claim_kind="flex",
        product_code=None,
        benefit_key=None,
        flex_category_name="Gym",
        claim_type="wellness",
    )
    assert _upload(anon, claim["id"], PDF + b" gym-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422


def test_duplicate_receipt_409(anon: TestClient):
    receipt = PDF + b" duplicate-me"
    first = _draft(anon)
    assert _upload(anon, first["id"], receipt).status_code == 200
    assert _submit(anon, first["id"]).status_code == 200

    second = _draft(anon)
    assert _upload(anon, second["id"], receipt).status_code == 200
    res = _submit(anon, second["id"])
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "duplicate_receipt"


def test_insured_claim_requires_product_422(anon: TestClient):
    res = anon.post(
        "/api/v1/portal/claims",
        json={
            "claim_kind": "insured",
            "claim_type": "outpatient",
            "incurred_date": "2027-06-15",
            "amount_claimed": 50.0,
        },
        headers=_auth(ACC_A),
    )
    assert res.status_code == 422


def test_claim_for_someone_elses_dependant_404(anon: TestClient):
    res = anon.post(
        "/api/v1/portal/claims",
        json={
            "claim_kind": "insured",
            "product_code": "GHS",
            "claim_type": "outpatient",
            "incurred_date": "2027-06-15",
            "amount_claimed": 50.0,
            "dependant_id": DEP_A,  # Alice's dependant, Carol claiming
        },
        headers=_auth(ACC_C),
    )
    assert res.status_code == 404


def test_upload_wrong_file_type_415(anon: TestClient):
    claim = _draft(anon)
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/documents",
        files={"file": ("macro.xlsm", b"bytes", "application/vnd.ms-excel")},
        headers=_auth(ACC_A),
    )
    assert res.status_code == 415


# ── Member isolation ─────────────────────────────────────────────────────────


def test_member_cannot_see_other_members_claim(anon: TestClient):
    claim = _draft(anon, account=ACC_A)
    res = anon.get(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_C))
    assert res.status_code == 404
    res = anon.get("/api/v1/portal/claims", headers=_auth(ACC_C))
    assert claim["id"] not in {c["id"] for c in res.json()["items"]}


def test_member_cannot_submit_other_members_claim(anon: TestClient):
    claim = _draft(anon, account=ACC_A)
    assert _submit(anon, claim["id"], account=ACC_C).status_code == 404


# ── Draft management ─────────────────────────────────────────────────────────


def test_delete_draft(anon: TestClient):
    claim = _draft(anon)
    assert _upload(anon, claim["id"], PDF + b" del-1").status_code == 200
    res = anon.delete(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A))
    assert res.status_code == 204
    assert (
        anon.get(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A)).status_code
        == 404
    )


def test_delete_submitted_claim_409(anon: TestClient):
    claim = _draft(anon)
    assert _upload(anon, claim["id"], PDF + b" del-2").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200
    res = anon.delete(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A))
    assert res.status_code == 409


# ── Broker decisions ─────────────────────────────────────────────────────────


def _submitted_claim(anon: TestClient, marker: bytes) -> str:
    claim = _draft(anon)
    assert _upload(anon, claim["id"], PDF + marker).status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200
    return claim["id"]


def test_broker_list_and_approve(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" approve-1")

    listing = broker.get(f"/api/v1/claims?policy_year_id={PY}&status=ai_review_pending")
    assert listing.status_code == 200, listing.text
    row = next(c for c in listing.json()["items"] if c["id"] == claim_id)
    assert row["staff_id"] == "CL-1"

    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "note": "OK"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["amount_approved"] == 85.0  # defaults to amount_claimed

    # Terminal: cannot decide again.
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision", json={"action": "reject"}
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "invalid_transition"


def test_broker_approve_with_explicit_amount(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" approve-2")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 60.0},
    )
    assert res.json()["amount_approved"] == 60.0


def test_broker_reject(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" reject-1")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "reject", "note": "Not covered"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    assert res.json()["amount_approved"] is None


def test_needs_info_roundtrip(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" info-1")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "needs_info", "note": "Send the itemized bill"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "needs_info"

    # The member sees the note, adds a doc, and resubmits.
    detail = anon.get(f"/api/v1/portal/claims/{claim_id}", headers=_auth(ACC_A))
    assert detail.json()["decision_notes"] == "Send the itemized bill"
    assert _upload(anon, claim_id, PDF + b" info-1b").status_code == 200
    res = _submit(anon, claim_id)
    assert res.status_code == 200
    assert res.json()["status"] == "ai_review_pending"


def test_broker_document_download(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" download-1")
    detail = broker.get(f"/api/v1/claims/{claim_id}")
    doc_id = detail.json()["documents"][0]["id"]
    res = broker.get(f"/api/v1/claims/{claim_id}/documents/{doc_id}/download")
    assert res.status_code == 200
    assert res.content == PDF + b" download-1"
    assert "receipt.pdf" in res.headers["content-disposition"]


def test_upload_after_submit_409(anon: TestClient):
    claim_id = _submitted_claim(anon, b" locked-1")
    assert _upload(anon, claim_id, PDF + b" locked-1b").status_code == 409
