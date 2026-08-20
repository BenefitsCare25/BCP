"""AI claim-review pipeline: stage orchestration, short-circuits, vision cap,
degradation to manual review, rerun supersession, submit dispatch.

The AI **gateway** functions are monkeypatched (the pipeline is exercised for
real; provider/cache/breaker plumbing has its own coverage in
test_ai_gateway_claims.py).
"""
from __future__ import annotations

import io
import os
from datetime import UTC, date, datetime
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
    ClaimReviewJob,
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
from app.services.claims_review.field_maps import AI_RULES, FIELD_MAPS  # noqa: E402
from app.services.claims_review.pipeline import run_review  # noqa: E402
from app.workers.claim_review import process_one_job  # noqa: E402
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
    present = {item.get("field_name") for item in comparisons}
    comparisons = list(comparisons) + [
        _match(field_map["portal_field"])
        for field_map in FIELD_MAPS
        if field_map["portal_field"] not in present
    ]
    if rules is None:
        rules = [
            {"rule": rule, "status": "pass", "evidence": "No concern found."}
            for rule in AI_RULES
        ]
    return ClaimReviewAIResult(
        review={
            "field_comparisons": comparisons,
            "rule_results": rules,
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


def test_missing_expected_ai_outputs_force_manual_review():
    """A provider may satisfy the tool schema with partial arrays. Missing
    configured comparisons/rules/doc checks must never be interpreted as clean."""
    claim_id, review_id = _mk_claim(marker=b"partial-ai-output")
    partial = ClaimReviewAIResult(
        review={
            "field_comparisons": [_match("amount_claimed")],
            "rule_results": [],
            "required_documents_check": [],
            "summary": "No discrepancies found.",
            "confidence": 0.99,
        },
        metadata=dict(META),
        cache_hit=False,
    )
    with patch(
        "app.services.ai_gateway.extract_claim_document",
        return_value=_extract_result(),
    ), patch("app.services.ai_gateway.review_claim", return_value=partial), patch(
        "app.services.ai_gateway.verify_claim_concern"
    ):
        run_review(claim_id, review_id, None)

    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_FLAGGED
    assert review.verdict == "flagged"
    incomplete = [
        result
        for result in review.rule_results
        if result.get("error_code") == "ai_output_incomplete"
    ]
    assert len(incomplete) == 3
    assert all("affected_fields" in result for result in incomplete)
    assert any("field comparison" in result["rule"] for result in incomplete)


def test_labelled_ai_comparison_names_are_reconciled():
    """Gemini may return human labels despite being prompted for keys."""
    claim_id, review_id = _mk_claim(marker=b"labelled-ai-output")
    with SessionLocal() as session:
        claim = session.get(Claim, claim_id)
        assert claim is not None
        claim.invoice_number = "INV-123"
        claim.diagnosis = "Other: QA smoke test"
        claim.form_fields = {
            **claim.form_fields,
            "invoice_number": claim.invoice_number,
            "diagnosis": claim.diagnosis,
        }
        session.commit()
    labelled = ClaimReviewAIResult(
        review={
            "field_comparisons": [
                _match("Amount claimed"),
                _match("Incurred date"),
                _match("Provider"),
                _match("invoice_number"),
                _match("Currency"),
                _match("diagnosis"),
            ],
            "rule_results": [
                {"rule": rule, "status": "pass", "evidence": "No concern found."}
                for rule in AI_RULES
            ],
            "required_documents_check": [
                {"document_type_name": "receipt or tax invoice", "found": True}
            ],
            "summary": "All fields match.",
            "confidence": 0.98,
        },
        metadata=dict(META),
        cache_hit=False,
    )
    with patch(
        "app.services.ai_gateway.extract_claim_document",
        return_value=_extract_result(),
    ), patch("app.services.ai_gateway.review_claim", return_value=labelled), patch(
        "app.services.ai_gateway.verify_claim_concern"
    ):
        run_review(claim_id, review_id, None)

    claim, review = _load(claim_id, review_id)
    assert claim.status == CLAIM_STATUS_AI_VERIFIED
    assert review.verdict == "clean"
    expected = {
        m["portal_field"]
        for m in FIELD_MAPS
        if claim.form_fields.get(m["portal_field"]) not in (None, "")
    }
    assert {c["field_name"] for c in review.field_comparisons} == expected
    assert not any(
        result.get("error_code") == "ai_output_incomplete"
        for result in review.rule_results
    )


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
    assert review.error_detail == "Claim review failed. Route the claim to manual review."
    assert "provider down" not in review.error_detail


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
        assert process_one_job("test-rerun-worker") is True
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
            "sub_type": "Emergency Accidental Outpatient Treatment",
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
        assert process_one_job("test-submit-worker") is True
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


def test_durable_worker_redacts_provider_failure_detail() -> None:
    claim_id, review_id = _mk_claim(marker=b"durable-error")
    with SessionLocal() as session:
        session.add(
            ClaimReviewJob(
                broker_firm_id=DEMO_BROKER_FIRM_ID,
                client_id=DEMO_CLIENT_ID,
                claim_id=claim_id,
                review_id=review_id,
                claim_revision=0,
                idempotency_key=f"test:{review_id}",
                available_at=datetime.now(UTC),
            )
        )
        session.commit()

    with patch(
        "app.services.ai_gateway.extract_claim_document",
        side_effect=RuntimeError("provider echoed sensitive claim content"),
    ):
        assert process_one_job("test-failure-worker") is True

    with SessionLocal() as session:
        claim = session.get(Claim, claim_id)
        review = session.get(ClaimAIReview, review_id)
        job = session.query(ClaimReviewJob).filter_by(review_id=review_id).one()
        assert claim.status == CLAIM_STATUS_SUBMITTED
        assert review.status == "error"
        assert review.error_detail == "Claim review failed. Route the claim to manual review."
        assert job.state == "failed"
        assert job.last_error_detail == review.error_detail
        assert "sensitive claim content" not in job.last_error_detail


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


# ── Document-type registry + key-field completeness (claim_doc_types) ─────────


def _ext(document_type, labels, file_name="doc.pdf"):
    return {
        "file_name": file_name,
        "document_type": document_type,
        "fields": [
            {"id": str(i), "label": lab, "value": "x", "field_type": "text",
             "confidence": 0.9}
            for i, lab in enumerate(labels, start=1)
        ],
    }


def _inpatient_claim(provider: str) -> Claim:
    return Claim(
        client_id="c1", policy_year_id="py1", employee_id="e1",
        claim_kind="insured", product_code="GHS",
        claim_type="Hospitalisation/Day Surgery/Other Inpatient Treatment",
        sub_type="Hospitalisation/Day Surgery/Other Inpatient Treatment",
        provider_name=provider, incurred_date=date(2027, 3, 1),
        amount_claimed=1000.0, currency="SGD", status="submitted",
    )


def test_doc_completeness_discharge_summary_optional_surgery():
    from app.services.claims_review.doc_completeness import doc_completeness_results

    claim = _inpatient_claim("Gleneagles Hospital")
    # "After Visit Summary" alias, showing a diagnosis but no surgery — a
    # non-surgical (medical) admission. Surgery is OPTIONAL, so this is a
    # complete document and must NOT warn.
    results = doc_completeness_results(
        claim, [_ext("After Visit Summary", ["Patient Name", "Diagnosis"])]
    )
    assert [r["status"] for r in results] == ["pass"]

    # Missing the REQUIRED Diagnosis field must block auto-clear.
    results = doc_completeness_results(
        claim, [_ext("After Visit Summary", ["Patient Name", "Ward"])]
    )
    warn = [r for r in results if r["status"] == "fail"]
    assert len(warn) == 1
    assert "Diagnosis" in warn[0]["evidence"]
    assert "Surgery" not in warn[0]["evidence"]  # optional, never flagged

    # Complete copy with surgery → pass.
    results = doc_completeness_results(
        claim,
        [_ext("Clinical Discharge Summary", ["Diagnosis", "Surgery Performed"])],
    )
    assert [r["status"] for r in results] == ["pass"]


def test_doc_completeness_private_final_tax_invoice():
    from app.services.claims_review.doc_completeness import doc_completeness_results

    claim = _inpatient_claim("Mount Alvernia Hospital")
    # Complete private final tax invoice.
    results = doc_completeness_results(
        claim,
        [_ext("tax invoice", [
            "Case Number", "Admission Date", "Discharge Date",
            "Final Bill", "HRN",
        ])],
    )
    assert [r["status"] for r in results] == ["pass"]

    # Missing HRN + Final Bill must block auto-clear and name both.
    results = doc_completeness_results(
        claim,
        [_ext("tax invoice", ["Case Number", "Admission Date", "Discharge Date"])],
    )
    assert [r["status"] for r in results] == ["fail"]
    assert "Final Bill" in results[0]["evidence"]
    assert "HRN" in results[0]["evidence"]


def test_doc_completeness_govt_finalised_invoice_and_sector_cross_check():
    from app.services.claims_review.doc_completeness import doc_completeness_results

    # Government bill (Schemes marker) on a government-hospital claim → pass.
    govt_labels = [
        "Admission Date", "Discharge Date", "MediShield Life Scheme", "HRN",
    ]
    claim = _inpatient_claim("Singapore General Hospital")
    results = doc_completeness_results(claim, [_ext("tax invoice", govt_labels)])
    assert [r["status"] for r in results] == ["pass"]

    # A government-format bill on a private claim must block auto-clear.
    claim = _inpatient_claim("Raffles Hospital")
    results = doc_completeness_results(claim, [_ext("tax invoice", govt_labels)])
    statuses = {r["rule"]: r["status"] for r in results}
    assert statuses["Invoice format matches the hospital's sector."] == "fail"


def test_doc_completeness_ignores_plain_outpatient_receipt():
    from app.services.claims_review.doc_completeness import doc_completeness_results

    claim = Claim(
        client_id="c1", policy_year_id="py1", employee_id="e1",
        claim_kind="insured", product_code="GP", claim_type="GP",
        provider_name="Raffles Medical Clinic", incurred_date=date(2027, 3, 1),
        amount_claimed=45.0, currency="SGD", status="submitted",
    )
    # An unlisted clinic's plain tax invoice — no inpatient markers, no sector
    # hint → never classified, never warned about.
    results = doc_completeness_results(
        claim,
        [_ext("tax invoice", ["Clinic Name", "Total Amount", "Invoice No"])],
    )
    assert results == []


# ── Broker-configurable registry: CRUD + custom config drives classification ──


def _reset_doc_types(broker: TestClient) -> list[dict]:
    current = broker.get("/api/v1/claim-doc-types").json()
    response = broker.post(
        "/api/v1/claim-doc-types/reset",
        json={
            "expected_versions": {
                row["id"]: row["updated_at"] for row in current
            }
        },
    )
    assert response.status_code == 200
    return response.json()


def test_doc_type_config_lazy_seed_and_crud(broker: TestClient):
    # First read lazily seeds the in-code defaults for the client.
    rows = broker.get("/api/v1/claim-doc-types").json()
    assert {r["key"] for r in rows} == {
        "discharge_summary", "final_tax_invoice", "finalised_tax_invoice"
    }
    assert all(r["is_default"] for r in rows)
    discharge = next(r for r in rows if r["key"] == "discharge_summary")
    assert discharge["claim_scope_keys"] == [
        "insured:*:ghs_hospitalisation_govt",
        "insured:*:ghs_hospitalisation_private",
    ]

    # Create a custom type; a duplicate name 409s.
    res = broker.post("/api/v1/claim-doc-types", json={
        "display": "Referral Memo",
        "aliases": ["memo", "referral memo"],
        "key_fields": [{"name": "Specialist", "keywords": []}],
        "claim_scope_keys": ["insured:*:ghs_pre_post"],
    })
    assert res.status_code == 201
    created = res.json()
    assert created["key"] == "referral_memo"
    assert created["is_default"] is False
    assert created["claim_scope_keys"] == [
        "insured:*:ghs_pre_post_govt",
        "insured:*:ghs_pre_post_private",
    ]
    dup = broker.post("/api/v1/claim-doc-types", json={
        "display": "Referral Memo", "aliases": [], "key_fields": [],
    })
    assert dup.status_code == 409

    # Unknown slot key is rejected.
    bad = broker.post("/api/v1/claim-doc-types", json={
        "display": "Weird", "aliases": [], "key_fields": [],
        "slot_key": "not_a_slot",
    })
    assert bad.status_code == 422
    bad_scope = broker.post("/api/v1/claim-doc-types", json={
        "display": "Bad scope", "aliases": [], "key_fields": [],
        "claim_scope_keys": ["insured:GHS:renamed_display_label"],
    })
    assert bad_scope.status_code == 422

    # Update an existing row (add an alias).
    res = broker.put(f"/api/v1/claim-doc-types/{discharge['id']}", json={
        "display": discharge["display"],
        "aliases": discharge["aliases"] + ["day surgery note"],
        "key_fields": discharge["key_fields"],
        "sector": discharge["sector"],
        "slot_key": discharge["slot_key"],
        "expected_updated_at": discharge["updated_at"],
    })
    assert res.status_code == 200
    assert "day surgery note" in res.json()["aliases"]
    # Older API clients omit the new field; omission preserves routing instead
    # of silently clearing it during an unrelated alias edit.
    assert res.json()["claim_scope_keys"] == discharge["claim_scope_keys"]
    stale = broker.put(f"/api/v1/claim-doc-types/{discharge['id']}", json={
        "display": discharge["display"],
        "aliases": discharge["aliases"],
        "key_fields": discharge["key_fields"],
        "sector": discharge["sector"],
        "slot_key": discharge["slot_key"],
        "expected_updated_at": discharge["updated_at"],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_configuration"

    # Delete the custom row; reset restores exactly the defaults.
    assert broker.delete(
        f"/api/v1/claim-doc-types/{created['id']}",
        params={"expected_updated_at": created["updated_at"]},
    ).status_code == 204
    rows = _reset_doc_types(broker)
    assert {r["key"] for r in rows} == {
        "discharge_summary", "final_tax_invoice", "finalised_tax_invoice"
    }


def test_document_scope_assignments_duplicate_atomically(broker: TestClient):
    rows = _reset_doc_types(broker)
    target = "insured:ghs:ghs_emergency_outpatient_govt"
    assignments = [
        {
            "id": row["id"],
            "expected_updated_at": row["updated_at"],
            "claim_scope_keys": [target] if row["key"] == "discharge_summary" else [],
        }
        for row in rows
    ]
    response = broker.post(
        "/api/v1/claim-doc-types/scope-assignments",
        json={"assignments": assignments},
    )
    assert response.status_code == 200
    by_key = {row["key"]: row for row in response.json()}
    assert by_key["discharge_summary"]["claim_scope_keys"] == [target]
    assert by_key["final_tax_invoice"]["claim_scope_keys"] == []

    # Any stale row rejects the entire batch; the first row must not be partly
    # saved before the conflict is discovered.
    current_versions = {row["id"]: row["updated_at"] for row in response.json()}
    stale_assignments = [
        {
            **assignment,
            "expected_updated_at": (
                current_versions[assignment["id"]]
                if index == 0
                else assignment["expected_updated_at"]
            ),
            "claim_scope_keys": [] if index == 0 else assignment["claim_scope_keys"],
        }
        for index, assignment in enumerate(assignments)
    ]
    stale = broker.post(
        "/api/v1/claim-doc-types/scope-assignments",
        json={"assignments": stale_assignments},
    )
    assert stale.status_code == 409
    current = broker.get("/api/v1/claim-doc-types").json()
    assert next(
        row for row in current if row["key"] == "discharge_summary"
    )["claim_scope_keys"] == [target]
    _reset_doc_types(broker)


def test_custom_config_drives_classification_and_completeness(broker: TestClient):
    from app.services.claim_doc_types import (
        classify_document,
        missing_key_fields,
        resolve_doc_types,
    )

    rows = broker.get("/api/v1/claim-doc-types").json()
    discharge = next(r for r in rows if r["key"] == "discharge_summary")
    # Broker teaches the system a new title + a custom key field.
    broker.put(f"/api/v1/claim-doc-types/{discharge['id']}", json={
        "display": discharge["display"],
        "aliases": discharge["aliases"] + ["operative note"],
        "key_fields": discharge["key_fields"]
        + [{"name": "Surgeon", "keywords": ["surgeon", "operating doctor"]}],
        "sector": discharge["sector"],
        "slot_key": discharge["slot_key"],
        "expected_updated_at": discharge["updated_at"],
    })
    try:
        with SessionLocal() as s:
            defs = resolve_doc_types(s, DEMO_CLIENT_ID)
        fields = [{"label": "Diagnosis", "value": "x"},
                  {"label": "Procedure", "value": "y"}]
        defn = classify_document("Operative Note", fields, definitions=defs)
        assert defn is not None and defn.key == "discharge_summary"
        # The custom key field participates in the completeness check.
        assert missing_key_fields(defn, fields) == ["Surgeon"]
        fields.append({"label": "Surgeon", "value": "Dr Lee"})
        assert missing_key_fields(defn, fields) == []
    finally:
        _reset_doc_types(broker)


def test_doc_type_slug_collision_creates_distinct_types(broker: TestClient):
    _reset_doc_types(broker)
    a = broker.post("/api/v1/claim-doc-types", json={
        "display": "Referral Memo", "aliases": [], "key_fields": [],
    })
    assert a.status_code == 201
    # Distinct display that slugifies identically must still be creatable
    # (unique key derived by suffixing), NOT a false 409.
    b = broker.post("/api/v1/claim-doc-types", json={
        "display": "Referral-Memo", "aliases": [], "key_fields": [],
    })
    assert b.status_code == 201
    assert a.json()["key"] != b.json()["key"]
    # A true duplicate DISPLAY (case-insensitive) still 409s.
    dup = broker.post("/api/v1/claim-doc-types", json={
        "display": "referral memo", "aliases": [], "key_fields": [],
    })
    assert dup.status_code == 409
    _reset_doc_types(broker)


def test_doc_type_optional_key_field_round_trips(broker: TestClient):
    rows = _reset_doc_types(broker)
    discharge = next(r for r in rows if r["key"] == "discharge_summary")
    surgery = next(f for f in discharge["key_fields"] if f["name"] == "Surgery")
    # The seeded Surgery field is optional and survives an edit round-trip.
    assert surgery["optional"] is True
    res = broker.put(f"/api/v1/claim-doc-types/{discharge['id']}", json={
        "display": discharge["display"],
        "aliases": discharge["aliases"],
        "key_fields": discharge["key_fields"],
        "sector": discharge["sector"],
        "slot_key": discharge["slot_key"],
        "expected_updated_at": discharge["updated_at"],
    })
    assert res.status_code == 200
    surgery2 = next(f for f in res.json()["key_fields"] if f["name"] == "Surgery")
    assert surgery2["optional"] is True


def test_recover_stranded_reviews_reverts_interrupted_run() -> None:
    """A claim stuck in ai_review_pending with a still-pending review (its
    background task died mid-flight) is reverted to submitted; a claim whose
    latest review already completed is left untouched."""
    from app.models.claim_ai_review import (
        REVIEW_STATUS_COMPLETE,
        REVIEW_STATUS_ERROR,
    )
    from app.services.claims_review.recovery import recover_stranded_reviews

    stranded_id, stranded_review_id = _mk_claim(status=CLAIM_STATUS_AI_REVIEW_PENDING)

    # A claim in ai_review_pending whose latest review already completed is a
    # different inconsistency the sweep must NOT touch.
    complete_id, complete_review_id = _mk_claim(status=CLAIM_STATUS_AI_REVIEW_PENDING)
    with SessionLocal() as s:
        rev = s.get(ClaimAIReview, complete_review_id)
        rev.status = REVIEW_STATUS_COMPLETE
        s.commit()

    recovered = recover_stranded_reviews()
    assert recovered >= 1

    with SessionLocal() as s:
        stranded = s.get(Claim, stranded_id)
        assert stranded.status == CLAIM_STATUS_SUBMITTED
        assert s.get(ClaimAIReview, stranded_review_id).status == REVIEW_STATUS_ERROR

        # The completed-review claim is left as-is.
        assert s.get(Claim, complete_id).status == CLAIM_STATUS_AI_REVIEW_PENDING
        assert s.get(ClaimAIReview, complete_review_id).status == REVIEW_STATUS_COMPLETE

    # Idempotent: a second sweep finds nothing new to revert (the first run
    # already moved every stranded claim to submitted).
    assert recover_stranded_reviews() == 0


# ── Per-claim-type review rule setup (claim_review_configs) ───────────────────


def _mk_review_config(**over):
    """Create a ClaimReviewConfig row for DEMO_CLIENT_ID. Returns its id.
    Callers MUST delete it (``_drop_review_configs``) — the module DB is shared
    and a leftover row would re-configure every later GHS claim's review."""
    from app.models import ClaimReviewConfig

    data = {
        "client_id": DEMO_CLIENT_ID,
        "claim_kind": "insured",
        "claim_key": "GHS",
        "display_label": "Hospitalisation rules",
        "enabled": True,
        "field_maps": [
            {
                "portal_field": "amount_claimed",
                "document_field": "Total Amount",
                "mode": "numeric",
                "tolerance": 0.01,
                "verify_with_vision": True,
            }
        ],
        "ai_rules": [],
        "required_documents": None,
    }
    data.update(over)
    with SessionLocal() as s:
        row = ClaimReviewConfig(**data)
        s.add(row)
        s.commit()
        return row.id


def _drop_review_configs():
    from app.models import ClaimReviewConfig

    with SessionLocal() as s:
        s.query(ClaimReviewConfig).delete()
        s.commit()


def test_resolve_review_config_defaults_and_matching():
    """No row → the in-code defaults; an enabled row on the claim's product
    wins; a disabled row is ignored; flex categories match case-insensitively."""
    from app.services.claim_review_configs import resolve_review_config

    insured_claim = Claim(
        client_id=DEMO_CLIENT_ID, claim_kind="insured", product_code="GHS"
    )
    flex_claim = Claim(
        client_id=DEMO_CLIENT_ID, claim_kind="flex", flex_category_name="Gym Membership"
    )
    try:
        with SessionLocal() as s:
            cfg = resolve_review_config(s, insured_claim)
            assert cfg.config_id is None
            assert cfg.vision_fields == {
                "amount_claimed",
                "incurred_date",
                "admission_date",
                "discharge_date",
                "provider_name",
            }
            assert all(r.severity == "critical" for r in cfg.ai_rules)

        config_id = _mk_review_config()
        flex_id = _mk_review_config(
            claim_kind="flex", claim_key="gym  membership",
            display_label="Gym rules",
        )
        with SessionLocal() as s:
            cfg = resolve_review_config(s, insured_claim)
            assert cfg.config_id == config_id
            assert cfg.config_label == "Hospitalisation rules"
            # The row's single field map drives the vision set.
            assert cfg.vision_fields == {"amount_claimed"}
            # Flex category name matches normalized/casefolded.
            assert resolve_review_config(s, flex_claim).config_id == flex_id

        with SessionLocal() as s:
            from app.models import ClaimReviewConfig

            s.get(ClaimReviewConfig, config_id).enabled = False
            s.commit()
        with SessionLocal() as s:
            assert resolve_review_config(s, insured_claim).config_id is None
    finally:
        _drop_review_configs()


def test_exact_subclaim_review_config_overrides_product_default():
    """A subtype override wins only for that scope; sibling claim choices keep
    inheriting the existing product-level setup."""
    from app.services.claim_intake import (
        GHS_SUB_TYPES,
        SCOPE_GHS_HOSPITALISATION,
    )
    from app.services.claim_review_configs import resolve_review_config

    hospital_claim = Claim(
        client_id=DEMO_CLIENT_ID,
        claim_kind="insured",
        product_code="GHS",
        sub_type=GHS_SUB_TYPES[1],
    )
    emergency_claim = Claim(
        client_id=DEMO_CLIENT_ID,
        claim_kind="insured",
        product_code="GHS",
        sub_type=GHS_SUB_TYPES[2],
    )
    try:
        product_id = _mk_review_config(display_label="GHS default")
        hospital_id = _mk_review_config(
            scope_code=SCOPE_GHS_HOSPITALISATION,
            display_label="Hospital stay override",
        )
        with SessionLocal() as s:
            assert resolve_review_config(s, hospital_claim).config_id == hospital_id
            assert resolve_review_config(s, emergency_claim).config_id == product_id
            from app.models import ClaimReviewConfig

            s.get(ClaimReviewConfig, hospital_id).enabled = False
            s.commit()
        with SessionLocal() as s:
            # Switching off a child override restores inheritance; it must not
            # skip the product setup and jump straight to global defaults.
            assert resolve_review_config(s, hospital_claim).config_id == product_id
    finally:
        _drop_review_configs()


def test_hospital_sector_review_config_inherits_hospitalisation_then_product():
    """Government and private stays can diverge without orphaning the existing
    hospitalisation setup. Unlisted hospitals use the stricter private branch,
    while an assessor's stored sector override remains authoritative."""
    from app.models.claim import HOSPITAL_TYPE_GOVERNMENT
    from app.services.claim_intake import (
        GHS_SUB_TYPES,
        SCOPE_GHS_HOSPITALISATION,
        SCOPE_GHS_HOSPITALISATION_GOVT,
        SCOPE_GHS_HOSPITALISATION_PRIVATE,
    )
    from app.services.claim_review_configs import resolve_review_config

    def stay(provider: str, *, hospital_type: str | None = None) -> Claim:
        return Claim(
            client_id=DEMO_CLIENT_ID,
            claim_kind="insured",
            product_code="GHS",
            sub_type=GHS_SUB_TYPES[1],
            provider_name=provider,
            hospital_type=hospital_type,
        )

    try:
        product_id = _mk_review_config(display_label="GHS default")
        hospital_id = _mk_review_config(
            scope_code=SCOPE_GHS_HOSPITALISATION,
            display_label="All hospital stays",
        )
        govt_id = _mk_review_config(
            scope_code=SCOPE_GHS_HOSPITALISATION_GOVT,
            display_label="Government stays",
        )
        private_id = _mk_review_config(
            scope_code=SCOPE_GHS_HOSPITALISATION_PRIVATE,
            display_label="Private stays",
        )
        with SessionLocal() as s:
            assert resolve_review_config(
                s, stay("Singapore General Hospital")
            ).config_id == govt_id
            assert resolve_review_config(
                s, stay("Raffles Hospital")
            ).config_id == private_id
            assert resolve_review_config(
                s, stay("Overseas Hospital")
            ).config_id == private_id
            assert resolve_review_config(
                s,
                stay(
                    "Raffles Hospital",
                    hospital_type=HOSPITAL_TYPE_GOVERNMENT,
                ),
            ).config_id == govt_id

            from app.models import ClaimReviewConfig

            s.get(ClaimReviewConfig, govt_id).enabled = False
            s.commit()
        with SessionLocal() as s:
            # A disabled sector override first falls back to the existing
            # hospitalisation setup, not directly to the product default.
            assert resolve_review_config(
                s, stay("Singapore General Hospital")
            ).config_id == hospital_id
            from app.models import ClaimReviewConfig

            s.get(ClaimReviewConfig, hospital_id).enabled = False
            s.commit()
        with SessionLocal() as s:
            assert resolve_review_config(
                s, stay("Singapore General Hospital")
            ).config_id == product_id
    finally:
        _drop_review_configs()


def test_every_ghs_subclaim_resolves_to_a_sector_specific_review_leaf():
    from app.services.claim_intake import (
        GHS_SUB_TYPES,
        generic_scope_code,
        scope_code_for_sub_type,
    )
    from app.services.claim_review_configs import claim_scope_for

    for sub_type in GHS_SUB_TYPES:
        govt = Claim(
            client_id=DEMO_CLIENT_ID,
            claim_kind="insured",
            product_code="GHS",
            sub_type=sub_type,
            provider_name="Singapore General Hospital",
        )
        private = Claim(
            client_id=DEMO_CLIENT_ID,
            claim_kind="insured",
            product_code="GHS",
            sub_type=sub_type,
            provider_name="Raffles Hospital",
        )
        govt_scope = claim_scope_for(govt)[2]
        private_scope = claim_scope_for(private)[2]

        assert govt_scope.endswith("_govt")
        assert private_scope.endswith("_private")
        assert generic_scope_code(govt_scope) == scope_code_for_sub_type(sub_type)
        assert generic_scope_code(private_scope) == scope_code_for_sub_type(sub_type)


def test_every_ghs_sector_leaf_inherits_its_legacy_subclaim_setup():
    from app.services.claim_intake import GHS_SUB_TYPES, scope_code_for_sub_type
    from app.services.claim_review_configs import resolve_review_config

    try:
        config_ids = {
            sub_type: _mk_review_config(
                scope_code=scope_code_for_sub_type(sub_type),
                display_label=f"Legacy {sub_type}",
            )
            for sub_type in GHS_SUB_TYPES
        }
        with SessionLocal() as session:
            for provider in ("Singapore General Hospital", "Raffles Hospital"):
                for sub_type in GHS_SUB_TYPES:
                    claim = Claim(
                        client_id=DEMO_CLIENT_ID,
                        claim_kind="insured",
                        product_code="GHS",
                        sub_type=sub_type,
                        provider_name=provider,
                    )
                    assert (
                        resolve_review_config(session, claim).config_id
                        == config_ids[sub_type]
                    )
    finally:
        _drop_review_configs()


def test_ghs_review_scopes_are_grouped_into_eight_configurable_leaves():
    from app.services.claim_intake import GHS_SUB_TYPES
    from app.services.claim_review_configs import review_scope_definitions

    scopes = review_scope_definitions("GHS", "Group Hospital & Surgical")
    compatibility = [scope for scope in scopes if not scope.configurable]
    leaves = [scope for scope in scopes if scope.configurable]

    assert {scope.code for scope in compatibility} == {
        "ghs_pre_post",
        "ghs_hospitalisation",
        "ghs_emergency_outpatient",
        "ghs_dialysis_cancer",
    }
    assert len(leaves) == 8
    assert [scope.group_label for scope in leaves[:4]] == [
        "Government hospital"
    ] * 4
    assert [scope.group_label for scope in leaves[4:]] == [
        "Private hospital"
    ] * 4
    assert {scope.label for scope in leaves[:4]} == set(GHS_SUB_TYPES)
    assert all(scope.parent_scope_code for scope in leaves)


def test_warning_severity_rule_fail_does_not_flag():
    """A failed [WARNING] rule surfaces as a warning result — the claim still
    verifies. A failed [CRITICAL] rule (and an unmatched failed rule) flags."""
    _mk_review_config(
        ai_rules=[
            {"id": "r1", "rule": "Outstanding balance should be zero.",
             "category": "amount", "severity": "warning"},
        ]
    )
    claim_id, review_id = _mk_claim(marker=b"warnsev")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result(
                       [_match("amount_claimed")],
                       rules=[{"rule": "[WARNING] Outstanding balance should be zero.",
                               "status": "fail",
                               "evidence": "Balance shows $12 outstanding."}],
                   )), \
             patch("app.services.ai_gateway.verify_claim_concern"):
            run_review(claim_id, review_id, None)

        claim, review = _load(claim_id, review_id)
        assert claim.status == CLAIM_STATUS_AI_VERIFIED
        assert review.verdict == "clean"
        entry = next(r for r in review.rule_results if r.get("rule_id") == "r1")
        assert entry["status"] == "warning"
        assert entry["severity"] == "warning"
        assert entry["category"] == "amount"
        # Provenance: the review records which setup drove it.
        assert review.review_config_id is not None
        assert review.review_config_label == "Hospitalisation rules"
    finally:
        _drop_review_configs()


def test_critical_and_unmatched_rule_failures_flag():
    _mk_review_config(
        ai_rules=[
            {"id": "r1", "rule": "Documents must be genuine.",
             "category": "authenticity", "severity": "critical"},
        ]
    )
    claim_id, review_id = _mk_claim(marker=b"critsev")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result(
                       [_match("amount_claimed")],
                       rules=[
                           {"rule": "[CRITICAL] Documents must be genuine.",
                            "status": "fail", "evidence": "Looks doctored."},
                           # The AI echoed text that matches NOTHING configured —
                           # must stay a fail (never silently downgraded).
                           {"rule": "Some drifted rule text.", "status": "fail",
                            "evidence": "…"},
                       ],
                   )), \
             patch("app.services.ai_gateway.verify_claim_concern"):
            run_review(claim_id, review_id, None)

        claim, review = _load(claim_id, review_id)
        assert claim.status == CLAIM_STATUS_AI_FLAGGED
        assert review.verdict == "flagged"
        drifted = next(
            r for r in review.rule_results if r.get("rule") == "Some drifted rule text."
        )
        assert drifted["status"] == "fail"
        assert drifted["severity"] == "critical"
    finally:
        _drop_review_configs()


def test_required_documents_config_adds_to_derived_families():
    """A configured required-documents list ADDS to the automatic slot/
    sub-type derivation — it never replaces it, so a per-claim-type list can't
    drop a sub-type-specific family or a guaranteed referral check."""
    _mk_review_config(required_documents=["itemised physiotherapy invoice"])
    claim_id, review_id = _mk_claim(marker=b"reqdocs")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result([_match("amount_claimed")])) as rv, \
             patch("app.services.ai_gateway.verify_claim_concern"):
            run_review(claim_id, review_id, None)
        sent = rv.call_args.kwargs["required_documents"]
        # The derived family for this claim survives...
        assert "receipt or tax invoice" in sent
        # ...and the broker's extra rides alongside it.
        assert "itemised physiotherapy invoice" in sent
        # The configured field maps + severity-tagged rules rode along too.
        assert rv.call_args.kwargs["field_maps"][0]["document_field"] == "Total Amount"
    finally:
        _drop_review_configs()


def test_required_documents_extra_is_deduped_against_derived():
    """Re-stating a family the derivation already produces must not duplicate
    it in the prompt."""
    _mk_review_config(required_documents=["Receipt or Tax Invoice"])
    claim_id, review_id = _mk_claim(marker=b"reqdocsdup")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result([_match("amount_claimed")])) as rv, \
             patch("app.services.ai_gateway.verify_claim_concern"):
            run_review(claim_id, review_id, None)
        sent = [d.lower() for d in rv.call_args.kwargs["required_documents"]]
        assert sent.count("receipt or tax invoice") == 1
    finally:
        _drop_review_configs()


def test_blank_echoed_rule_text_is_not_attributed():
    """An AI rule_result with a blank/missing `rule` must NOT be matched to a
    configured rule — "" is contained in every rule text, so a containment
    fallback would lend it the first rule's severity and a warning-severity
    match would downgrade an unattributable FAIL into a passing claim."""
    _mk_review_config(
        ai_rules=[
            {"id": "r1", "rule": "Outstanding balance should be zero.",
             "category": "amount", "severity": "warning"},
        ]
    )
    claim_id, review_id = _mk_claim(marker=b"blankrule")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result(
                       [_match("amount_claimed")],
                       rules=[{"rule": "", "status": "fail", "evidence": "?"}],
                   )), \
             patch("app.services.ai_gateway.verify_claim_concern"):
            run_review(claim_id, review_id, None)

        claim, review = _load(claim_id, review_id)
        entry = next(r for r in review.rule_results if r.get("source") == "ai")
        assert entry.get("rule_id") is None       # never attributed
        assert entry["severity"] == "critical"    # fail-safe default
        assert entry["status"] == "fail"          # NOT downgraded
        assert claim.status == CLAIM_STATUS_AI_FLAGGED
    finally:
        _drop_review_configs()


def test_severity_prefix_is_stripped_from_stored_rule_text():
    """`[CRITICAL]` etc. is prompt markup — the review row (and so the broker's
    rule panel and the flagged reasons) must carry the broker's own wording."""
    _mk_review_config(
        ai_rules=[
            {"id": "r1", "rule": "Documents must be genuine.",
             "category": "authenticity", "severity": "critical"},
        ]
    )
    claim_id, review_id = _mk_claim(marker=b"prefix")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result(
                       [_match("amount_claimed")],
                       rules=[
                           {"rule": "[CRITICAL] Documents must be genuine.",
                            "status": "fail", "evidence": "Looks doctored."},
                           {"rule": "[WARNING] Some unconfigured rule text.",
                            "status": "fail", "evidence": "…"},
                       ],
                   )), \
             patch("app.services.ai_gateway.verify_claim_concern"):
            run_review(claim_id, review_id, None)

        _, review = _load(claim_id, review_id)
        texts = [r.get("rule", "") for r in review.rule_results]
        assert "Documents must be genuine." in texts
        # Even an UNMATCHED rule gets the markup stripped.
        assert "Some unconfigured rule text." in texts
        assert not any(t.startswith("[") for t in texts)
        # …and the flagged reasons quote the clean text too.
        assert "[CRITICAL]" not in (review.summary or "")
    finally:
        _drop_review_configs()


def test_evidence_requirement_is_independent_of_vision_spend():
    """Turning OFF a vision re-check is a cost decision; it must not switch off
    the unsubstantiated-value guard. require_evidence keeps MISSING_IN_PDF
    flagging with zero vision calls."""
    _mk_review_config(
        field_maps=[{
            "portal_field": "amount_claimed",
            "document_field": "Total Amount",
            "mode": "numeric",
            "tolerance": 0.01,
            "verify_with_vision": False,
            "require_evidence": True,
        }]
    )
    claim_id, review_id = _mk_claim(marker=b"evidenceonly")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result([{
                       "field_name": "amount_claimed", "claim_value": "85.00",
                       "document_value": None, "status": "MISSING_IN_PDF",
                       "confidence": 0.9,
                   }])), \
             patch("app.services.ai_gateway.verify_claim_concern") as vf:
            run_review(claim_id, review_id, None)

        assert vf.call_count == 0  # no vision spend
        claim, review = _load(claim_id, review_id)
        assert claim.status == CLAIM_STATUS_AI_FLAGGED
        assert "Not substantiated" in (review.summary or "")
    finally:
        _drop_review_configs()


def test_legacy_field_map_without_require_evidence_mirrors_vision():
    """Rows written before the flags were split carry only
    `verify_with_vision` — they must keep their original behaviour."""
    from app.models import ClaimReviewConfig
    from app.services.claim_review_configs import config_from_row

    config_id = _mk_review_config(
        field_maps=[
            {"portal_field": "amount_claimed", "document_field": "Total",
             "mode": "numeric", "verify_with_vision": True},
            {"portal_field": "currency", "document_field": "Currency",
             "mode": "fuzzy", "verify_with_vision": False},
        ]
    )
    try:
        with SessionLocal() as s:
            cfg = config_from_row(s.get(ClaimReviewConfig, config_id))
        assert cfg.vision_fields == {"amount_claimed"}
        assert cfg.evidence_fields == {"amount_claimed"}
    finally:
        _drop_review_configs()


def test_review_config_stamped_on_deterministically_flagged_claim():
    """A stage-1 fail short-circuits before any AI spend — but the review row
    must still record WHICH setup was in force (NULL means 'the defaults')."""
    _mk_review_config()
    # Out-of-period incurred date → deterministic fail.
    claim_id, review_id = _mk_claim(incurred=date(2027, 1, 1), marker=b"detprov")
    try:
        with patch("app.services.ai_gateway.extract_claim_document") as ex, \
             patch("app.services.ai_gateway.review_claim") as rv:
            run_review(claim_id, review_id, None)
        assert ex.call_count == 0 and rv.call_count == 0

        claim, review = _load(claim_id, review_id)
        assert claim.status == CLAIM_STATUS_AI_FLAGGED
        assert review.review_config_label == "Hospitalisation rules"
        assert review.review_config_id is not None
    finally:
        _drop_review_configs()


def test_vision_gating_follows_config():
    """verify_with_vision=False on the amount map → no vision spend, and a
    MISSING_IN_PDF on that field no longer blocks verification."""
    _mk_review_config(
        field_maps=[{
            "portal_field": "amount_claimed",
            "document_field": "Total Amount",
            "mode": "numeric",
            "tolerance": 0.01,
            "verify_with_vision": False,
        }]
    )
    claim_id, review_id = _mk_claim(marker=b"novision")
    try:
        with patch("app.services.ai_gateway.extract_claim_document",
                   return_value=_extract_result()), \
             patch("app.services.ai_gateway.review_claim",
                   return_value=_review_result([{
                       "field_name": "amount_claimed", "claim_value": "85.00",
                       "document_value": None, "status": "MISSING_IN_PDF",
                       "confidence": 0.9,
                   }])), \
             patch("app.services.ai_gateway.verify_claim_concern") as vf:
            run_review(claim_id, review_id, None)

        assert vf.call_count == 0
        claim, review = _load(claim_id, review_id)
        assert claim.status == CLAIM_STATUS_AI_VERIFIED
        assert review.verdict == "clean"
    finally:
        _drop_review_configs()


def test_review_config_crud_over_http(broker: TestClient):
    """CRUD + options + duplicate guard + preview, as the settings UI uses it."""
    try:
        assert broker.get("/api/v1/claim-review-configs").json() == []

        options = broker.get("/api/v1/claim-review-configs/options").json()
        defaults = options["default_config"]
        assert {m["portal_field"] for m in defaults["field_maps"]} >= {
            "amount_claimed", "incurred_date", "provider_name"
        }
        assert len(defaults["ai_rules"]) == 6
        body = {
            "claim_kind": "insured",
            "claim_key": "GHS",
            "display_label": "Hospitalisation rules",
            "field_maps": defaults["field_maps"],
            "ai_rules": [{"rule": "No third-party billing.",
                          "category": "amount", "severity": "warning"}],
            "required_documents": [],
        }
        created = broker.post("/api/v1/claim-review-configs", json=body)
        assert created.status_code == 201
        created_row = created.json()
        config_id = created_row["id"]
        # Rules get stable ids assigned on write.
        assert created.json()["ai_rules"][0]["id"]

        # The same product may carry one exact subtype override; an unknown
        # display-derived code is rejected so renaming labels cannot orphan it.
        exact = broker.post("/api/v1/claim-review-configs", json={
            **body,
            "scope_code": "ghs_hospitalisation",
            "display_label": "Hospital stay override",
        })
        assert exact.status_code == 201
        govt = broker.post("/api/v1/claim-review-configs", json={
            **body,
            "scope_code": "ghs_hospitalisation_govt",
            "display_label": "Government hospital override",
        })
        assert govt.status_code == 201
        invalid_scope = broker.post("/api/v1/claim-review-configs", json={
            **body,
            "scope_code": "Hospitalisation/Day Surgery",
        })
        assert invalid_scope.status_code == 422

        # Same claim type again (case-insensitive key) → 409.
        dup = broker.post(
            "/api/v1/claim-review-configs", json={**body, "claim_key": "ghs"}
        )
        assert dup.status_code == 409
        assert dup.json()["detail"]["code"] == "duplicate_claim_type"

        updated = broker.put(
            f"/api/v1/claim-review-configs/{config_id}",
            json={
                **body,
                "display_label": "GHS review rules",
                "enabled": False,
                "expected_updated_at": created_row["updated_at"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False
        stale = broker.put(
            f"/api/v1/claim-review-configs/{config_id}",
            json={**body, "expected_updated_at": created_row["updated_at"]},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "stale_configuration"

        preview = broker.post("/api/v1/claim-review-configs/preview", json=body)
        assert preview.status_code == 200
        prompt = preview.json()["prompt"]
        assert "[WARNING] No third-party billing." in prompt
        assert "<amount_claimed>" in prompt
        assert "derived automatically from the claim type" in prompt

        # Self-import is rejected.
        self_import = broker.post(
            "/api/v1/claim-review-configs/import",
            json={"source_client_id": DEMO_CLIENT_ID, "config_ids": [config_id]},
        )
        assert self_import.status_code == 422

        assert (
            broker.delete(
                f"/api/v1/claim-review-configs/{config_id}",
                params={"expected_updated_at": updated.json()["updated_at"]},
            ).status_code
            == 204
        )
        assert broker.delete(
            f"/api/v1/claim-review-configs/{exact.json()['id']}",
            params={"expected_updated_at": exact.json()["updated_at"]},
        ).status_code == 204
        assert broker.delete(
            f"/api/v1/claim-review-configs/{govt.json()['id']}",
            params={"expected_updated_at": govt.json()["updated_at"]},
        ).status_code == 204
        assert broker.get("/api/v1/claim-review-configs").json() == []
    finally:
        _drop_review_configs()


def test_blank_label_is_rejected_not_committed(broker: TestClient):
    """A whitespace-only label used to pass min_length, normalize to "" on
    write, and then make EVERY later list call 500 — the row was committed but
    unserializable. It must be refused at the boundary instead."""
    try:
        options = broker.get("/api/v1/claim-review-configs/options").json()
        body = {
            "claim_kind": "insured",
            "claim_key": "GHS",
            "display_label": "   ",
            "field_maps": options["default_config"]["field_maps"],
            "ai_rules": [],
            "required_documents": [],
        }
        assert broker.post("/api/v1/claim-review-configs", json=body).status_code == 422
        assert (
            broker.post(
                "/api/v1/claim-review-configs",
                json={**body, "display_label": "GHS", "claim_key": " "},
            ).status_code
            == 422
        )
        # Nothing was written, and the surface still reads.
        assert broker.get("/api/v1/claim-review-configs").status_code == 200
        assert broker.get("/api/v1/claim-review-configs").json() == []
    finally:
        _drop_review_configs()


def test_corrupt_row_stays_listable_and_deletable(broker: TestClient):
    """Reading must never fail: a hand-edited row that violates the write-side
    schema (over-long rule, too many required docs) still lists — otherwise the
    broker could neither see nor delete it."""
    config_id = _mk_review_config(
        ai_rules=[{"id": "r1", "rule": "x" * 5000, "category": "c" * 200,
                   "severity": "bogus"}],
        required_documents=[f"doc {i}" for i in range(40)],
    )
    try:
        res = broker.get("/api/v1/claim-review-configs")
        assert res.status_code == 200
        row = next(r for r in res.json() if r["id"] == config_id)
        assert len(row["ai_rules"][0]["rule"]) == 2000      # clamped
        assert row["ai_rules"][0]["severity"] == "critical"  # fail-safe
        assert len(row["required_documents"]) == 15          # clamped
        assert (
            broker.delete(
                f"/api/v1/claim-review-configs/{config_id}",
                params={"expected_updated_at": row["updated_at"]},
            ).status_code
            == 204
        )
    finally:
        _drop_review_configs()


def test_import_sources_lists_only_same_firm_companies(broker: TestClient):
    """The picker is server-authoritative: it must offer exactly the companies
    /import would accept (same broker firm, never the active one)."""
    from app.models import BrokerFirm, Client

    rival_firm = "00000000-0000-0000-0000-0000000000e0"
    rival_client = "00000000-0000-0000-0000-0000000000e1"
    with SessionLocal() as s:
        if s.get(BrokerFirm, rival_firm) is None:
            s.add(BrokerFirm(id=rival_firm, name="Rival Brokers"))
            s.add(Client(id=rival_client, name="Rival client",
                         broker_firm_id=rival_firm))
            s.commit()
    try:
        sources = broker.get("/api/v1/claim-review-configs/sources").json()
        ids = {c["id"] for c in sources}
        assert DEMO_CLIENT_ID not in ids   # never the active company
        assert rival_client not in ids     # never another firm
        assert all("configured_count" in c for c in sources)
    finally:
        with SessionLocal() as s:
            rc = s.get(Client, rival_client)
            if rc is not None:
                s.delete(rc)
            rf = s.get(BrokerFirm, rival_firm)
            if rf is not None:
                s.delete(rf)
            s.commit()


def test_options_scope_is_the_current_year_and_survives_a_bad_flex_scheme(
    broker: TestClient,
):
    """The claim-type vocabulary is read from the CURRENT benefit year alone.

    With no year flagged current the list is empty for a reason that has
    nothing to do with claim rules (the member portal is dark too), so the
    response says which case it is instead of leaving them indistinguishable.
    Also pins the two shapes the endpoint must tolerate: a `key` on every entry
    (the UI's join key — never re-derived client-side) and a `FlexScheme.scheme`
    that isn't an object (it is unvalidated JSON; raising there would take the
    whole AI-extraction tab down).
    """
    from app.models import FlexScheme

    with SessionLocal() as s:
        demoted = [
            y for y in s.query(PolicyYear).filter(
                PolicyYear.client_id == DEMO_CLIENT_ID,
                PolicyYear.status == PolicyYearStatus.active,
            )
        ]
        demoted_ids = [y.id for y in demoted]
        s.add(FlexScheme(policy_year_id=PY, status="draft", scheme=["not-an-object"]))
        s.commit()
    try:
        options = broker.get("/api/v1/claim-review-configs/options").json()
        assert options["has_current_year"] is True
        # Malformed scheme → no flex types, but a 200 with the insured ones.
        assert [t for t in options["claim_types"] if t["claim_kind"] == "flex"] == []
        for t in options["claim_types"]:
            assert t["key"] == f"{t['claim_kind']}:{t['claim_key'].casefold()}"
            if t["claim_kind"] == "insured":
                assert t["scopes"]
                assert all(scope["scope_code"] for scope in t["scopes"])
                assert all(scope["key"].startswith(t["key"] + ":")
                           for scope in t["scopes"])

        with SessionLocal() as s:
            for y in s.query(PolicyYear).filter(PolicyYear.id.in_(demoted_ids)):
                y.status = PolicyYearStatus.archived
            s.commit()

        options = broker.get("/api/v1/claim-review-configs/options").json()
        assert options["has_current_year"] is False
        assert options["claim_types"] == []
        # The defaults still come back — the editor prefills from them.
        assert options["default_config"]["field_maps"]
    finally:
        with SessionLocal() as s:
            for y in s.query(PolicyYear).filter(PolicyYear.id.in_(demoted_ids)):
                y.status = PolicyYearStatus.active
            s.query(FlexScheme).filter(FlexScheme.policy_year_id == PY).delete()
            s.commit()


# ── Duplicate detection: the INVOICE NUMBER, not the document hash ───────────


def _rule(claim_id: str, name: str) -> dict:
    from app.services.claims_review.rules import deterministic_rule_results

    with SessionLocal() as s:
        claim = s.get(Claim, claim_id)
        results = deterministic_rule_results(s, claim, _statement(s.get(Employee, EMP)))
    return next(r for r in results if name in r["rule"])


def _set_invoice(claim_id: str, number: str | None) -> None:
    with SessionLocal() as s:
        claim = s.get(Claim, claim_id)
        claim.invoice_number = number
        s.commit()


def test_duplicate_invoice_rule_fails_on_the_members_own_claim():
    first, _ = _mk_claim(status=CLAIM_STATUS_SUBMITTED, marker=b"dupe-a")
    second, _ = _mk_claim(marker=b"dupe-b")
    _set_invoice(first, "INV-777")
    _set_invoice(second, "inv 777")  # same bill, typed differently

    result = _rule(second, "invoice number")
    assert result["status"] == "fail"
    assert first in result["evidence"]


def test_the_same_document_on_two_invoices_is_not_a_duplicate():
    """The multi-invoice split shares one discharge summary across an episode's
    claims — the old SHA-256 rule failed every claim after the first."""
    shared = b"one-episode"
    first, _ = _mk_claim(status=CLAIM_STATUS_SUBMITTED, marker=shared)
    second, _ = _mk_claim(marker=shared)
    _set_invoice(first, "EP-1")
    _set_invoice(second, "EP-2")

    assert _rule(second, "invoice number")["status"] == "pass"


def test_another_members_identical_invoice_number_is_a_warning_not_a_failure():
    """Short receipt numbers collide across providers, so a stranger's matching
    number is for a broker to weigh — never an automatic flag on a member."""
    other_emp = new_uuid()
    with SessionLocal() as s:
        s.add(
            Employee(
                id=other_emp, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="CR-9", employee_name="Other", attribute_values={},
                derived_attribute_values={}, source="csv_import", status="active",
            )
        )
        s.commit()
    theirs, _ = _mk_claim(status=CLAIM_STATUS_SUBMITTED, marker=b"theirs")
    with SessionLocal() as s:
        s.get(Claim, theirs).employee_id = other_emp
        s.commit()
    _set_invoice(theirs, "0001")

    mine, _ = _mk_claim(marker=b"mine")
    _set_invoice(mine, "0001")

    result = _rule(mine, "invoice number")
    assert result["status"] == "warning"
    assert theirs in result["evidence"]


def test_a_claim_with_no_invoice_number_never_matches():
    first, _ = _mk_claim(status=CLAIM_STATUS_SUBMITTED, marker=b"blank-a")
    second, _ = _mk_claim(marker=b"blank-b")
    _set_invoice(first, None)
    _set_invoice(second, "  ")

    assert _rule(second, "invoice number")["status"] == "pass"
