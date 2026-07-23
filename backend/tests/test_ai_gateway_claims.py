"""Claims AI gateway entries — cache keys, breaker semantics, spend rows."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

TEST_DB = Path(__file__).parent / "_test_ai_gateway_claims.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
# Force the gateway to think AI is configured during tests.
os.environ.setdefault("INSPRO_AI_PROVIDER", "vertex")
os.environ.setdefault("VERTEX_PROJECT", "test-project")

from sqlalchemy import select  # noqa: E402

from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import AISpendLog  # noqa: E402
from app.services import ai_breaker, ai_cache  # noqa: E402
from app.services.ai_extractor import AIParseError  # noqa: E402
from app.services.ai_gateway import (  # noqa: E402
    extract_claim_document,
    review_claim,
    verify_claim_concern,
)
from scripts.seed_demo import seed  # noqa: E402

BLOCKS = [
    {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": "eA=="},
    }
]
META = {"provider": "anthropic", "model": "claude-test", "input_tokens": 100, "output_tokens": 50}


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_singletons():
    ai_cache.reset_cache_for_tests()
    ai_breaker.reset_breaker_for_tests()


def _extract_payload():
    return (
        {
            "document_type": "receipt",
            "fields": [
                {"id": "field_1", "label": "Total Amount", "value": "85.00",
                 "field_type": "currency", "confidence": 0.95},
            ],
        },
        dict(META),
    )


def _spend_rows(db, operation: str) -> list[AISpendLog]:
    return list(
        db.execute(
            select(AISpendLog).where(
                AISpendLog.client_id == DEMO_CLIENT_ID,
                AISpendLog.operation == operation,
            )
        ).scalars()
    )


def test_extract_caches_on_document_hash() -> None:
    """Same sha256 → cache hit regardless of blocks/file name (resubmitted
    receipt never re-extracts)."""
    db = SessionLocal()
    try:
        with patch(
            "app.services.ai_gateway.extract_claim_document_via_ai",
            return_value=_extract_payload(),
        ) as m:
            r1 = extract_claim_document(
                db, client_id=DEMO_CLIENT_ID, policy_year_id=None,
                sha256="a" * 64, blocks=BLOCKS, file_name="receipt.pdf",
            )
            r2 = extract_claim_document(
                db, client_id=DEMO_CLIENT_ID, policy_year_id=None,
                sha256="a" * 64, blocks=[], file_name="renamed.pdf",
            )
            r3 = extract_claim_document(
                db, client_id=DEMO_CLIENT_ID, policy_year_id=None,
                sha256="b" * 64, blocks=BLOCKS, file_name="receipt.pdf",
            )
        db.commit()
        assert m.call_count == 2  # a-hash live, a-hash cached, b-hash live
        assert (r1.cache_hit, r2.cache_hit, r3.cache_hit) == (False, True, False)
        assert r2.document["document_type"] == "receipt"
        rows = _spend_rows(db, "ai_claim_extract")
        assert len(rows) == 3
        assert sum(1 for r in rows if r.cache_hit) == 1
    finally:
        db.close()


def test_review_spend_row_and_cache() -> None:
    db = SessionLocal()
    try:
        review_payload = (
            {
                "field_comparisons": [
                    {"field_name": "amount_claimed", "status": "MATCH", "confidence": 0.99}
                ],
                "rule_results": [],
                "required_documents_check": [],
                "summary": "Consistent.",
                "confidence": 0.9,
            },
            dict(META),
        )
        kwargs = dict(
            client_id=DEMO_CLIENT_ID, policy_year_id=None,
            claim_fields={"amount_claimed": 85.0},
            documents=[{"file_name": "r.pdf", "document_type": "receipt", "fields": []}],
            field_maps=[
                {"portal_field": "amount_claimed", "document_field": "Total",
                 "mode": "numeric", "tolerance": 0.01}
            ],
            ai_rules=["No third-party payer."],
            required_documents=["receipt or tax invoice"],
        )
        with patch(
            "app.services.ai_gateway.review_claim_via_ai", return_value=review_payload
        ) as m:
            r1 = review_claim(db, **kwargs)
            r2 = review_claim(db, **kwargs)
            kwargs["claim_fields"] = {"amount_claimed": 90.0}
            r3 = review_claim(db, **kwargs)
        db.commit()
        assert m.call_count == 2
        assert (r1.cache_hit, r2.cache_hit, r3.cache_hit) == (False, True, False)
        rows = _spend_rows(db, "ai_claim_review")
        assert len(rows) == 3
        live = [r for r in rows if not r.cache_hit]
        assert all(r.input_tokens == 100 and r.output_tokens == 50 for r in live)
    finally:
        db.close()


def test_verify_cache_key_includes_claim_id() -> None:
    db = SessionLocal()
    try:
        with patch(
            "app.services.ai_gateway.verify_claim_concern_via_ai",
            return_value=({"verdict": "CONFIRMED", "explanation": "seen"}, dict(META)),
        ) as m:
            kwargs = dict(
                client_id=DEMO_CLIENT_ID, policy_year_id=None,
                question="Is the amount 85.00 shown?",
                doc_sha256="c" * 64, blocks=BLOCKS,
            )
            r1 = verify_claim_concern(db, claim_id="claim-1", **kwargs)
            r2 = verify_claim_concern(db, claim_id="claim-1", **kwargs)
            r3 = verify_claim_concern(db, claim_id="claim-2", **kwargs)
        db.commit()
        assert m.call_count == 2  # same claim cached; other claim is a fresh call
        assert (r1.cache_hit, r2.cache_hit, r3.cache_hit) == (False, True, False)
        assert r1.verdict == "CONFIRMED"
        assert len(_spend_rows(db, "ai_claim_vision_verify")) == 3
    finally:
        db.close()


def test_parse_error_does_not_trip_breaker() -> None:
    db = SessionLocal()
    try:
        breaker = ai_breaker.get_breaker()
        with patch(
            "app.services.ai_gateway.extract_claim_document_via_ai",
            side_effect=AIParseError("bad tool payload"),
        ):
            for i in range(breaker.threshold + 2):
                with pytest.raises(AIParseError):
                    extract_claim_document(
                        db, client_id=DEMO_CLIENT_ID, policy_year_id=None,
                        sha256=f"{i:064d}", blocks=BLOCKS, file_name="r.pdf",
                    )
        assert breaker.state == "closed"
    finally:
        db.close()


def test_provider_failure_trips_breaker() -> None:
    db = SessionLocal()
    try:
        breaker = ai_breaker.get_breaker()
        with patch(
            "app.services.ai_gateway.extract_claim_document_via_ai",
            side_effect=RuntimeError("provider down"),
        ):
            for i in range(breaker.threshold):
                with pytest.raises(RuntimeError):
                    extract_claim_document(
                        db, client_id=DEMO_CLIENT_ID, policy_year_id=None,
                        sha256=f"{i:063d}f", blocks=BLOCKS, file_name="r.pdf",
                    )
        assert breaker.state == "open"
    finally:
        db.close()
