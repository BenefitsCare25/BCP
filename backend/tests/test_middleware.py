"""Middleware regression tests — security headers, request-ID, CORS.

These prove the wiring in app/main.py adds the headers, propagates inbound
request IDs (with sanitisation), and emits them back to the caller.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_middleware.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402


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


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_security_headers_present(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    headers = {k.lower(): v for k, v in res.headers.items()}
    assert "content-security-policy" in headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "permissions-policy" in headers
    assert "referrer-policy" in headers


def test_request_id_round_trips_when_provided(client: TestClient) -> None:
    res = client.get("/health", headers={"X-Request-ID": "test-abc-123"})
    assert res.headers["x-request-id"] == "test-abc-123"


def test_request_id_generated_when_missing(client: TestClient) -> None:
    res = client.get("/health")
    rid = res.headers.get("x-request-id")
    assert rid is not None
    # Generated IDs are 32-char hex (uuid4 without dashes).
    assert len(rid) == 32
    assert all(c in "0123456789abcdef" for c in rid)


def test_malicious_request_id_rejected(client: TestClient) -> None:
    """Header-injection attempts (control chars, oversized values, etc.)
    are dropped and replaced with a fresh UUID."""
    res = client.get(
        "/health",
        headers={"X-Request-ID": "evil\r\nInjected-Header: yes"},
    )
    rid = res.headers["x-request-id"]
    # Must NOT echo the malicious string.
    assert "\r" not in rid
    assert "\n" not in rid
    assert ":" not in rid
    assert rid != "evil\r\nInjected-Header: yes"


def test_oversized_request_id_dropped(client: TestClient) -> None:
    too_long = "x" * 250
    res = client.get("/health", headers={"X-Request-ID": too_long})
    assert res.headers["x-request-id"] != too_long
    assert len(res.headers["x-request-id"]) <= 200


def test_readiness_pings_database(client: TestClient) -> None:
    res = client.get("/readiness")
    assert res.status_code == 200
    assert res.json() == {
        "status": "ready",
        "database": "ok",
        "redis": "not-required",
    }
