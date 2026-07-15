"""Phase 9 activation flow — gates, snapshot, idempotency."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_activation.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import update  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Category, PolicyYear  # noqa: E402
from app.models.category import CategoryStatus  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "placement_slips"
    / "STMicroelectronics - Placement Slips 2026_workingfile (1).xls"
)


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


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _policy_year_id(client: TestClient) -> str:
    return client.get("/api/v1/policy-years").json()[0]["id"]


def _seed_categories(client: TestClient, py_id: str) -> None:
    if not FIXTURE.exists():
        pytest.skip(f"Fixture not present: {FIXTURE}")
    with FIXTURE.open("rb") as f:
        res = client.post(
            "/api/v1/placement-slips/parse",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
            data={"policy_year_id": py_id},
        )
    assert res.status_code == 200, res.text


def test_readiness_reports_unconfirmed(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    _seed_categories(client, py_id)
    res = client.get(f"/api/v1/policy-years/{py_id}/activation-readiness")
    assert res.status_code == 200
    body = res.json()
    assert body["total_categories"] > 0
    assert body["confirmed_categories"] < body["total_categories"]
    assert body["ready"] is False


def test_activate_fails_when_unconfirmed(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    res = client.post(f"/api/v1/policy-years/{py_id}/activate")
    assert res.status_code == 422
    assert "not yet confirmed" in res.json()["detail"]


def test_activate_succeeds_after_confirm_all(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    db = SessionLocal()
    try:
        db.execute(
            update(Category)
            .where(Category.policy_year_id == py_id)
            .values(status=CategoryStatus.confirmed.value)
        )
        db.commit()
    finally:
        db.close()

    res = client.post(f"/api/v1/policy-years/{py_id}/activate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == PolicyYearStatus.active.value
    assert body["snapshot_counts"]["categories"] > 0


def test_snapshot_endpoint_returns_blob(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    res = client.get(f"/api/v1/policy-years/{py_id}/snapshot")
    assert res.status_code == 200
    body = res.json()
    assert body["snapshot"]["version"] == "v1"
    assert "categories" in body["snapshot"]
    assert "counts" in body["snapshot"]


def test_activate_twice_returns_conflict(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    res = client.post(f"/api/v1/policy-years/{py_id}/activate")
    assert res.status_code == 409


def test_snapshot_missing_for_draft_year(client: TestClient) -> None:
    # Create a fresh draft policy year and confirm snapshot 404s.
    from datetime import date

    db = SessionLocal()
    try:
        client_row = db.query(PolicyYear).first()
        new_py = PolicyYear(
            client_id=client_row.client_id,
            year=2027,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
        )
        db.add(new_py)
        db.commit()
        new_id = new_py.id
    finally:
        db.close()
    res = client.get(f"/api/v1/policy-years/{new_id}/snapshot")
    assert res.status_code == 404


def test_activate_fails_when_no_categories(client: TestClient) -> None:
    from datetime import date

    db = SessionLocal()
    try:
        client_row = db.query(PolicyYear).first()
        new_py = PolicyYear(
            client_id=client_row.client_id,
            year=2028,
            start_date=date(2028, 1, 1),
            end_date=date(2028, 12, 31),
        )
        db.add(new_py)
        db.commit()
        new_id = new_py.id
    finally:
        db.close()

    res = client.post(f"/api/v1/policy-years/{new_id}/activate")
    assert res.status_code == 422
    assert "no categories" in res.json()["detail"]
