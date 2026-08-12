"""Claim episodes — the earlier visit a claim continues.

Design: `docs/CLAIM_EPISODES_PLAN.md`. What is pinned here is everything that is
either a REFUSAL (a wrong anchor is worse than no anchor) or a rule whose
failure is silent — the cross-year claimant resolution, the root normalization,
and the specialist follow-up riding the anchor's referral rather than whichever
letter was uploaded most recently.

Same shape as `test_claim_amendment.py`: a canned statement, the AI pipeline
stubbed out.
"""
from __future__ import annotations

import itertools
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_claim_episodes.db"
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
    Product,
    StoredDocument,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.models.product_term import ProductTerm  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    StatementEmployee,
)
from scripts.seed_demo import seed  # noqa: E402

# The CURRENT year the portal resolves against, and the one before it — the
# cross-year anchor case (a December admission, a January consult) is the whole
# reason `_Claimant` exists, so the fixture has to be able to produce it.
PY_PREV = "00000000-0000-0000-0000-00000000ep00"
PY = "00000000-0000-0000-0000-00000000ep01"
EMP_A = "00000000-0000-0000-0000-00000000ep02"
EMP_A_PREV = "00000000-0000-0000-0000-00000000ep0a"
ACC_A = "00000000-0000-0000-0000-00000000ep03"
DEP_A = "00000000-0000-0000-0000-00000000ep04"
DEP_A_PREV = "00000000-0000-0000-0000-00000000ep0b"
# A second member of the same company — the "someone else's visit" case.
EMP_B = "00000000-0000-0000-0000-00000000ep05"
ACC_B = "00000000-0000-0000-0000-00000000ep06"

PDF = b"%PDF-1.4 episode test receipt"

PRE_POST = "Follow up Pre-/Post-Hospitalisation"
HOSPITALISATION = "Hospitalisation/Day Surgery/Other Inpatient Treatment"


def _statement_for(employee: Employee) -> BenefitStatementOut:
    dep = DependantSummary(id=DEP_A, name="Alice Jr", relationship="child")
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
                benefit_schedule={"items": [{"number": "1", "name": "Room & Board"}]},
                covers_dependants=True,
                covered_dependants=[dep],
            ),
            CoverageLine(
                product_code="SP",
                product_name="Specialist",
                plan_code="P1",
                benefit_schedule={"items": [{"number": "1", "name": "SP Consultation"}]},
                covers_dependants=True,
                covered_dependants=[dep],
            ),
            CoverageLine(
                product_code="GCGP",
                product_name="Group Clinical GP",
                plan_code="P1",
                benefit_schedule={"items": [{"number": "1", "name": "GP Consultation"}]},
                covers_dependants=False,
                covered_dependants=[],
            ),
        ],
        dependants=[dep],
        flex=None,
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    os.environ["INSPRO_STORAGE_DIR"] = str(tmp_path_factory.mktemp("episode_storage"))
    clear_settings_cache()

    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        for pid, year, start, end, status in (
            (PY_PREV, 2026, date(2026, 4, 1), date(2027, 3, 31), PolicyYearStatus.archived),
            (PY, 2027, date(2027, 4, 1), date(2028, 3, 31), PolicyYearStatus.active),
        ):
            session.add(
                PolicyYear(
                    id=pid, client_id=DEMO_CLIENT_ID, year=year,
                    start_date=start, end_date=end, status=status,
                )
            )
        session.flush()
        session.add(
            MemberAccount(
                id=ACC_A, client_id=DEMO_CLIENT_ID, email="alice@episode.test",
                staff_id="EP-1", status="active",
            )
        )
        session.add(
            MemberAccount(
                id=ACC_B, client_id=DEMO_CLIENT_ID, email="bob@episode.test",
                staff_id="EP-2", status="active",
            )
        )
        # The SAME PERSON in two benefit years — different Employee rows, same
        # (client_id, staff_id). This is what the cross-year resolution keys on.
        for eid, pid, acc in (
            (EMP_A_PREV, PY_PREV, None),
            (EMP_A, PY, ACC_A),
        ):
            session.add(
                Employee(
                    id=eid, client_id=DEMO_CLIENT_ID, policy_year_id=pid,
                    staff_id="EP-1", employee_name="Alice", member_account_id=acc,
                    attribute_values={}, derived_attribute_values={},
                    source="csv_import", status="active",
                )
            )
        session.add(
            Employee(
                id=EMP_B, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="EP-2", employee_name="Bob", member_account_id=ACC_B,
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status="active",
            )
        )
        session.flush()
        # The dependant in both years, carrying the same national ID — the only
        # stable cross-year identity a dependant has.
        for did, pid, eid in (
            (DEP_A_PREV, PY_PREV, EMP_A_PREV),
            (DEP_A, PY, EMP_A),
        ):
            session.add(
                Dependant(
                    id=did, client_id=DEMO_CLIENT_ID, policy_year_id=pid,
                    employee_id=eid,
                    attribute_values={"name": "Alice Jr", "relationship": "child"},
                    national_id_normalized="S1234567D",
                    link_method="staff_id", status="active",
                )
            )
        session.commit()
    yield
    engine.dispose()
    os.environ.pop("INSPRO_STORAGE_DIR", None)
    clear_settings_cache()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _canned_statement(monkeypatch):
    from app.api.v1 import portal_claims
    from app.services import claims as claims_service
    from app.services import log_cases

    for module in (claims_service, portal_claims, log_cases):
        monkeypatch.setattr(
            module, "build_member_statement", lambda db, emp: _statement_for(emp)
        )


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
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


def _auth(account: str = ACC_A) -> dict[str, str]:
    token, _ = issue_member_token(account, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


_invoice_seq = itertools.count(1)


def _next_invoice() -> str:
    return f"EP-{next(_invoice_seq):05d}"


def _draft(anon: TestClient, *, account: str = ACC_A, **overrides) -> dict:
    body = {
        "claim_kind": "insured",
        "product_code": "GHS",
        "claim_type": "Group Hospital & Surgical",
        "sub_type": HOSPITALISATION,
        "incurred_date": "2027-06-15",
        "provider_name": "Mount Elizabeth Hospital",
        "invoice_number": _next_invoice(),
        "diagnosis": "Appendicitis",
        "amount_claimed": 8400.0,
        "currency": "SGD",
    }
    body.update(overrides)
    return anon.post("/api/v1/portal/claims", json=body, headers=_auth(account))


def _submitted(anon: TestClient, *, account: str = ACC_A, **overrides) -> dict:
    res = _draft(anon, account=account, **overrides)
    assert res.status_code == 201, res.text
    claim = res.json()
    # Every inpatient slot, so the helper works for a government hospital (one
    # finalised invoice) and a private one (summary + itemised + discharge)
    # alike — the hospital name varies per test and the sector decides the set.
    for slot in (
        "finalised_tax_invoice",
        "summary_tax_invoice",
        "itemised_tax_invoice",
        "discharge_summary",
        "invoice_receipt",
        "sp_invoice",
    ):
        up = anon.post(
            f"/api/v1/portal/claims/{claim['id']}/documents",
            files={
                "file": (
                    f"{slot}.pdf",
                    PDF + claim["id"].encode() + slot.encode(),
                    "application/pdf",
                )
            },
            data={"doc_type": slot},
            headers=_auth(account),
        )
        assert up.status_code == 200, up.text
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/submit", headers=_auth(account)
    )
    assert res.status_code == 200, res.text
    return res.json()


def _anchors(anon: TestClient, mode: str, *, dependant_id: str | None = None):
    url = f"/api/v1/portal/claim-anchors?mode={mode}"
    if dependant_id:
        url += f"&dependant_id={dependant_id}"
    res = anon.get(url, headers=_auth())
    assert res.status_code == 200, res.text
    return res.json()


_upload_seq = itertools.count(1)


def _referral(anon: TestClient, name: str, issued_on: str | None = None) -> str:
    res = anon.post(
        "/api/v1/portal/referral-letters",
        files={"file": (name, PDF + name.encode(), "application/pdf")},
        data={"issued_on": issued_on} if issued_on else {},
        headers=_auth(),
    )
    assert res.status_code == 201, res.text
    doc_id = res.json()["id"]
    # Stamp a strictly increasing upload time. `latest_referral_letter` orders
    # by `created_at`, and SQLite stores it at second resolution — two letters
    # uploaded in the same test tick tie, and "newest" then depends on row
    # order rather than on time. That ambiguity is the test's problem, not the
    # code's, but it has to be removed for the fallback to be assertable.
    with SessionLocal() as s:
        doc = s.get(StoredDocument, doc_id)
        doc.created_at = datetime(2027, 1, 1, tzinfo=UTC) + timedelta(
            minutes=next(_upload_seq)
        )
        s.commit()
    return doc_id


# ── The picker ────────────────────────────────────────────────────────────────


def test_only_admissions_are_offered_as_admission_anchors(anon):
    admission = _submitted(anon)
    _submitted(
        anon, product_code="GCGP", claim_type="GP (General Practitioner)",
        sub_type=None, provider_name="Raffles Medical", amount_claimed=68.0,
    )
    ids = [a["id"] for a in _anchors(anon, "admission")]
    assert admission["id"] in ids
    # The GP claim is a visit, but it is not a hospital stay.
    assert len(ids) == 1


def test_a_draft_is_never_offered_as_an_anchor(anon):
    draft = _draft(anon)
    assert draft.status_code == 201
    ids = [a["id"] for a in _anchors(anon, "admission")]
    assert draft.json()["id"] not in ids


def test_an_admission_in_the_previous_year_is_still_offered(anon):
    """`Employee` rows are per policy YEAR, so the same person has a different
    employee_id last year. A December admission with its January consults is the
    ordinary shape of a pre/post episode — scoping to one row would empty the
    picker exactly when it matters."""
    with SessionLocal() as s:
        s.add(
            Claim(
                id="00000000-0000-0000-0000-0000000prev1",
                client_id=DEMO_CLIENT_ID, policy_year_id=PY_PREV,
                employee_id=EMP_A_PREV, claim_kind="insured", product_code="GHS",
                claim_type="Group Hospital & Surgical", sub_type=HOSPITALISATION,
                incurred_date=date(2027, 3, 20), provider_name="Gleneagles",
                amount_claimed=5000.0, currency="SGD", status="approved",
                reference_no="CLM-PREV-1",
            )
        )
        s.commit()
    ids = [a["id"] for a in _anchors(anon, "admission")]
    assert "00000000-0000-0000-0000-0000000prev1" in ids


def test_another_members_admission_is_never_offered(anon):
    theirs = _submitted(anon, account=ACC_B)
    assert theirs["id"] not in [a["id"] for a in _anchors(anon, "admission")]


def test_a_dependants_admission_does_not_anchor_the_members_own_consult(anon):
    """The failure this prevents is not a 404 — it is one household member's
    diagnosis prefilled into another's claim form."""
    for_dep = _submitted(anon, dependant_id=DEP_A, provider_name="Thomson Medical")
    assert for_dep["id"] not in [a["id"] for a in _anchors(anon, "admission")]
    assert for_dep["id"] in [
        a["id"] for a in _anchors(anon, "admission", dependant_id=DEP_A)
    ]


# ── The LOG carve-out ─────────────────────────────────────────────────────────


def test_a_guaranteed_admission_is_offered_but_carries_nothing_broker_entered(
    anon, broker
):
    """A stay settled by Letter of Guarantee is never member-visible, and it is
    the commonest anchor there is. It is offered — and it LINKS without
    PREFILLING: no diagnosis, no doctor, nothing an assessor wrote."""
    res = broker.post(
        f"/api/v1/employees/{EMP_A}/log-cases",
        json={
            "claim_kind": "insured", "product_code": "GHS",
            "sub_type": HOSPITALISATION,
            "incurred_date": "2027-07-02", "amount_claimed": 12000.0,
            "currency": "SGD", "provider_name": "Raffles Hospital",
            "diagnosis": "Broker-entered diagnosis",
            "received_via": "email", "received_on": "2027-07-01",
        },
    )
    assert res.status_code == 201, res.text
    log_id = res.json()["id"]

    # It is NOT in the member's own claim list...
    listed = anon.get("/api/v1/portal/claims", headers=_auth()).json()
    assert log_id not in [c["id"] for c in listed["items"]]

    # ...but it IS an anchor, projected down to the facts they lived through.
    offered = {a["id"]: a for a in _anchors(anon, "admission")}
    assert log_id in offered
    anchor = offered[log_id]
    assert anchor["from_records"] is True
    assert anchor["provider_name"] == "Raffles Hospital"
    assert anchor["diagnosis"] is None
    assert anchor["doctor_name"] is None


def test_the_assessor_sees_a_log_anchor_whole_but_the_member_does_not(
    anon, broker
):
    """The redaction protects the MEMBER's form from broker-written text. Applied
    to the assessor it protected nothing — they hold the LOG claim in full on
    their own queue — and cost them the comparison the episode rules ask them to
    make, on exactly the guaranteed admissions they cannot check anywhere else.
    """
    res = broker.post(
        f"/api/v1/employees/{EMP_A}/log-cases",
        json={
            "claim_kind": "insured", "product_code": "GHS",
            "sub_type": HOSPITALISATION,
            "incurred_date": "2027-07-04", "amount_claimed": 9000.0,
            "currency": "SGD", "provider_name": "Mount Alvernia Hospital",
            "diagnosis": "Laparoscopic cholecystectomy",
            "received_via": "email", "received_on": "2027-07-03",
        },
    )
    assert res.status_code == 201, res.text
    log_id = res.json()["id"]

    consult = _pre_post(anon, log_id, provider_name="Camden Medical Centre")
    assert consult.status_code == 201, consult.text
    claim_id = consult.json()["id"]

    # The member's own payload keeps the redaction — the link, not the words.
    mine = anon.get(f"/api/v1/portal/claims/{claim_id}", headers=_auth()).json()
    assert mine["related_claim"]["id"] == log_id
    assert mine["related_claim"]["provider_name"] == "Mount Alvernia Hospital"
    assert mine["related_claim"]["diagnosis"] is None
    assert mine["related_claim"]["from_records"] is True

    # The assessor's does not.
    theirs = broker.get(f"/api/v1/claims/{claim_id}")
    assert theirs.status_code == 200, theirs.text
    anchor = theirs.json()["related_claim"]
    assert anchor["diagnosis"] == "Laparoscopic cholecystectomy"
    # Still flagged as broker-entered: that is context for both audiences, not
    # something being withheld from one of them.
    assert anchor["from_records"] is True


# ── Creating a claim against an anchor ────────────────────────────────────────


def _pre_post(anon: TestClient, anchor_id: str | None, **overrides):
    body = {
        "product_code": "GHS",
        "claim_type": "Group Hospital & Surgical",
        "sub_type": PRE_POST,
        "provider_name": "Novena Specialist Clinic",
        "doctor_name": "Dr Tan",
        "amount_claimed": 180.0,
        "related_claim_id": anchor_id,
    }
    body.update(overrides)
    return _draft(anon, **body)


def test_a_pre_post_consult_records_the_admission_it_follows(anon):
    admission = _submitted(anon, provider_name="Mount Alvernia Hospital")
    res = _pre_post(anon, admission["id"])
    assert res.status_code == 201, res.text
    claim = res.json()
    assert claim["related_claim_id"] == admission["id"]
    assert claim["related_claim"]["provider_name"] == "Mount Alvernia Hospital"


def test_a_claim_with_no_anchor_still_files(anon):
    """The link is never required. An admission settled by guarantee under a
    different broker, or one predating our data, must not stop a member."""
    res = _pre_post(anon, None)
    assert res.status_code == 201, res.text
    assert res.json()["related_claim_id"] is None


def test_another_members_claim_is_a_404_not_a_403(anon):
    theirs = _submitted(anon, account=ACC_B, provider_name="Farrer Park Hospital")
    res = _pre_post(anon, theirs["id"])
    # 404 — "someone else's claim" and "no such claim" must be indistinguishable.
    assert res.status_code == 404, res.text


def test_an_anchor_for_a_different_claimant_is_refused(anon):
    for_dep = _submitted(anon, dependant_id=DEP_A, provider_name="KKH")
    res = _pre_post(anon, for_dep["id"])
    assert res.status_code == 422
    assert "different person" in res.json()["detail"]


def test_a_gp_claim_cannot_name_an_admission(anon):
    """A claim type that continues nothing simply drops the link rather than
    erroring — nothing about a GP receipt is invalid because of it."""
    admission = _submitted(anon, provider_name="Parkway East")
    res = _draft(
        anon, product_code="GCGP", claim_type="GP (General Practitioner)",
        sub_type=None, provider_name="Raffles Medical", amount_claimed=68.0,
        related_claim_id=admission["id"],
    )
    assert res.status_code == 201, res.text
    assert res.json()["related_claim_id"] is None


def test_the_anchor_is_stored_as_the_root_of_its_episode(anon):
    """Depth-1 by construction: a follow-up of a follow-up still points at the
    admission, so "everything in this episode" stays one query."""
    admission = _submitted(anon, provider_name="Mount Elizabeth Novena")
    first = _pre_post(anon, admission["id"])
    assert first.status_code == 201
    second = _pre_post(anon, first.json()["id"], incurred_date="2027-07-20")
    assert second.status_code == 201, second.text
    assert second.json()["related_claim_id"] == admission["id"]


# ── The referral defect this closes ───────────────────────────────────────────


def _sp(anon: TestClient, *, visit_type: str, **overrides):
    body = {
        "product_code": "SP",
        "claim_type": "SP (Specialist)",
        "sub_type": None,
        "visit_type": visit_type,
        "provider_name": "Cardiology Associates",
        "diagnosis": "Atrial fibrillation",
        "amount_claimed": 250.0,
    }
    body.update(overrides)
    return _draft(anon, **body)


def test_a_follow_up_rides_the_anchors_referral_not_the_newest_letter(anon):
    """A member under two specialists at once uploads a second letter. Without
    the anchor, "latest on file" attaches the cardiology referral to the
    orthopaedic follow-up — silently, and the review's referral check passes it,
    because a letter IS attached."""
    cardiology = _referral(anon, "cardiology.pdf")
    first = _submitted(
        anon, product_code="SP", claim_type="SP (Specialist)", sub_type=None,
        visit_type="first", provider_name="Cardiology Associates",
        diagnosis="Atrial fibrillation", amount_claimed=250.0,
        referral_document_id=cardiology,
    )

    # A newer, unrelated referral lands on file afterwards.
    orthopaedic = _referral(anon, "orthopaedic.pdf")
    assert orthopaedic != cardiology

    follow_up = _sp(
        anon, visit_type="follow_up", related_claim_id=first["id"],
        diagnosis="Atrial fibrillation",
    )
    assert follow_up.status_code == 201, follow_up.text
    assert follow_up.json()["referral_document_id"] == cardiology


def test_a_follow_up_with_no_anchor_still_falls_back_to_the_latest_letter(anon):
    """Every claim filed before episodes existed takes this path — it must keep
    working."""
    newest = _referral(anon, "newest.pdf")
    res = _sp(anon, visit_type="follow_up")
    assert res.status_code == 201, res.text
    assert res.json()["referral_document_id"] == newest


def test_a_first_visit_may_not_name_an_earlier_visit(anon):
    """A first visit that named a course would be claiming to continue the thing
    it starts."""
    letter = _referral(anon, "first-visit.pdf")
    first = _submitted(
        anon, product_code="SP", claim_type="SP (Specialist)", sub_type=None,
        visit_type="first", provider_name="Ortho Associates",
        diagnosis="Fractured wrist", amount_claimed=250.0,
        referral_document_id=letter,
    )
    second = _sp(
        anon, visit_type="first", referral_document_id=letter,
        related_claim_id=first["id"],
    )
    assert second.status_code == 201, second.text
    assert second.json()["related_claim_id"] is None


# ── Amendment ─────────────────────────────────────────────────────────────────


def test_amending_the_type_away_from_pre_post_clears_the_anchor(anon):
    """Cleared, not refused. A 422 would make the claim uncorrectable from any
    surface that doesn't expose the anchor control — the same trap
    `clear_rider_key` exists to avoid for `benefit_key`."""
    admission = _submitted(anon, provider_name="Bright Vision")
    consult = _pre_post(anon, admission["id"]).json()
    res = anon.patch(
        f"/api/v1/portal/claims/{consult['id']}",
        json={
            "sub_type": "Emergency Accidental Outpatient Treatment",
            "expected_revision": consult["revision"],
        },
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    assert res.json()["related_claim_id"] is None


def test_a_broker_may_clear_the_link_on_a_settled_claim(anon, broker):
    admission = _submitted(anon, provider_name="Sengkang General")
    consult = _pre_post(anon, admission["id"]).json()
    res = broker.patch(
        f"/api/v1/claims/{consult['id']}",
        json={
            "related_claim_id": None,
            "expected_revision": consult["revision"],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["related_claim_id"] is None


# ── Review rules ──────────────────────────────────────────────────────────────


def _rule(results: list[dict], needle: str) -> dict | None:
    return next((r for r in results if needle in r["rule"]), None)


def _rules_for(claim_id: str) -> list[dict]:
    from app.services.claims_review.rules import deterministic_rule_results

    with SessionLocal() as s:
        claim = s.get(Claim, claim_id)
        employee = s.get(Employee, claim.employee_id)
        return deterministic_rule_results(s, claim, _statement_for(employee))


def test_a_consult_outside_the_stated_window_is_flagged(anon):
    with SessionLocal() as s:
        product_id = s.execute(
            Product.__table__.select().where(Product.code == "GHS")
        ).first()[0]
        s.add(
            ProductTerm(
                policy_year_id=PY, product_id=product_id,
                pre_hosp_days=90, post_hosp_days=100,
            )
        )
        s.commit()

    admission = _submitted(anon, incurred_date="2027-05-01", provider_name="TTSH")
    with SessionLocal() as s:
        stay = s.get(Claim, admission["id"])
        stay.admission_date = date(2027, 5, 1)
        stay.discharge_date = date(2027, 5, 5)
        s.commit()

    inside = _pre_post(anon, admission["id"], incurred_date="2027-06-01").json()
    assert _rule(_rules_for(inside["id"]), "pre-/post-hospitalisation window")[
        "status"
    ] == "pass"

    # 100 days after 5 May is 13 August; the 1st of October is well past it.
    outside = _pre_post(anon, admission["id"], incurred_date="2027-10-01").json()
    breach = _rule(_rules_for(outside["id"]), "pre-/post-hospitalisation window")
    assert breach["status"] == "warning"
    assert breach.get("flag") is True


def test_a_pre_post_consult_naming_no_admission_is_flagged(anon):
    consult = _pre_post(anon, None, incurred_date="2027-06-20").json()
    row = _rule(_rules_for(consult["id"]), "names the admission it follows")
    assert row["status"] == "warning"
    assert row.get("flag") is True


def test_a_follow_up_for_a_different_condition_is_flagged(anon):
    admission = _submitted(
        anon, provider_name="Changi General", diagnosis="Appendicitis"
    )
    consult = _pre_post(
        anon, admission["id"], diagnosis="Other: sprained ankle"
    ).json()
    row = _rule(_rules_for(consult["id"]), "Diagnosis matches")
    assert row["status"] == "warning"
    assert row.get("flag") is True


def test_a_stale_referral_is_flagged_only_when_the_letter_is_dated(anon):
    dated = _referral(anon, "old-referral.pdf", issued_on="2026-01-05")
    stale = _sp(anon, visit_type="first", referral_document_id=dated)
    assert stale.status_code == 201, stale.text
    row = _rule(_rules_for(stale.json()["id"]), "Referral letter is still valid")
    assert row["status"] == "warning"
    assert row.get("flag") is True

    undated = _referral(anon, "undated-referral.pdf")
    plain = _sp(anon, visit_type="first", referral_document_id=undated)
    # No issue date, no rule — the UPLOAD date is never read as a stand-in.
    assert _rule(_rules_for(plain.json()["id"]), "Referral letter is still valid") is None


def test_a_referral_dated_after_the_visit_is_flagged_not_passed(anon):
    """A referral is written BEFORE the consultation it authorises.

    The age goes negative, which trivially satisfies "within validity" — so the
    strongest signal this rule can see was the one it reported as a pass,
    printing a clamped "0 days before the visit" for something that happened the
    other way round.
    """
    # Stamped directly rather than uploaded with the date: the upload guard
    # refuses a letter dated in the future relative to TODAY, and the claims in
    # this module are incurred inside the 2027 policy year. The two dates the
    # rule compares are the letter's and the CLAIM's, which is the case here
    # either way.
    later = _referral(anon, "post-dated-referral.pdf")
    with SessionLocal() as s:
        s.get(StoredDocument, later).issued_on = date(2027, 9, 1)
        s.commit()
    res = _sp(anon, visit_type="first", referral_document_id=later)
    assert res.status_code == 201, res.text
    row = _rule(_rules_for(res.json()["id"]), "Referral letter is still valid")
    assert row["status"] == "warning"
    assert row.get("flag") is True
    assert "AFTER the visit" in row["evidence"]


def test_a_cross_year_course_files_on_its_own_referral_letter(anon):
    """The picker offers a specialist course from the PREVIOUS year, so the
    letter it rides on is stamped with last year's `Employee` row.

    Checking the referral against the CURRENT row refused the anchor the member
    had just been offered — a 404 naming a document they never chose and could
    not fix. The letter must also still be listed, or the picker holds an id the
    control cannot display.
    """
    with SessionLocal() as s:
        doc = StoredDocument(
            id="00000000-0000-0000-0000-00000000ref1",
            client_id=DEMO_CLIENT_ID,
            entity_type="referral",
            entity_id=EMP_A_PREV,
            file_name="lastyear.pdf",
            storage_path="k/lastyear.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            sha256="a" * 64,
        )
        s.add(doc)
        s.add(
            Claim(
                id="00000000-0000-0000-0000-00000000sp01",
                client_id=DEMO_CLIENT_ID, policy_year_id=PY_PREV,
                employee_id=EMP_A_PREV, claim_kind="insured", product_code="SP",
                claim_type="SP (Specialist)", visit_type="first",
                incurred_date=date(2027, 3, 20), provider_name="Ortho Clinic",
                diagnosis="Knee pain", amount_claimed=250.0, currency="SGD",
                status="approved", reference_no="CLM-SP-PREV",
                referral_document_id="00000000-0000-0000-0000-00000000ref1",
                origin="portal",
            )
        )
        s.commit()
    ids = [a["id"] for a in _anchors(anon, "sp_course")]
    assert "00000000-0000-0000-0000-00000000sp01" in ids, ids

    # Listed, so the "existing letter" control can show what the anchor chose.
    listed = anon.get("/api/v1/portal/referral-letters", headers=_auth())
    assert listed.status_code == 200, listed.text
    assert "00000000-0000-0000-0000-00000000ref1" in [
        d["id"] for d in listed.json()
    ]

    res = _draft(
        anon, product_code="SP", claim_type="SP (Specialist)", sub_type=None,
        visit_type="follow_up", provider_name="Ortho Clinic",
        diagnosis="Knee pain", amount_claimed=250.0,
        related_claim_id="00000000-0000-0000-0000-00000000sp01",
    )
    assert res.status_code == 201, res.text
    assert (
        res.json()["referral_document_id"]
        == "00000000-0000-0000-0000-00000000ref1"
    )


def test_a_recycled_staff_id_never_reaches_another_persons_claims(anon):
    """Cross-year identity is `person_employee_ids`, which applies the account
    binding rule — not `(client_id, staff_id)` alone.

    A staff id that has been reissued, or a roster carrying a placeholder,
    otherwise puts a stranger's admission in the picker and their diagnosis into
    this member's form. A row bound to a DIFFERENT member account is never this
    person, whatever its staff id says.
    """
    stranger_emp = "00000000-0000-0000-0000-00000000str1"
    stranger_claim = "00000000-0000-0000-0000-00000000str2"
    with SessionLocal() as s:
        s.add(
            Employee(
                id=stranger_emp, client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_PREV,
                # The same staff id Alice carries — and somebody else's account.
                staff_id="EP-1", employee_name="Not Alice",
                member_account_id=ACC_B,
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status="active",
            )
        )
        s.flush()
        s.add(
            Claim(
                id=stranger_claim, client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_PREV, employee_id=stranger_emp,
                claim_kind="insured", product_code="GHS",
                claim_type="Group Hospital & Surgical", sub_type=HOSPITALISATION,
                incurred_date=date(2027, 3, 21), provider_name="Mount Alvernia",
                diagnosis="Something private", amount_claimed=9000.0,
                currency="SGD", status="approved", reference_no="CLM-STR-1",
                origin="portal",
            )
        )
        s.commit()
    assert stranger_claim not in [a["id"] for a in _anchors(anon, "admission")]

    # And naming it directly is a 404, the same as any other claim that isn't
    # theirs — not a 403, which would confirm it exists.
    res = _pre_post(anon, stranger_claim)
    assert res.status_code == 404, res.text
