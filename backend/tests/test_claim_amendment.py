"""Member claim amendment — the edit window, the interlocks, the trail.

Design: `docs/CLAIM_AMENDMENT_PLAN.md`. The WINDOW itself (which statuses are
editable, and the served flag the portal renders off) is pinned in
`test_claims_lifecycle.py` beside the rest of the status machine; what lives
here is what happens when a member actually changes something.

Like the lifecycle module, `build_member_statement` is monkeypatched to a canned
shape and the AI pipeline is stubbed out — this exercises the amendment rules,
not plan hydration or the review.
"""
from __future__ import annotations

import itertools
import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_claim_amendment.db"
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
    AuditLog,
    Claim,
    ClaimAIReview,
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

PY = "00000000-0000-0000-0000-00000000am01"
EMP_A = "00000000-0000-0000-0000-00000000am02"
ACC_A = "00000000-0000-0000-0000-00000000am03"
DEP_A = "00000000-0000-0000-0000-00000000am04"

PDF = b"%PDF-1.4 amendment test receipt"


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
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "Room & Board", "value": "S$650/day"},
                    ]
                },
                covers_dependants=True,
                covered_dependants=[dep],
            ),
            CoverageLine(
                product_code="GCGP",
                product_name="Group Clinical GP",
                plan_code="P1",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "GP Consultation", "value": "As charged"},
                        {
                            "number": "2",
                            "name": "TCM & Chiropractor",
                            "value": "S$300 per policy year",
                        },
                    ]
                },
                covers_dependants=False,
                covered_dependants=[],
            ),
        ],
        dependants=[dep],
        flex=FlexCoverageLine(
            tier_name="Tier 1",
            wallet_amount=1000.0,
            currency="SGD",
            benefit_categories=[
                FlexBenefitCategoryLine(name="Dental", claimable=True, sub_limit=500.0),
            ],
        ),
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    os.environ["INSPRO_STORAGE_DIR"] = str(tmp_path_factory.mktemp("amend_storage"))
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
        session.add(
            MemberAccount(
                id=ACC_A, client_id=DEMO_CLIENT_ID, email="alice@amend.test",
                staff_id="AM-1", status="active",
            )
        )
        session.add(
            Employee(
                id=EMP_A, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="AM-1", employee_name="Alice", member_account_id=ACC_A,
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
    engine.dispose()
    os.environ.pop("INSPRO_STORAGE_DIR", None)
    clear_settings_cache()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _canned_statement(monkeypatch):
    from app.api.v1 import portal_claims
    from app.services import claims as claims_service

    monkeypatch.setattr(
        claims_service, "build_member_statement", lambda db, emp: _statement_for(emp)
    )
    monkeypatch.setattr(
        portal_claims, "build_member_statement", lambda db, emp: _statement_for(emp)
    )


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
    """Submit parks the claim at `ai_review_pending` — the pipeline itself is
    covered by test_claims_review_pipeline.py."""
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


def _auth() -> dict[str, str]:
    token, _ = issue_member_token(ACC_A, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


_invoice_seq = itertools.count(1)


def _next_invoice() -> str:
    return f"AM-{next(_invoice_seq):05d}"


def _draft(anon: TestClient, **overrides) -> dict:
    body = {
        "claim_kind": "insured",
        "product_code": "GHS",
        "claim_type": "Group Hospital & Surgical",
        "sub_type": "Emergency Accidental Outpatient Treatment",
        "incurred_date": "2027-06-15",
        "provider_name": "Raffles Medical",
        "invoice_number": _next_invoice(),
        "diagnosis": "Dengue fever",
        "amount_claimed": 85.0,
        "currency": "SGD",
    }
    body.update(overrides)
    res = anon.post("/api/v1/portal/claims", json=body, headers=_auth())
    assert res.status_code == 201, res.text
    return res.json()


def _upload(anon: TestClient, claim_id: str, marker: bytes, doc_type=None):
    return anon.post(
        f"/api/v1/portal/claims/{claim_id}/documents",
        files={"file": ("receipt.pdf", PDF + marker, "application/pdf")},
        data={"doc_type": doc_type} if doc_type else {},
        headers=_auth(),
    )


def _submitted(anon: TestClient, marker: bytes, **overrides) -> dict:
    claim = _draft(anon, **overrides)
    assert _upload(anon, claim["id"], marker).status_code == 200
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/submit", headers=_auth()
    )
    assert res.status_code == 200, res.text
    return res.json()


def _amend(anon: TestClient, claim_id: str, **body):
    return anon.patch(
        f"/api/v1/portal/claims/{claim_id}", json=body, headers=_auth()
    )


def _get(anon: TestClient, claim_id: str) -> dict:
    res = anon.get(f"/api/v1/portal/claims/{claim_id}", headers=_auth())
    assert res.status_code == 200, res.text
    return res.json()


def _thread(anon: TestClient, claim_id: str) -> list[dict]:
    res = anon.get(
        f"/api/v1/portal/claims/{claim_id}/messages", headers=_auth()
    )
    assert res.status_code == 200, res.text
    return res.json()


def _code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


# ── The happy path ───────────────────────────────────────────────────────────


def test_a_member_corrects_a_submitted_claim(anon: TestClient):
    """The case the whole feature exists for: the figure was mistyped and the
    claim is still sitting in the queue."""
    claim = _submitted(anon, b" happy")
    # Still 0: the receipt went on while the claim was a DRAFT, and a draft is
    # in front of nobody, so nothing is stamped.
    assert claim["revision"] == 0

    res = _amend(
        anon, claim["id"], amount_claimed=105.0, expected_revision=claim["revision"]
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["amount_claimed"] == 105.0
    assert body["revision"] == claim["revision"] + 1
    assert body["amended_at"] is not None
    # Still theirs to change — nobody has decided anything.
    assert body["member_editable"] is True


def test_the_form_snapshot_follows_the_amendment(anon: TestClient):
    """`form_fields` is what the AI review compares documents against, so an
    amended claim that kept its original snapshot would have every comparison
    reported against a figure the member has already corrected."""
    claim = _submitted(anon, b" snapshot")
    assert _amend(anon, claim["id"], amount_claimed=210.5).status_code == 200

    with SessionLocal() as s:
        row = s.get(Claim, claim["id"])
        assert row.form_fields["amount_claimed"] == 210.5
        assert row.form_fields["invoice_number"] == row.invoice_number


def test_a_partial_amendment_leaves_everything_else_alone(anon: TestClient):
    """`model_fields_set`, for the reason `ClaimAssessmentIn` uses it: an edit
    sheet that touches one control must not blank the rest."""
    claim = _submitted(anon, b" partial", diagnosis="Dengue fever")
    before = _get(anon, claim["id"])

    assert _amend(anon, claim["id"], provider_name="Mount Elizabeth").status_code == 200
    after = _get(anon, claim["id"])

    assert after["provider_name"] == "Mount Elizabeth"
    for field in ("diagnosis", "invoice_number", "amount_claimed", "incurred_date"):
        assert after[field] == before[field], field


def test_an_explicit_null_clears_a_nullable_field(anon: TestClient):
    """Absence means "leave alone" and `null` means "clear" — which is how a
    claim stops being for a dependant."""
    claim = _submitted(anon, b" clearing", dependant_id=DEP_A)
    assert _get(anon, claim["id"])["dependant_id"] == DEP_A

    assert _amend(anon, claim["id"], dependant_id=None).status_code == 200
    assert _get(anon, claim["id"])["dependant_id"] is None


def test_an_empty_amendment_is_refused(anon: TestClient):
    """Otherwise it bumps the revision, supersedes the AI review and posts the
    member a notice saying they changed something — for a request that changed
    nothing."""
    claim = _submitted(anon, b" empty")
    assert _amend(anon, claim["id"]).status_code == 422
    assert _amend(anon, claim["id"], expected_revision=claim["revision"]).status_code == 422


def test_a_required_field_cannot_be_cleared(anon: TestClient):
    """`null` on a NOT NULL column would either 500 on flush or store a claim
    nothing downstream can render."""
    claim = _submitted(anon, b" notnull")
    assert _amend(anon, claim["id"], amount_claimed=None).status_code == 422
    assert _amend(anon, claim["id"], invoice_number=None).status_code == 422


# ── The window ───────────────────────────────────────────────────────────────


def test_amending_a_decided_claim_is_refused(anon: TestClient, broker: TestClient):
    """A 403 carrying the SERVED sentence — word-for-word what the claim page
    already told them. Not a 404: the member owns this claim and can see it."""
    claim = _submitted(anon, b" decided")
    assert broker.post(
        f"/api/v1/claims/{claim['id']}/decision", json={"action": "approve"}
    ).status_code == 200

    res = _amend(anon, claim["id"], amount_claimed=1.0)
    assert res.status_code == 403
    assert _code(res) == "claim_not_editable"
    assert res.json()["detail"]["message"] == _get(anon, claim["id"])["member_edit_block"]


def test_a_needs_info_claim_stays_editable_and_stays_needs_info(
    anon: TestClient, broker: TestClient
):
    """Amending a `needs_info` claim is not answering it — the member still has
    to press send, and the notice says so."""
    claim = _submitted(anon, b" needsinfo")
    assert broker.post(
        f"/api/v1/claims/{claim['id']}/decision",
        json={"action": "needs_info", "note": "Send the itemised bill."},
    ).status_code == 200

    res = _amend(anon, claim["id"], amount_claimed=99.0)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "needs_info"
    assert "send it again" in _thread(anon, claim["id"])[-1]["body"]


# ── The interlocks ───────────────────────────────────────────────────────────


def test_an_amendment_supersedes_the_ai_verdict(anon: TestClient):
    """A verdict is a statement about a specific set of claimed values. Once
    they change it describes a claim that no longer exists, so it is superseded
    and the claim falls back to plain `submitted` for manual review — never
    auto-rerun, which would make an edit loop an AI-spend loop.
    """
    claim = _submitted(anon, b" verdict")
    with SessionLocal() as s:
        row = s.get(Claim, claim["id"])
        row.status = "ai_verified"
        s.add(
            ClaimAIReview(
                client_id=DEMO_CLIENT_ID, claim_id=row.id,
                status="complete", verdict="clean",
            )
        )
        s.commit()

    res = _amend(anon, claim["id"], amount_claimed=42.0)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "submitted"

    with SessionLocal() as s:
        reviews = s.query(ClaimAIReview).filter_by(claim_id=claim["id"]).all()
        assert reviews and all(r.superseded for r in reviews)


def test_a_stale_revision_is_refused(anon: TestClient):
    """Two devices on one claim is the ordinary case, not an exotic one."""
    claim = _submitted(anon, b" stale")
    assert _amend(
        anon, claim["id"], amount_claimed=50.0, expected_revision=claim["revision"]
    ).status_code == 200

    res = _amend(
        anon, claim["id"], amount_claimed=60.0, expected_revision=claim["revision"]
    )
    assert res.status_code == 409
    assert _code(res) == "claim_amended"
    assert res.json()["detail"]["revision"] == claim["revision"] + 1
    # Omitting it skips the check — the same claim goes straight through.
    assert _amend(anon, claim["id"], amount_claimed=60.0).status_code == 200


def test_an_amendment_runs_the_whole_submit_chain(anon: TestClient):
    """The invariant the split exists for: an edited claim is always a claim
    that would pass submit, so the broker's queue can never hold an invalid
    row."""
    claim = _submitted(anon, b" chain")

    # Outside the policy year.
    res = _amend(anon, claim["id"], incurred_date="2030-01-01")
    assert res.status_code == 422
    assert "policy year" in res.json()["detail"]

    # Coverage the member doesn't hold.
    assert _amend(anon, claim["id"], product_code="NOPE").status_code == 422

    # A dependant the plan doesn't cover it for.
    assert _amend(
        anon, claim["id"], product_code="GCGP", claim_type="Group Clinical GP",
        sub_type=None, dependant_id=DEP_A,
    ).status_code == 422


def test_amending_onto_a_duplicate_invoice_is_refused(anon: TestClient):
    """One invoice is one claim, on the amendment path exactly as at submit —
    and with no member-side override here either."""
    first = _submitted(anon, b" dup-a")
    second = _submitted(anon, b" dup-b")

    res = _amend(anon, second["id"], invoice_number=first["invoice_number"])
    assert res.status_code == 409
    assert _code(res) == "duplicate_invoice_number"

    # Its OWN number is not a duplicate of itself.
    assert _amend(
        anon, second["id"], invoice_number=second["invoice_number"]
    ).status_code == 200


def test_amending_away_from_a_rider_moves_the_benefit_key(anon: TestClient):
    """The GP-rider clear, end to end: `benefit_key` decides which limit the
    claim draws on, so a stale one silently bills the wrong bucket."""
    claim = _submitted(
        anon, b" rider",
        product_code="GCGP",
        claim_type="TCM (Traditional Chinese Medicine)",
        sub_type="TCM (Traditional Chinese Medicine)",
        diagnosis="Other: lower back pain",
    )
    assert claim["benefit_key"] == "TCM & Chiropractor"

    res = _amend(
        anon, claim["id"], sub_type=None, claim_type="Group Clinical GP"
    )
    assert res.status_code == 200, res.text
    assert res.json()["benefit_key"] is None


# ── Documents ────────────────────────────────────────────────────────────────


def test_removing_the_only_receipt_from_a_submitted_claim_is_refused(
    anon: TestClient,
):
    """A submitted claim is in front of an assessor. Deleting its only receipt
    would park a claim there that can never be progressed."""
    claim = _submitted(anon, b" doc-last")
    doc_id = _get(anon, claim["id"])["documents"][0]["id"]

    res = anon.delete(
        f"/api/v1/portal/claims/{claim['id']}/documents/{doc_id}",
        headers=_auth(),
    )
    assert res.status_code == 409
    assert _code(res) == "documents_required"
    assert len(_get(anon, claim["id"])["documents"]) == 1


def test_a_replaced_receipt_can_be_removed(anon: TestClient):
    """Add the replacement first, then remove the wrong one — which is what the
    refusal above tells the member to do."""
    claim = _submitted(anon, b" doc-swap")
    wrong = _get(anon, claim["id"])["documents"][0]["id"]
    assert _upload(anon, claim["id"], b" doc-swap-2").status_code == 200

    res = anon.delete(
        f"/api/v1/portal/claims/{claim['id']}/documents/{wrong}",
        headers=_auth(),
    )
    assert res.status_code == 204, res.text
    remaining = _get(anon, claim["id"])["documents"]
    assert [d["id"] for d in remaining] != [wrong] and len(remaining) == 1


def test_a_draft_may_be_emptied(anon: TestClient):
    """A draft is in front of nobody, and submit asks for the documents when it
    is sent."""
    claim = _draft(anon)
    assert _upload(anon, claim["id"], b" doc-draft").status_code == 200
    doc_id = _get(anon, claim["id"])["documents"][0]["id"]

    res = anon.delete(
        f"/api/v1/portal/claims/{claim['id']}/documents/{doc_id}",
        headers=_auth(),
    )
    assert res.status_code == 204, res.text
    assert _get(anon, claim["id"])["documents"] == []
    # A draft is not stamped — it is nobody's but the member's.
    assert _get(anon, claim["id"])["revision"] == 0


def test_a_document_change_stamps_the_claim(anon: TestClient):
    """Evidence IS what a verdict is about, so the document set moving has to
    invalidate a review and bump the revision exactly as a figure moving does."""
    claim = _submitted(anon, b" doc-stamp")
    with SessionLocal() as s:
        s.add(
            ClaimAIReview(
                client_id=DEMO_CLIENT_ID, claim_id=claim["id"],
                status="complete", verdict="clean",
            )
        )
        s.get(Claim, claim["id"]).status = "ai_verified"
        s.commit()

    assert _upload(anon, claim["id"], b" doc-stamp-2").status_code == 200
    after = _get(anon, claim["id"])
    assert after["revision"] == claim["revision"] + 1
    assert after["status"] == "submitted"
    with SessionLocal() as s:
        assert all(
            r.superseded
            for r in s.query(ClaimAIReview).filter_by(claim_id=claim["id"]).all()
        )


def test_a_referral_letter_cannot_be_deleted_through_a_claim(anon: TestClient):
    """The member-level referral letter has its own endpoint with its own
    in-use guard. Reaching it through here would strand every other claim
    riding on the same letter."""
    res = anon.post(
        "/api/v1/portal/referral-letters",
        files={"file": ("ref.pdf", PDF + b" ref", "application/pdf")},
        headers=_auth(),
    )
    assert res.status_code == 201, res.text
    referral_id = res.json()["id"]

    claim = _submitted(anon, b" ref-claim")
    assert anon.delete(
        f"/api/v1/portal/claims/{claim['id']}/documents/{referral_id}",
        headers=_auth(),
    ).status_code == 404


# ── The record ───────────────────────────────────────────────────────────────


def test_the_audit_row_keeps_the_figure_it_exists_to_preserve(anon: TestClient):
    """Snapshotted BEFORE the merge. Taken after, `before` reads off an
    already-mutated claim and a correction from 1,200.00 to 120.00 writes
    before=120.00 / after=120.00 — losing the only number the row was for."""
    claim = _submitted(anon, b" audit", amount_claimed=1200.0)
    assert _amend(anon, claim["id"], amount_claimed=120.0).status_code == 200

    with SessionLocal() as s:
        row = (
            s.query(AuditLog)
            .filter(AuditLog.entity_id == claim["id"], AuditLog.action == "claim.amended")
            .order_by(AuditLog.created_at.desc())
            .first()
        )
        assert row is not None
        assert row.before["amount_claimed"] == 1200.0
        assert row.after["amount_claimed"] == 120.0
        assert row.after["revision"] == claim["revision"] + 1


def test_the_thread_names_what_changed_without_reprinting_it(anon: TestClient):
    """The claim page the thread sits on already prints every current value,
    and the before/after belongs in the audit log — one history, not two."""
    claim = _submitted(anon, b" notice", amount_claimed=88.0)
    assert _amend(
        anon, claim["id"], amount_claimed=99.0, invoice_number=_next_invoice()
    ).status_code == 200

    last = _thread(anon, claim["id"])[-1]
    assert last["event"] == "amended"
    assert last["author_type"] == "system"
    assert "the amount claimed" in last["body"]
    assert "the invoice number" in last["body"]
    assert "99" not in last["body"]


def test_a_draft_amendment_posts_nothing(anon: TestClient):
    """A draft has no thread — nothing has been sent."""
    claim = _draft(anon)
    assert _amend(anon, claim["id"], amount_claimed=12.0).status_code == 200
    assert _thread(anon, claim["id"]) == []


# ── The broker's amendment ───────────────────────────────────────────────────


def _broker_amend(broker: TestClient, claim_id: str, **body):
    return broker.patch(f"/api/v1/claims/{claim_id}", json=body)


def test_a_broker_corrects_a_live_claim_freely(
    anon: TestClient, broker: TestClient
):
    claim = _submitted(anon, b" bk-live")
    res = _broker_amend(broker, claim["id"], amount_claimed=77.0)
    assert res.status_code == 200, res.text
    assert res.json()["amount_claimed"] == 77.0
    assert res.json()["revision"] == claim["revision"] + 1


def test_a_broker_correction_to_a_settled_claim_needs_a_reason(
    anon: TestClient, broker: TestClient
):
    """By now the figure has been given to the member, and on a dispatched
    claim to the insurer as well. Mirrors `ClaimCaseTypeIn.reason`."""
    claim = _submitted(anon, b" bk-settled")
    assert broker.post(
        f"/api/v1/claims/{claim['id']}/decision", json={"action": "approve"}
    ).status_code == 200

    res = _broker_amend(broker, claim["id"], amount_claimed=55.0)
    assert res.status_code == 422
    assert _code(res) == "reason_required"

    res = _broker_amend(
        broker, claim["id"], amount_claimed=55.0, reason="Invoice re-read: 55.00."
    )
    assert res.status_code == 200, res.text
    assert res.json()["amount_claimed"] == 55.0


def test_a_reason_alone_is_not_a_change(anon: TestClient, broker: TestClient):
    """`reason` STEERS an amendment; it is not part of one. Counting it would
    let a body carrying nothing else bump the revision and record an edit that
    never happened."""
    claim = _submitted(anon, b" bk-reasononly")
    assert _broker_amend(broker, claim["id"], reason="just because").status_code == 422


def test_the_broker_may_not_rewrite_the_members_note(
    anon: TestClient, broker: TestClient
):
    """`remarks` is the member's own sentence and they can read it back. An
    assessor editing it would be putting words in their mouth — so the field is
    absent from the broker schema and a request carrying it is ignored."""
    claim = _submitted(anon, b" bk-remarks", remarks="I paid cash on the day.")
    res = _broker_amend(
        broker, claim["id"], remarks="member says otherwise", amount_claimed=91.0
    )
    assert res.status_code == 200, res.text
    assert res.json()["amount_claimed"] == 91.0
    assert res.json()["remarks"] == "I paid cash on the day."


def test_a_broker_amendment_leaves_the_review_and_the_thread_alone(
    anon: TestClient, broker: TestClient
):
    """The assessor is READING the review while they correct the claim, so
    invalidating it under them is the opposite of useful. And a broker changing
    what a member claimed needs a sentence a person wrote, not a generated
    one."""
    claim = _submitted(anon, b" bk-quiet")
    with SessionLocal() as s:
        s.add(
            ClaimAIReview(
                client_id=DEMO_CLIENT_ID, claim_id=claim["id"],
                status="complete", verdict="clean",
            )
        )
        s.get(Claim, claim["id"]).status = "ai_verified"
        s.commit()
    before_thread = len(_thread(anon, claim["id"]))

    assert _broker_amend(broker, claim["id"], amount_claimed=64.0).status_code == 200

    body = _get(anon, claim["id"])
    assert body["status"] == "ai_verified"  # not knocked back to submitted
    assert len(_thread(anon, claim["id"])) == before_thread
    with SessionLocal() as s:
        assert not any(
            r.superseded
            for r in s.query(ClaimAIReview).filter_by(claim_id=claim["id"]).all()
        )


def test_a_decision_on_a_stale_read_is_refused(
    anon: TestClient, broker: TestClient
):
    """THE race the revision guard exists for: the assessor reads $150, the
    member corrects it to $105, and the assessor approves what they read."""
    claim = _submitted(anon, b" race", amount_claimed=150.0)
    seen = claim["revision"]

    assert _amend(anon, claim["id"], amount_claimed=105.0).status_code == 200

    res = broker.post(
        f"/api/v1/claims/{claim['id']}/decision",
        json={"action": "approve", "expected_revision": seen},
    )
    assert res.status_code == 409
    assert _code(res) == "claim_amended"
    # Nothing was decided.
    assert _get(anon, claim["id"])["status"] != "approved"

    # Reloading and deciding on what is actually there works, and approves the
    # CORRECTED figure.
    current = _get(anon, claim["id"])["revision"]
    res = broker.post(
        f"/api/v1/claims/{claim['id']}/decision",
        json={"action": "approve", "expected_revision": current},
    )
    assert res.status_code == 200, res.text
    assert res.json()["amount_approved"] == 105.0


def test_a_decision_without_a_revision_still_works(
    anon: TestClient, broker: TestClient
):
    """Optional on purpose — a LOG case created and decided in one assessor
    flow has no stale read to guard against, and making it mandatory would
    break every existing caller."""
    claim = _submitted(anon, b" norev")
    assert broker.post(
        f"/api/v1/claims/{claim['id']}/decision", json={"action": "approve"}
    ).status_code == 200


def test_a_broker_amendment_runs_the_same_validation(
    anon: TestClient, broker: TestClient
):
    """An assessor correcting a claim is subject to the same truth about what
    is claimable as the member who filed it."""
    claim = _submitted(anon, b" bk-valid")
    assert _broker_amend(broker, claim["id"], incurred_date="2030-01-01").status_code == 422
    assert _broker_amend(broker, claim["id"], product_code="NOPE").status_code == 422
