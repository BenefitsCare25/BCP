"""AI claim-review pipeline: stage orchestration, short-circuits, vision cap,
degradation to manual review, rerun supersession, submit dispatch.

The AI **gateway** functions are monkeypatched (the pipeline is exercised for
real; provider/cache/breaker plumbing has its own coverage in
test_ai_gateway_claims.py).
"""
from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

TEST_DB = Path(__file__).parent / "_test_claims_pipeline.db"
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
from app.core.storage import document_path, get_storage  # noqa: E402
from app.db.base import Base, new_uuid  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Claim,
    ClaimAIReview,
    Employee,
    MemberAccount,
    PolicyYear,
    StoredDocument,
)
from app.models.claim import (  # noqa: E402
    CLAIM_STATUS_AI_FLAGGED,
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_SUBMITTED,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    StatementEmployee,
)
from app.services.ai_extractor import AINotConfiguredError  # noqa: E402
from app.services.ai_gateway import (  # noqa: E402
    ClaimExtractionResult,
    ClaimReviewAIResult,
    ClaimVerifyResult,
)
from app.services.claims_review import pipeline  # noqa: E402
from app.services.claims_review.pipeline import run_review  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-00000000cr01"
EMP = "00000000-0000-0000-0000-00000000cr02"
ACC = "00000000-0000-0000-0000-00000000cr03"

META = {"provider": "anthropic", "model": "claude-test", "input_tokens": 100, "output_tokens": 50}


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    storage_dir = tmp_path_factory.mktemp("pipeline_storage")
    os.environ["INSPRO_STORAGE_DIR"] = str(storage_dir)
    clear_settings_cache()

    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY,
                client_id=DEMO_CLIENT_ID,
                year=2028,  # NOT 2026 — seed() one_or_none's the demo 2026 year
                start_date=date(2028, 4, 1),
                end_date=date(2029, 3, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        session.add(
            MemberAccount(
                id=ACC, client_id=DEMO_CLIENT_ID, email="pat@cr.test",
                staff_id="CR-1", status="active",
            )
        )
        session.flush()
        session.add(
            Employee(
                id=EMP, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="CR-1", employee_name="Pat", member_account_id=ACC,
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status="active",
            )
        )
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(ClaimAIReview).delete()
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


def _statement(employee) -> BenefitStatementOut:
    return BenefitStatementOut(
        employee=StatementEmployee(
            id=employee.id, staff_id=employee.staff_id,
            employee_name=employee.employee_name,
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
                    "items": [{"number": "1", "name": "Outpatient GP", "value": "As charged"}]
                },
            )
        ],
        dependants=[],
        flex=None,
    )


@pytest.fixture(autouse=True)
def _canned_statement(monkeypatch):
    from app.api.v1 import portal_claims
    from app.services import claims as claims_service

    for mod in (pipeline, claims_service, portal_claims):
        monkeypatch.setattr(mod, "build_member_statement", lambda db, emp: _statement(emp))


def _mk_claim(
    *,
    status: str = CLAIM_STATUS_AI_REVIEW_PENDING,
    incurred: date = date(2028, 6, 15),
    amount: float = 85.0,
    currency: str = "SGD",
    docs: int = 1,
    marker: bytes = b"",
) -> tuple[str, str]:
    """Create a claim (+ pending review row + stored docs). Returns (claim_id, review_id)."""
    with SessionLocal() as s:
        claim = Claim(
            client_id=DEMO_CLIENT_ID,
            policy_year_id=PY,
            employee_id=EMP,
            claim_kind="insured",
            product_code="GHS",
            benefit_key="Outpatient GP",
            claim_type="outpatient",
            incurred_date=incurred,
            provider_name="Raffles Medical",
            amount_claimed=amount,
            currency=currency,
            status=status,
            form_fields={
                "claim_type": "outpatient",
                "incurred_date": incurred.isoformat(),
                "provider_name": "Raffles Medical",
                "amount_claimed": amount,
                "currency": currency,
            },
        )
        s.add(claim)
        s.flush()
        for i in range(docs):
            content = b"%PDF-1.4 receipt " + marker + str(i).encode() + claim.id.encode()
            doc_id = new_uuid()
            path = document_path(
                DEMO_BROKER_FIRM_ID, DEMO_CLIENT_ID, "claim", claim.id, doc_id, ".pdf"
            )
            blob = get_storage().save(io.BytesIO(content), path)
            s.add(
                StoredDocument(
                    id=doc_id, client_id=DEMO_CLIENT_ID, entity_type="claim",
                    entity_id=claim.id, file_name=f"receipt-{i}.pdf",
                    mime_type="application/pdf", size_bytes=blob.size_bytes,
                    sha256=blob.sha256, storage_path=blob.path,
                )
            )
        review = ClaimAIReview(client_id=DEMO_CLIENT_ID, claim_id=claim.id)
        s.add(review)
        s.commit()
        return claim.id, review.id


def _extract_result(**over) -> ClaimExtractionResult:
    doc = {
        "document_type": "receipt",
        "fields": [
            {"id": "field_1", "label": "Total Amount", "value": "85.00",
             "field_type": "currency", "confidence": 0.95},
        ],
    }
    doc.update(over)
    return ClaimExtractionResult(document=doc, metadata=dict(META), cache_hit=False)


def _review_result(comparisons, confidence=0.9, rules=None, req_docs=None):
    return ClaimReviewAIResult(
        review={
            "field_comparisons": comparisons,
            "rule_results": rules or [],
            "required_documents_check": req_docs
            or [{"document_type_name": "receipt or tax invoice", "found": True}],
            "summary": "Reviewed.",
            "confidence": confidence,
        },
        metadata=dict(META),
        cache_hit=False,
    )


def _verify_result(verdict: str) -> ClaimVerifyResult:
    return ClaimVerifyResult(
        verdict=verdict, explanation="looked at the document",
        metadata=dict(META), cache_hit=False,
    )


def _match(field: str):
    return {"field_name": field, "claim_value": "x", "document_value": "x",
            "status": "MATCH", "confidence": 0.98}


def _mismatch(field: str, value: str = "85.00"):
    return {"field_name": field, "claim_value": value, "document_value": "12.00",
            "status": "MISMATCH", "confidence": 0.9}


def _load(claim_id: str, review_id: str) -> tuple[Claim, ClaimAIReview]:
    with SessionLocal() as s:
        return s.get(Claim, claim_id), s.get(ClaimAIReview, review_id)


# ── Stage orchestration ───────────────────────────────────────────────────────


def test_clean_run_verifies_claim():
    claim_id, review_id = _mk_claim(marker=b"clean")
    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()) as ex, \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_match("amount_claimed")])) as rv, \
         patch("app.services.ai_gateway.verify_claim_concern") as vf:
        run_review(claim_id, review_id, None)

    assert ex.call_count == 1 and rv.call_count == 1 and vf.call_count == 0
    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_VERIFIED
    assert review.status == "complete"
    assert review.verdict == "clean"
    assert review.confidence == 0.9
    assert review.extractions[0]["document_type"] == "receipt"
    assert any(c["status"] == "MATCH" for c in review.field_comparisons)
    # Deterministic + AI rules both recorded.
    sources = {r["source"] for r in review.rule_results}
    assert sources == {"deterministic", "ai"}
    # Token accounting: extract + review = 2 live calls.
    assert review.input_tokens == 200 and review.output_tokens == 100
    assert review.cost_estimate_usd > 0
    assert review.model == "claude-test"


def test_deterministic_fail_short_circuits_with_zero_ai_calls():
    claim_id, review_id = _mk_claim(incurred=date(2027, 1, 1), marker=b"oob")
    with patch("app.services.ai_gateway.extract_claim_document") as ex, \
         patch("app.services.ai_gateway.review_claim") as rv, \
         patch("app.services.ai_gateway.verify_claim_concern") as vf:
        run_review(claim_id, review_id, None)

    assert ex.call_count == 0 and rv.call_count == 0 and vf.call_count == 0
    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_FLAGGED
    assert review.status == "complete"
    assert review.verdict == "flagged"
    assert review.cost_estimate_usd == 0.0
    failed = [r for r in review.rule_results if r["status"] == "fail"]
    assert failed and "policy year" in failed[0]["evidence"]
    assert "deterministic" in review.summary.lower() or "Flagged" in review.summary


def test_vision_confirm_flips_mismatch_to_match():
    claim_id, review_id = _mk_claim(marker=b"flip")
    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_mismatch("amount_claimed")])), \
         patch("app.services.ai_gateway.verify_claim_concern",
               return_value=_verify_result("CONFIRMED")) as vf:
        run_review(claim_id, review_id, None)

    assert vf.call_count == 1
    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_VERIFIED
    assert review.verdict == "clean"
    comp = next(c for c in review.field_comparisons if c["field_name"] == "amount_claimed")
    assert comp["status"] == "MATCH"
    assert comp["vision_verified"] is True
    assert review.vision_checks[0]["verdict"] == "CONFIRMED"


def test_vision_refuted_keeps_claim_flagged():
    claim_id, review_id = _mk_claim(marker=b"refute")
    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_mismatch("amount_claimed")])), \
         patch("app.services.ai_gateway.verify_claim_concern",
               return_value=_verify_result("REFUTED")):
        run_review(claim_id, review_id, None)

    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_FLAGGED
    assert review.verdict == "flagged"


def test_vision_checks_capped_at_four():
    # 3 vision-eligible mismatches x 2 docs, every verdict UNCERTAIN → the
    # pipeline burns its 4-check budget and stops.
    claim_id, review_id = _mk_claim(docs=2, marker=b"cap")
    comparisons = [
        _mismatch("amount_claimed"),
        _mismatch("incurred_date", "2028-06-15"),
        _mismatch("provider_name", "Raffles Medical"),
    ]
    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result(comparisons)), \
         patch("app.services.ai_gateway.verify_claim_concern",
               return_value=_verify_result("UNCERTAIN")) as vf:
        run_review(claim_id, review_id, None)

    assert vf.call_count == 4
    claim, review = _load(claim_id, review_id)
    assert len(review.vision_checks) == 4
    assert claim.status == CLAIM_STATUS_AI_FLAGGED


def test_non_vision_field_not_verified():
    claim_id, review_id = _mk_claim(marker=b"novis")
    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_mismatch("currency", "SGD")])), \
         patch("app.services.ai_gateway.verify_claim_concern") as vf:
        run_review(claim_id, review_id, None)
    assert vf.call_count == 0  # currency map opts out of vision
    claim, _ = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_FLAGGED


# ── Degradation ───────────────────────────────────────────────────────────────


def test_provider_error_degrades_to_manual_review():
    claim_id, review_id = _mk_claim(marker=b"err")
    with patch("app.services.ai_gateway.extract_claim_document",
               side_effect=RuntimeError("provider down")):
        run_review(claim_id, review_id, None)

    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_SUBMITTED  # back to manual review
    assert review.status == "error"
    assert "provider down" in review.error_detail


def test_ai_not_configured_saves_deterministic_results():
    claim_id, review_id = _mk_claim(marker=b"nocfg")
    with patch("app.services.ai_gateway.extract_claim_document",
               side_effect=AINotConfiguredError("no provider")):
        run_review(claim_id, review_id, None)

    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_SUBMITTED
    assert review.status == "error"
    # Deterministic pre-checks ran before the AI stage and were kept.
    assert review.rule_results and all(
        r["source"] == "deterministic" for r in review.rule_results
    )


def test_broker_decision_wins_race():
    """If the broker decided while the pipeline ran, the verdict must not
    clobber the terminal status."""
    claim_id, review_id = _mk_claim(status="approved", marker=b"race")
    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_match("amount_claimed")])):
        run_review(claim_id, review_id, None)
    claim, review = _load(claim_id, review_id)
    assert claim.status == "approved"
    assert review.status == "complete"  # the review itself still lands


# ── Endpoints: rerun + submit dispatch + broker visibility ────────────────────


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


def test_rerun_supersedes_previous_review(broker: TestClient):
    claim_id, review_id = _mk_claim(marker=b"rerun")
    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_mismatch("amount_claimed")])), \
         patch("app.services.ai_gateway.verify_claim_concern",
               return_value=_verify_result("REFUTED")):
        run_review(claim_id, review_id, None)
    claim, _ = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_FLAGGED

    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_match("amount_claimed")])):
        res = broker.post(f"/api/v1/claims/{claim_id}/rerun-review")
    assert res.status_code == 200, res.text

    with SessionLocal() as s:
        old = s.get(ClaimAIReview, review_id)
        assert old.superseded is True
        claim = s.get(Claim, claim_id)
        assert claim.status == CLAIM_STATUS_AI_VERIFIED

    detail = broker.get(f"/api/v1/claims/{claim_id}/review")
    assert detail.status_code == 200
    body = detail.json()
    assert body["verdict"] == "clean"
    assert body["id"] != review_id


def test_rerun_from_draft_409(broker: TestClient):
    claim_id, _ = _mk_claim(status="draft", marker=b"draftrerun")
    res = broker.post(f"/api/v1/claims/{claim_id}/rerun-review")
    assert res.status_code == 409


def test_review_404_when_none(broker: TestClient):
    claim_id, review_id = _mk_claim(marker=b"noreview")
    with SessionLocal() as s:
        s.delete(s.get(ClaimAIReview, review_id))
        s.commit()
    assert broker.get(f"/api/v1/claims/{claim_id}/review").status_code == 404


def test_submit_dispatches_pipeline_and_broker_sees_verdict(broker: TestClient):
    anon = TestClient(app)
    token, _ = issue_member_token(ACC, DEMO_CLIENT_ID)
    headers = {"Authorization": f"Bearer {token}"}

    res = anon.post(
        "/api/v1/portal/claims",
        json={
            "claim_kind": "insured",
            "product_code": "GHS",
            "claim_type": "Group Hospital & Surgical",
            "sub_type": "Hospitalisation or Day Surgery",
            "incurred_date": "2028-06-15",
            "provider_name": "Raffles Medical",
            "invoice_number": "INV-00123",
            "diagnosis": "Dengue fever",
            "amount_claimed": 85.0,
            "currency": "SGD",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    claim_id = res.json()["id"]
    assert anon.post(
        f"/api/v1/portal/claims/{claim_id}/documents",
        files={"file": ("receipt.pdf", b"%PDF-1.4 submit-dispatch", "application/pdf")},
        headers=headers,
    ).status_code == 200

    with patch("app.services.ai_gateway.extract_claim_document",
               return_value=_extract_result()), \
         patch("app.services.ai_gateway.review_claim",
               return_value=_review_result([_match("amount_claimed")])) as review_mock:
        res = anon.post(f"/api/v1/portal/claims/{claim_id}/submit", headers=headers)
    assert res.status_code == 200, res.text
    # The review receives the claimant identity — without it the patient-name
    # rule has nothing to compare the documents against.
    review_fields = review_mock.call_args.kwargs["claim_fields"]
    assert review_fields["claimant_name"] == "Pat"
    assert review_fields["policyholder_name"] == "Pat"
    # The response is serialized before the background task runs.
    assert res.json()["status"] == CLAIM_STATUS_AI_REVIEW_PENDING

    with SessionLocal() as s:
        assert s.get(Claim, claim_id).status == CLAIM_STATUS_AI_VERIFIED

    listing = broker.get(f"/api/v1/claims?policy_year_id={PY}&status=ai_verified")
    row = next(c for c in listing.json()["items"] if c["id"] == claim_id)
    assert row["ai_review"]["verdict"] == "clean"
    assert row["ai_review"]["status"] == "complete"

    # The member payload never exposes the AI review.
    member_view = anon.get(f"/api/v1/portal/claims/{claim_id}", headers=headers)
    assert "ai_review" not in member_view.json()


def test_dependant_age_rule_warns_on_aged_out_child():
    """Scheme-level ANB eligibility window (child max 25 by default) surfaces
    a deterministic warning on claims for an aged-out dependant."""
    from app.models import Dependant
    from app.services.claims_review.rules import _check_dependant_age

    with SessionLocal() as s:
        dep_ok = Dependant(
            id=new_uuid(), client_id=DEMO_CLIENT_ID, policy_year_id=PY,
            employee_id=EMP,
            attribute_values={"name": "Kid", "relationship": "child",
                              "dob": "2020-06-01"},
            link_method="staff_id", status="active",
        )
        dep_old = Dependant(
            id=new_uuid(), client_id=DEMO_CLIENT_ID, policy_year_id=PY,
            employee_id=EMP,
            # Age 29 at the 2028-04-01 year start → ANB 30 > 25.
            attribute_values={"name": "Grown Kid", "relationship": "child",
                              "dob": "1998-06-01"},
            link_method="staff_id", status="active",
        )
        s.add_all([dep_ok, dep_old])
        s.flush()

        def _claim(dep_id: str) -> Claim:
            c = Claim(
                client_id=DEMO_CLIENT_ID, policy_year_id=PY, employee_id=EMP,
                dependant_id=dep_id, claim_kind="insured", product_code="GHS",
                claim_type="Group Hospital & Surgical",
                incurred_date=date(2028, 6, 15), amount_claimed=100.0,
                currency="SGD", status="submitted",
            )
            s.add(c)
            s.flush()
            return c

        assert _check_dependant_age(s, _claim(dep_ok.id))["status"] == "pass"
        warn = _check_dependant_age(s, _claim(dep_old.id))
        assert warn["status"] == "warning"
        assert "outside the eligibility window" in warn["evidence"]
        s.rollback()


def test_required_documents_referral_from_profile_not_name():
    """The specialist referral-letter requirement rides on the intake profile's
    requires_referral flag, not a 'specialist' substring in the display name."""
    from app.services.claims_review.field_maps import required_documents_for

    # Product name lacks the word 'specialist' → keyword scan would miss it,
    # but requires_referral=True still guarantees the referral family.
    docs = required_documents_for("GCSP", None, requires_referral=True)
    assert any("referral" in d.lower() for d in docs)

    # Not a specialist product → no referral family injected.
    docs = required_documents_for("Group Clinical GP", None, requires_referral=False)
    assert not any("referral" in d.lower() for d in docs)

    # Referral family isn't duplicated when the keyword branch already has it.
    docs = required_documents_for("Group Clinical Specialist", None, requires_referral=True)
    assert sum("referral" in d.lower() for d in docs) == 1
