"""LOG cases — a broker-entered claim category.

Covers the three things that make this feature more than a boolean:

1. **The relaxed intake.** A LOG case saves with no documents, no diagnosis and
   no sub-type, and against a product members may not self-file.
2. **The visibility rule.** The portal filters on ORIGIN, not on case type — so
   a broker-created case is invisible to the member while a claim they
   submitted themselves stays visible even after it is reclassified. That
   asymmetry is the whole reason the two columns exist and is pinned here.
3. **Reclassification.** In place, reversible, idempotent, and refused once the
   claim's outcome is recorded.

`build_member_statement` is monkeypatched to a canned statement, like
`test_claims_lifecycle.py` — plan hydration has its own coverage.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_log_cases.db"
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
from app.models.claim import (  # noqa: E402
    CASE_TYPE_CLAIM,
    CASE_TYPE_LOG,
    ORIGIN_BROKER,
    ORIGIN_PORTAL,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    StatementEmployee,
)
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-0000000010g1"
EMP_A = "00000000-0000-0000-0000-0000000010g2"
ACC_A = "00000000-0000-0000-0000-0000000010g3"
DEP_COVERED = "00000000-0000-0000-0000-0000000010g4"
DEP_UNCOVERED = "00000000-0000-0000-0000-0000000010g5"
DEP_PENDING = "00000000-0000-0000-0000-0000000010g6"

PDF = b"%PDF-1.4 log case evidence"
IN_PERIOD = "2027-06-15"


def _statement_for(employee: Employee) -> BenefitStatementOut:
    covered = DependantSummary(id=DEP_COVERED, name="Ana Jr", relationship="child")
    uncovered = DependantSummary(id=DEP_UNCOVERED, name="Ben", relationship="child")
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
                annual_policy_limit="S$100,000",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "Room & Board", "value": "S$650/day"}
                    ]
                },
                covers_dependants=True,
                # Elected subset: the covered dependant only.
                covered_dependants=[covered],
            ),
            CoverageLine(
                product_code="GCGP",
                product_name="Group Clinical GP",
                plan_code="P1",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "GP Consultation", "value": "As charged"}
                    ]
                },
                covers_dependants=False,
                covered_dependants=[],
            ),
            # Held by the member but NOT member-filed (`member_claimable=False`).
            # An assessor must still be able to record a LOG case against it.
            CoverageLine(
                product_code="GMM",
                product_name="Group Major Medical",
                plan_code="P1",
                covers_dependants=False,
                covered_dependants=[],
            ),
        ],
        dependants=[covered, uncovered],
        flex=None,
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    storage_dir = tmp_path_factory.mktemp("log_cases_storage")
    os.environ["INSPRO_STORAGE_DIR"] = str(storage_dir)
    clear_settings_cache()

    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY,
                client_id=DEMO_CLIENT_ID,
                year=2027,
                start_date=date(2027, 4, 1),
                end_date=date(2028, 3, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        session.add(
            MemberAccount(
                id=ACC_A, client_id=DEMO_CLIENT_ID, email="ana@log.test",
                staff_id="LOG-1", status="active",
            )
        )
        session.add(
            Employee(
                id=EMP_A, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="LOG-1", employee_name="Ana", member_account_id=ACC_A,
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status="active",
            )
        )
        session.flush()
        for dep_id, name, dep_status in (
            (DEP_COVERED, "Ana Jr", "active"),
            (DEP_UNCOVERED, "Ben", "active"),
            (DEP_PENDING, "Pending Kid", "pending_approval"),
        ):
            session.add(
                Dependant(
                    id=dep_id, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                    employee_id=EMP_A,
                    attribute_values={"name": name, "relationship": "child"},
                    link_method="staff_id", status=dep_status,
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
    from app.api.v1 import portal_claims
    from app.services import log_cases, utilization

    # Each of these imports the builder by name, so each needs its own patch —
    # utilization included, or the limit maths runs against a real (unmatched)
    # statement and every bucket reports `remaining: null`.
    monkeypatch.setattr(
        portal_claims, "build_member_statement", lambda db, emp: _statement_for(emp)
    )
    monkeypatch.setattr(
        log_cases, "build_member_statement", lambda db, emp: _statement_for(emp)
    )
    monkeypatch.setattr(
        utilization, "build_member_statement", lambda db, emp: _statement_for(emp)
    )


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
    from app.api.v1 import claims as broker_claims
    from app.api.v1 import portal_claims

    monkeypatch.setattr(portal_claims, "run_review", lambda *a, **k: None)
    monkeypatch.setattr(broker_claims, "run_review", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _clean_claims():
    yield
    with SessionLocal() as session:
        session.query(Claim).delete()
        session.commit()


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


def _auth(account_id: str = ACC_A) -> dict[str, str]:
    token, _ = issue_member_token(account_id, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def _log_body(**overrides) -> dict:
    body = {
        "claim_kind": "insured",
        "product_code": "GHS",
        "incurred_date": IN_PERIOD,
        "amount_claimed": 8400.0,
        "currency": "SGD",
        "received_via": "email",
        "received_on": "2027-06-10",
        "requested_by": "HR — Serene Lim",
    }
    body.update(overrides)
    return body


def _create_log(broker: TestClient, **overrides):
    return broker.post(f"/api/v1/employees/{EMP_A}/log-cases", json=_log_body(**overrides))


def _member_claim(anon: TestClient) -> str:
    """A real member-submitted claim, through the portal endpoints."""
    created = anon.post(
        "/api/v1/portal/claims",
        headers=_auth(),
        json={
            "claim_kind": "insured",
            "product_code": "GCGP",
            "claim_type": "GP (General Practitioner)",
            "incurred_date": IN_PERIOD,
            "provider_name": "Raffles Medical",
            "invoice_number": "INV-991",
            "diagnosis": "Acute upper respiratory infection",
            "amount_claimed": 68.0,
            "currency": "SGD",
        },
    )
    assert created.status_code == 201, created.text
    claim_id = created.json()["id"]
    up = anon.post(
        f"/api/v1/portal/claims/{claim_id}/documents",
        headers=_auth(),
        files={"file": ("receipt.pdf", PDF, "application/pdf")},
    )
    assert up.status_code == 200, up.text
    sub = anon.post(f"/api/v1/portal/claims/{claim_id}/submit", headers=_auth())
    assert sub.status_code == 200, sub.text
    return claim_id


# ── Creation ─────────────────────────────────────────────────────────────────


def test_log_case_saves_with_no_documents(broker):
    """The point of the feature: an emailed request is recorded as it arrives,
    with no receipt, no diagnosis and no sub-type."""
    res = _create_log(broker)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["case_type"] == CASE_TYPE_LOG
    assert body["origin"] == ORIGIN_BROKER
    assert body["claim_type"] == "LOG"
    # Straight into the queue — no draft state.
    assert body["status"] == "submitted"
    assert body["submitted_at"] is not None
    assert body["documents"] == []
    assert body["received_via"] == "email"
    assert body["requested_by"] == "HR — Serene Lim"
    assert body["received_on"] == "2027-06-10"
    # A LOG case never runs through `submit_claim`, so it has to mint its own
    # reference. Without one it appears in the claims register and on the
    # insurer submission with a blank Reference No. — the column both sides
    # reconcile on.
    assert body["reference_no"], "a LOG case needs a quotable reference too"


def test_log_case_allowed_against_a_product_members_cannot_self_file(broker, anon):
    """`member_claimable` hides insurer-settled products from the member form.
    An assessor recording a case on their behalf is exactly what that gate was
    written to exclude, so it must not apply to a LOG case."""
    member = anon.post(
        "/api/v1/portal/claims",
        headers=_auth(),
        json={
            "claim_kind": "insured",
            "product_code": "GMM",
            "claim_type": "Group Major Medical",
            "incurred_date": IN_PERIOD,
            "provider_name": "Mount Elizabeth",
            "invoice_number": "INV-1",
            "diagnosis": "Appendicitis",
            "sub_type": "Hospitalisation/Day Surgery/Other Inpatient Treatment",
            "amount_claimed": 100.0,
        },
    )
    # The member's own path still refuses it (at submit, via the coverage check).
    claim_id = member.json().get("id") if member.status_code == 201 else None
    if claim_id:
        anon.post(
            f"/api/v1/portal/claims/{claim_id}/documents",
            headers=_auth(),
            files={"file": ("r.pdf", PDF, "application/pdf")},
        )
        blocked = anon.post(
            f"/api/v1/portal/claims/{claim_id}/submit", headers=_auth()
        )
        assert blocked.status_code == 422
        assert "aren't submitted through the portal" in blocked.text

    assert _create_log(broker, product_code="GMM").status_code == 201


def test_log_case_against_uncovered_product_422(broker):
    res = _create_log(broker, product_code="GTL")
    assert res.status_code == 422
    assert "no GTL coverage" in res.text


def test_log_case_without_coverage_named_422(broker):
    res = _create_log(broker, product_code=None)
    assert res.status_code == 422
    assert "must name the coverage" in res.text


def test_log_case_for_dependant_outside_the_covered_subset_422(broker):
    """An enrollment-elected subset binds here exactly as it does on the member
    surface — the statement's covered list is authoritative."""
    res = _create_log(broker, dependant_id=DEP_UNCOVERED)
    assert res.status_code == 422
    assert "not covered" in res.text

    ok = _create_log(broker, dependant_id=DEP_COVERED)
    assert ok.status_code == 201
    assert ok.json()["dependant_name"] == "Ana Jr"


def test_log_case_for_pending_dependant_422(broker):
    res = _create_log(broker, dependant_id=DEP_PENDING)
    assert res.status_code == 422
    assert "pending approval" in res.text


def test_log_case_outside_the_benefit_year_422(broker):
    res = _create_log(broker, incurred_date="2026-01-05")
    assert res.status_code == 422
    assert "policy year" in res.text


def test_log_case_rejects_an_unsupported_currency(broker):
    res = _create_log(broker, currency="XXX")
    assert res.status_code == 422
    assert "not a supported claim currency" in res.text


def test_log_case_is_not_bound_by_the_member_submission_deadline(broker):
    """The grace period governs MEMBERS submitting, not brokers recording. A
    request that arrived by email after the window closed still has to be
    logged — the deadline is a rule about the portal, and an assessor entering
    a late case knows it is late. Deliberate; pinned so it can't be "fixed"
    into a block by someone reading `submit_claim`."""
    with SessionLocal() as session:
        year = session.get(PolicyYear, PY)
        year.claim_grace_period_days = 0
        session.commit()
    try:
        assert _create_log(broker).status_code == 201
    finally:
        with SessionLocal() as session:
            year = session.get(PolicyYear, PY)
            year.claim_grace_period_days = None
            session.commit()


def test_broker_can_attach_a_document_to_a_case_in_review(broker):
    """Unlike the member's upload, the broker's is not gated on the claim being
    member-editable: correspondence arrives after submission."""
    claim_id = _create_log(broker).json()["id"]
    res = broker.post(
        f"/api/v1/claims/{claim_id}/documents",
        files={"file": ("request-email.pdf", PDF, "application/pdf")},
    )
    assert res.status_code == 201, res.text
    detail = broker.get(f"/api/v1/claims/{claim_id}").json()
    assert [d["file_name"] for d in detail["documents"]] == ["request-email.pdf"]


def test_broker_document_upload_rejects_an_unknown_slot_tag(broker):
    claim_id = _create_log(broker).json()["id"]
    res = broker.post(
        f"/api/v1/claims/{claim_id}/documents",
        files={"file": ("x.pdf", PDF, "application/pdf")},
        data={"doc_type": "not_a_slot"},
    )
    assert res.status_code == 422


# ── Visibility: origin, not case type ────────────────────────────────────────


def test_broker_created_case_is_invisible_to_the_member(broker, anon):
    claim_id = _create_log(broker).json()["id"]

    listed = anon.get("/api/v1/portal/claims", headers=_auth())
    assert listed.status_code == 200
    assert claim_id not in {c["id"] for c in listed.json()["items"]}
    assert listed.json()["total"] == 0

    # Not readable, and not writable, through any member endpoint.
    assert anon.get(f"/api/v1/portal/claims/{claim_id}", headers=_auth()).status_code == 404
    assert anon.post(
        f"/api/v1/portal/claims/{claim_id}/documents",
        headers=_auth(),
        files={"file": ("r.pdf", PDF, "application/pdf")},
    ).status_code == 404
    assert anon.post(
        f"/api/v1/portal/claims/{claim_id}/submit", headers=_auth()
    ).status_code == 404
    assert anon.delete(
        f"/api/v1/portal/claims/{claim_id}", headers=_auth()
    ).status_code == 404


def test_broker_created_case_is_invisible_on_the_MESSAGE_surfaces_too(broker, anon):
    """A thread hangs off a claim, so it inherits that claim's visibility.

    The message endpoints had their own copy of the member claim loader and did
    not learn about broker-created cases: the member could read (and post to)
    the thread of a case that 404s everywhere else, and a decision notice
    badged their inbox for a claim they could not open.
    """
    claim_id = _create_log(broker).json()["id"]
    broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "acknowledge": True, "note": "Guaranteed"},
    )

    assert anon.get(
        f"/api/v1/portal/claims/{claim_id}/messages", headers=_auth()
    ).status_code == 404
    assert anon.post(
        f"/api/v1/portal/claims/{claim_id}/messages",
        headers=_auth(),
        json={"body": "how is this going?"},
    ).status_code == 404

    inbox = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    assert inbox["total"] == 0
    assert inbox["unread_total"] == 0

    # And the notice was never written in the first place.
    thread = broker.get(f"/api/v1/claims/{claim_id}/messages").json()
    assert thread == []


def test_a_members_own_claim_keeps_its_thread_after_reclassification(broker, anon):
    """The mirror of the test above: reclassifying must not silence a claim the
    member filed — they are still owed the decision notice."""
    claim_id = _member_claim(anon)
    broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "log", "reason": "Recorded as an LOG case"},
    )
    broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "acknowledge": True},
    )
    assert anon.get(
        f"/api/v1/portal/claims/{claim_id}/messages", headers=_auth()
    ).status_code == 200
    inbox = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    assert inbox["total"] >= 1
    assert any(c["subject"]["id"] == claim_id for c in inbox["items"])


def test_preview_messages_mirror_the_member_exactly(broker, anon):
    """Parity is asserted for the claims preview; the message preview reached
    claims through a different path and diverged."""
    log_id = _create_log(broker).json()["id"]
    own_id = _member_claim(anon)
    broker.post(
        f"/api/v1/claims/{log_id}/decision",
        json={"action": "approve", "acknowledge": True},
    )

    assert broker.get(
        f"/api/v1/employees/{EMP_A}/portal-preview/claims/{log_id}/messages"
    ).status_code == 404
    assert broker.get(
        f"/api/v1/employees/{EMP_A}/portal-preview/claims/{own_id}/messages"
    ).status_code == 200

    preview = broker.get(
        f"/api/v1/employees/{EMP_A}/portal-preview/conversations"
    ).json()
    member = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    assert preview == member


def test_broker_created_case_is_absent_from_the_employee_view_preview(broker):
    """The preview is asserted to return exactly what the member sees, so the
    filter has to be the same one."""
    claim_id = _create_log(broker).json()["id"]
    preview = broker.get(f"/api/v1/employees/{EMP_A}/portal-preview/claims")
    assert preview.status_code == 200
    assert claim_id not in {c["id"] for c in preview.json()["items"]}


def test_relabelling_a_member_claim_never_hides_it_from_them(broker, anon):
    """The defect this design exists to prevent: filtering the portal on
    `case_type` would make a member's own submission vanish mid-review."""
    claim_id = _member_claim(anon)

    relabel = broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "log", "reason": "Hospital sent this as an LOG request"},
    )
    assert relabel.status_code == 200, relabel.text
    assert relabel.json()["case_type"] == CASE_TYPE_LOG

    still_there = anon.get("/api/v1/portal/claims", headers=_auth())
    assert claim_id in {c["id"] for c in still_there.json()["items"]}
    assert anon.get(
        f"/api/v1/portal/claims/{claim_id}", headers=_auth()
    ).status_code == 200

    preview = broker.get(f"/api/v1/employees/{EMP_A}/portal-preview/claims")
    assert claim_id in {c["id"] for c in preview.json()["items"]}


# ── Reclassification ─────────────────────────────────────────────────────────


def test_relabel_preserves_status_documents_and_amounts(broker, anon):
    claim_id = _member_claim(anon)
    before = broker.get(f"/api/v1/claims/{claim_id}").json()

    broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "log", "reason": "Recorded as an LOG case"},
    )
    after = broker.get(f"/api/v1/claims/{claim_id}").json()

    assert after["status"] == before["status"]
    assert after["amount_claimed"] == before["amount_claimed"]
    assert [d["id"] for d in after["documents"]] == [
        d["id"] for d in before["documents"]
    ]
    assert after["origin"] == ORIGIN_PORTAL  # who filed it never changes


def test_reclassifying_never_rewrites_the_members_own_claim_label(broker, anon):
    """`claim_type` is the DESCRIPTIVE label and it is what the member sees as
    the title of their claim in the portal. Stamping it "LOG" renamed their
    submission to an acronym they have never been given — the category belongs
    on `case_type`, which only the broker surface renders."""
    claim_id = _member_claim(anon)
    label = "GP (General Practitioner)"

    broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "log", "reason": "Mistook it for an LOG request"},
    )
    as_log = anon.get(f"/api/v1/portal/claims/{claim_id}", headers=_auth()).json()
    assert as_log["claim_type"] == label
    assert as_log["case_type"] == CASE_TYPE_LOG

    back = broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "claim", "reason": "It is an ordinary reimbursement"},
    )
    assert back.status_code == 200
    assert back.json()["case_type"] == CASE_TYPE_CLAIM
    assert back.json()["claim_type"] == label


def test_reclassification_is_recorded_on_the_case(broker, anon):
    """The trail is the record of why the correction was made."""
    from app.db.session import SessionLocal
    from app.models import Claim as ClaimModel

    claim_id = _member_claim(anon)
    broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "log", "reason": "Hospital emailed it as an LOG"},
    )
    with SessionLocal() as session:
        meta = session.get(ClaimModel, claim_id).intake_meta or {}
    trail = meta.get("conversions") or []
    assert len(trail) == 1
    assert trail[0]["from"] == CASE_TYPE_CLAIM
    assert trail[0]["to"] == CASE_TYPE_LOG
    assert trail[0]["reason"] == "Hospital emailed it as an LOG"


def test_relabel_is_idempotent(broker):
    """A double-clicked confirm dialog must not report an error after the first
    click succeeded."""
    claim_id = _create_log(broker).json()["id"]
    again = broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "log", "reason": "double click"},
    )
    assert again.status_code == 200
    assert again.json()["case_type"] == CASE_TYPE_LOG


def test_relabel_refused_once_the_outcome_is_recorded(broker):
    claim_id = _create_log(broker).json()["id"]
    approved = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "acknowledge": True},
    )
    assert approved.status_code == 200

    res = broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "claim", "reason": "too late"},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "case_type_locked"


def test_relabel_requires_a_reason(broker):
    claim_id = _create_log(broker).json()["id"]
    assert broker.patch(
        f"/api/v1/claims/{claim_id}/case-type", json={"case_type": "claim"}
    ).status_code == 422
    assert broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "claim", "reason": "   "},
    ).status_code == 422


# ── Queue + decisions ────────────────────────────────────────────────────────


def test_case_type_filter_defaults_to_both(broker, anon):
    _member_claim(anon)
    _create_log(broker)

    both = broker.get(f"/api/v1/claims?policy_year_id={PY}")
    assert both.json()["total"] == 2

    logs = broker.get(f"/api/v1/claims?policy_year_id={PY}&case_type=log")
    assert logs.json()["total"] == 1
    assert logs.json()["items"][0]["case_type"] == CASE_TYPE_LOG

    claims = broker.get(f"/api/v1/claims?policy_year_id={PY}&case_type=claim")
    assert claims.json()["total"] == 1
    assert claims.json()["items"][0]["case_type"] == CASE_TYPE_CLAIM

    bad = broker.get(f"/api/v1/claims?policy_year_id={PY}&case_type=nope")
    assert bad.status_code == 422


def test_a_log_case_is_decided_by_the_ordinary_decision_endpoint(broker):
    claim_id = _create_log(broker, amount_claimed=1200.0).json()["id"]
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 1000.0, "note": "Guaranteed"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    assert res.json()["amount_approved"] == 1000.0


def test_a_log_case_counts_against_the_members_limit(broker):
    """No separate money path: an approved LOG case consumes the limit exactly
    like any other approved claim."""
    claim_id = _create_log(broker, amount_claimed=25000.0).json()["id"]
    broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "acknowledge": True},
    )
    util = broker.get(f"/api/v1/employees/{EMP_A}/utilization").json()
    ghs = next(b for b in util["insured"] if b["product_code"] == "GHS" and not b["benefit_key"])
    assert ghs["approved"] == 25000.0
    assert ghs["remaining"] == 75000.0


def test_claims_register_marks_the_case_type(broker):
    _create_log(broker)
    res = broker.get(f"/api/v1/claims/register?policy_year_id={PY}")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
