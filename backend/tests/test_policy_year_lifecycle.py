"""Policy (benefit) year lifecycle — create, update, set-current, copy, delete.

Activation locking was removed: configuration is editable on every year and one
year is flagged "current" (``status == active``, what the portal reads).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_py_lifecycle.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Category, PolicyYear  # noqa: E402
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

API = "/api/v1/policy-years"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    # The suite shares ONE SQLite DB (the engine binds to the first-imported
    # module's path). Only that binder module's teardown deletes the file, so a
    # non-binder module must clean up its own rows or it pollutes later modules'
    # policy-year counts. Every year this module creates uses year >= 2030.
    with SessionLocal() as s:
        s.query(PolicyYear).filter(PolicyYear.year >= 2030).delete(
            synchronize_session=False
        )
        s.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _first_year_id(client: TestClient) -> str:
    return client.get(API).json()[0]["id"]


def test_create_with_grace_period(client: TestClient) -> None:
    res = client.post(
        API,
        json={
            "start_date": "2030-01-01",
            "end_date": "2030-12-31",
            "claim_grace_period_days": 45,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["claim_grace_period_days"] == 45
    assert body["status"] == PolicyYearStatus.draft.value


def test_patch_grace_only_keeps_dates(client: TestClient) -> None:
    created = client.post(
        API, json={"start_date": "2031-01-01", "end_date": "2031-12-31"}
    ).json()
    res = client.patch(f"{API}/{created['id']}", json={"claim_grace_period_days": 30})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["claim_grace_period_days"] == 30
    assert body["start_date"] == "2031-01-01"
    assert body["end_date"] == "2031-12-31"


def test_set_current_demotes_previous(client: TestClient) -> None:
    a = client.post(
        API, json={"start_date": "2032-01-01", "end_date": "2032-12-31"}
    ).json()
    b = client.post(
        API, json={"start_date": "2033-01-01", "end_date": "2033-12-31"}
    ).json()

    res_a = client.post(f"{API}/{a['id']}/set-current")
    assert res_a.status_code == 200
    assert res_a.json()["status"] == PolicyYearStatus.active.value

    res_b = client.post(f"{API}/{b['id']}/set-current")
    assert res_b.status_code == 200
    assert res_b.json()["status"] == PolicyYearStatus.active.value

    # Only one current year per client: a was demoted to archived.
    years = {y["id"]: y["status"] for y in client.get(API).json()}
    assert years[b["id"]] == PolicyYearStatus.active.value
    assert years[a["id"]] == PolicyYearStatus.archived.value


def test_cannot_delete_current_year(client: TestClient) -> None:
    y = client.post(
        API, json={"start_date": "2034-01-01", "end_date": "2034-12-31"}
    ).json()
    client.post(f"{API}/{y['id']}/set-current")
    res = client.delete(f"{API}/{y['id']}")
    assert res.status_code == 409
    assert "current benefit year" in res.json()["detail"]


def test_delete_draft_year(client: TestClient) -> None:
    y = client.post(
        API, json={"start_date": "2035-01-01", "end_date": "2035-12-31"}
    ).json()
    assert client.delete(f"{API}/{y['id']}").status_code == 204
    assert client.get(f"{API}/{y['id']}").status_code == 404


def test_delete_year_with_config_cascades(client: TestClient) -> None:
    """Deleting a year with categories must cascade-delete them, not NULL the
    FK (regression: the ORM tried to null categories.policy_year_id → 500)."""
    y = client.post(
        API, json={"start_date": "2036-01-01", "end_date": "2036-12-31"}
    ).json()
    db = SessionLocal()
    try:
        for i in range(3):
            db.add(
                Category(
                    policy_year_id=y["id"],
                    display_name=f"Del cat {i}",
                    raw_description=f"Del cat {i}",
                    source=SourceKind.manual.value,
                    status=CategoryStatus.confirmed.value,
                )
            )
        db.commit()
    finally:
        db.close()

    assert client.delete(f"{API}/{y['id']}").status_code == 204
    assert client.get(f"{API}/{y['id']}").status_code == 404
    db = SessionLocal()
    try:
        assert (
            db.query(Category).filter(Category.policy_year_id == y["id"]).count()
            == 0
        )
    finally:
        db.close()


def test_patch_explicit_null_date_422(client: TestClient) -> None:
    """An explicit null date must 422, not 500 (date < None TypeError)."""
    y = client.post(
        API, json={"start_date": "2037-01-01", "end_date": "2037-12-31"}
    ).json()
    res = client.patch(f"{API}/{y['id']}", json={"start_date": None})
    assert res.status_code == 422, res.text


def test_overlap_rejected(client: TestClient) -> None:
    client.post(API, json={"start_date": "2040-01-01", "end_date": "2040-12-31"})
    res = client.post(
        API, json={"start_date": "2040-06-01", "end_date": "2041-05-31"}
    )
    assert res.status_code == 409


def test_copy_carries_both_deadlines_and_accepts_an_override_of_either(
    client: TestClient,
) -> None:
    """The grace period and the leaver run-off are the same kind of per-year
    setting, and copy has to treat them the same way.

    It honoured an override of the grace period and silently DROPPED one of
    `leaver_access_days` — pydantic ignores an unknown key — so a broker
    stating a new run-off on renewal got the old one with no error, on the
    setting that decides how long a leaver keeps the portal.
    """
    source = client.post(
        API,
        json={
            "start_date": "2043-01-01", "end_date": "2043-12-31",
            "claim_grace_period_days": 45, "leaver_access_days": 14,
        },
    ).json()

    carried = client.post(
        f"{API}/{source['id']}/copy",
        json={"start_date": "2044-01-01", "end_date": "2044-12-31"},
    )
    assert carried.status_code == 201, carried.text
    assert carried.json()["policy_year"]["claim_grace_period_days"] == 45
    assert carried.json()["policy_year"]["leaver_access_days"] == 14

    overridden = client.post(
        f"{API}/{source['id']}/copy",
        json={
            "start_date": "2045-01-01", "end_date": "2045-12-31",
            "claim_grace_period_days": 30, "leaver_access_days": 90,
        },
    )
    assert overridden.status_code == 201, overridden.text
    assert overridden.json()["policy_year"]["claim_grace_period_days"] == 30
    assert overridden.json()["policy_year"]["leaver_access_days"] == 90

    # 0 is a real value ("access ends on the last day"), not "unset".
    zeroed = client.post(
        f"{API}/{source['id']}/copy",
        json={
            "start_date": "2046-01-01", "end_date": "2046-12-31",
            "leaver_access_days": 0,
        },
    )
    assert zeroed.status_code == 201, zeroed.text
    assert zeroed.json()["policy_year"]["leaver_access_days"] == 0

    rejected = client.post(
        f"{API}/{source['id']}/copy",
        json={
            "start_date": "2047-01-01", "end_date": "2047-12-31",
            "leaver_access_days": -1,
        },
    )
    assert rejected.status_code == 422, rejected.text


def test_copy_clones_categories(client: TestClient) -> None:
    source = client.post(
        API, json={"start_date": "2041-01-01", "end_date": "2041-12-31"}
    ).json()
    # Seed a couple of categories on the source year directly.
    db = SessionLocal()
    try:
        for i in range(2):
            db.add(
                Category(
                    policy_year_id=source["id"],
                    display_name=f"Cat {i}",
                    raw_description=f"Cat {i} desc",
                    source=SourceKind.manual.value,
                    status=CategoryStatus.confirmed.value,
                    plan_assignments={"tier_labels": {"EO": "Employee only"}},
                )
            )
        db.commit()
    finally:
        db.close()

    res = client.post(
        f"{API}/{source['id']}/copy",
        json={"start_date": "2042-01-01", "end_date": "2042-12-31"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["copied"]["categories"] == 2
    new_id = body["policy_year"]["id"]

    db = SessionLocal()
    try:
        clones = (
            db.query(Category).filter(Category.policy_year_id == new_id).all()
        )
        assert len(clones) == 2
        assert {c.display_name for c in clones} == {"Cat 0", "Cat 1"}
        # plan_assignments copied verbatim.
        assert all(c.plan_assignments == {"tier_labels": {"EO": "Employee only"}} for c in clones)
    finally:
        db.close()
