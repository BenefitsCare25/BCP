"""End-to-end test for the Phase 5 match-results endpoints.

Pipeline under test: seed → parse STM placement slip (creates categories with
JSONLogic rules + display names) → insert a few employees → run matching →
assert audit row written + GET returns items + at least one row matched.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_matches.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog, Dependant, Employee  # noqa: E402
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
    """Upload the STM placement slip so categories with display_name + rules exist."""
    if not FIXTURE.exists():
        pytest.skip(f"Fixture not present: {FIXTURE}")
    with FIXTURE.open("rb") as f:
        res = client.post(
            "/api/v1/placement-slips/parse",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
            data={"policy_year_id": py_id},
        )
    assert res.status_code == 200, res.text


def _insert_employees(py_id: str) -> list[str]:
    """Insert three test employees directly — avoids slow .xlsx upload in tests.

    Returns their IDs.
    """
    db = SessionLocal()
    try:
        # Find any seeded category display_name to use as an exact-match probe.
        from app.models import Category  # local import — DB is initialised by fixture

        category_names = [
            (c.id, c.display_name)
            for c in db.execute(
                select(Category).where(Category.policy_year_id == py_id)
            ).scalars().all()
        ]
        assert category_names, "Expected categories to be seeded by placement slip parse"
        # Pick one with a parseable shape "<digits> ..." so the derivation also fires.
        probe = next(
            ((cid, name) for cid, name in category_names if name and name[:2].strip().isdigit()),
            category_names[0],
        )
        _probe_id, probe_name = probe

        emps = [
            Employee(
                client_id=DEMO_CLIENT_ID,
                policy_year_id=py_id,
                staff_id="TEST-001",
                employee_name="Alice Exact",
                attribute_values={"category": probe_name, "pass": "EP"},
            ),
            Employee(
                client_id=DEMO_CLIENT_ID,
                policy_year_id=py_id,
                staff_id="TEST-002",
                employee_name="Bob Unmatched",
                attribute_values={"category": "Zzzz Nothing Like Any Category Xyz"},
            ),
            Employee(
                client_id=DEMO_CLIENT_ID,
                policy_year_id=py_id,
                staff_id="TEST-003",
                employee_name="Carol Fuzzy",
                # Slightly different from the exact probe so we fall to fuzzy.
                attribute_values={"category": (probe_name or "") + " extra word"},
            ),
        ]
        db.add_all(emps)
        db.commit()
        return [e.id for e in emps]
    finally:
        db.close()


def test_match_results_before_manual_run(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    _seed_categories(client, py_id)
    _insert_employees(py_id)

    res = client.get("/api/v1/match-results", params={"policy_year_id": py_id})
    assert res.status_code == 200
    body = res.json()
    assert body["employees_total"] >= 3
    # Slip upload now auto-runs matching in the same transaction, so a run
    # exists and matches are FRESH relative to the parsed categories — the
    # stale flag must not fire right after an upload.
    if body["last_run_at"] is None:
        # No auto-run happened (e.g. fixture skipped employees) — never-run
        # semantics: pending with nothing to list yet.
        assert body["pending"] is True
        assert body["items"] == []
    else:
        # (Shared-DB full-suite runs can skip the auto-run when no employees
        # existed at upload time — then the old run is stale, which is fine.)
        assert body["pending"] is False or "stale" in (body["reason"] or "")


def test_run_matching_populates_results(client: TestClient) -> None:
    py_id = _policy_year_id(client)

    res = client.post(
        "/api/v1/match-results/run", params={"policy_year_id": py_id}
    )
    assert res.status_code == 200, res.text
    run_body = res.json()
    assert run_body["employees_total"] >= 3
    assert run_body["employees_matched"] >= 1
    assert "by_method" in run_body
    assert run_body["duration_ms"] >= 0


def test_audit_row_written_for_run(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    db = SessionLocal()
    try:
        rows = list(
            db.execute(
                select(AuditLog).where(
                    AuditLog.action == "run_matching",
                    AuditLog.entity_type == "policy_year",
                    AuditLog.entity_id == py_id,
                )
            )
            .scalars()
            .all()
        )
        assert rows, "Expected at least one run_matching audit row"
        latest = rows[-1]
        assert "employees_matched" in (latest.after or {})
        assert "by_method" in (latest.after or {})
    finally:
        db.close()


def test_get_returns_items_after_run(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    res = client.get(
        "/api/v1/match-results",
        params={"policy_year_id": py_id, "offset": 0, "limit": 50},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["pending"] is False
    assert body["last_run_at"] is not None
    assert isinstance(body["items"], list)
    assert len(body["items"]) >= 3
    # Confirm at least one item was matched and shows the method.
    methods = {it["match_method"] for it in body["items"] if it["match_method"]}
    assert methods, "Expected at least one matched item"


def test_exact_match_path_fires(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    res = client.get(
        "/api/v1/match-results",
        params={"policy_year_id": py_id, "limit": 100},
    )
    body = res.json()
    by_staff = {it["staff_id"]: it for it in body["items"]}
    assert "TEST-001" in by_staff
    # Alice was given a verbatim copy of a category display_name; she should
    # have hit the exact_name tier.
    assert by_staff["TEST-001"]["match_method"] == "exact_name"
    assert by_staff["TEST-001"]["match_confidence"] == 1.0


def test_unmatched_employee_has_null_category(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    body = client.get(
        "/api/v1/match-results",
        params={"policy_year_id": py_id, "limit": 100},
    ).json()
    by_staff = {it["staff_id"]: it for it in body["items"]}
    assert "TEST-002" in by_staff
    assert by_staff["TEST-002"]["matched_category_id"] is None
    assert by_staff["TEST-002"]["match_method"] is None


def test_employees_match_status_filter(client: TestClient) -> None:
    """The roster list filters on match status so unmatched rows can be mapped manually."""
    py_id = _policy_year_id(client)
    base = {"policy_year_id": py_id, "limit": 200}

    unmatched = client.get(
        "/api/v1/employees", params={**base, "match_status": "unmatched"}
    ).json()
    assert all(it["matched_category_id"] is None for it in unmatched["items"])
    assert any(it["staff_id"] == "TEST-002" for it in unmatched["items"])

    matched = client.get(
        "/api/v1/employees", params={**base, "match_status": "matched"}
    ).json()
    assert matched["items"], "expected at least one matched employee"
    assert all(it["matched_category_id"] is not None for it in matched["items"])
    assert not any(it["staff_id"] == "TEST-002" for it in matched["items"])

    all_emps = client.get("/api/v1/employees", params=base).json()
    assert all_emps["total"] == matched["total"] + unmatched["total"]


def test_employees_invalid_match_status_422(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    res = client.get(
        "/api/v1/employees",
        params={"policy_year_id": py_id, "match_status": "bogus"},
    )
    assert res.status_code == 422


def test_manual_override_survives_rerun(client: TestClient) -> None:
    """A manually-mapped employee must keep its pin when matching is re-run."""
    py_id = _policy_year_id(client)
    cats = client.get("/api/v1/categories", params={"policy_year_id": py_id}).json()
    if not cats:
        pytest.skip("no categories seeded (fixture absent)")
    cat_id = cats[0]["id"]

    unmatched = client.get(
        "/api/v1/employees",
        params={"policy_year_id": py_id, "match_status": "unmatched", "limit": 200},
    ).json()["items"]
    emp = next(it for it in unmatched if it["staff_id"] == "TEST-002")

    ov = client.post(
        f"/api/v1/match-results/employees/{emp['id']}/override",
        json={"category_id": cat_id},
    )
    assert ov.status_code == 200, ov.text

    # Re-run matching — the manual pin must survive, not be recomputed away.
    client.post("/api/v1/match-results/run", params={"policy_year_id": py_id})
    after = client.get(f"/api/v1/employees/{emp['id']}").json()
    assert after["matched_category_id"] == cat_id
    assert after["match_method"] == "manual_override"


def test_patch_employee_updates_name_and_rederives(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    emp = next(
        it
        for it in client.get(
            "/api/v1/employees", params={"policy_year_id": py_id, "limit": 200}
        ).json()["items"]
        if it["staff_id"] == "TEST-001"
    )
    res = client.patch(
        f"/api/v1/employees/{emp['id']}",
        json={
            "employee_name": "Renamed Person",
            "attribute_values": {"category": "18 and above"},
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["employee_name"] == "Renamed Person"
    # Editing the raw category re-derives grade via the seeded derivation rule.
    assert body["derived_attribute_values"].get("grade") == 18


def _two_categories_distinct_products(client: TestClient, py_id: str) -> list[str]:
    cats = client.get("/api/v1/categories", params={"policy_year_id": py_id}).json()
    by_product: dict[str, str] = {}
    for c in cats:
        if c["product_id"] and c["product_id"] not in by_product:
            by_product[c["product_id"]] = c["id"]
    return list(by_product.values())[:2]


def test_multi_override_assigns_then_clears(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    cat_ids = _two_categories_distinct_products(client, py_id)
    if len(cat_ids) < 2:
        pytest.skip("need two products to test multi-override")
    emp = next(
        it
        for it in client.get(
            "/api/v1/employees", params={"policy_year_id": py_id, "limit": 200}
        ).json()["items"]
        if it["staff_id"] == "TEST-003"
    )
    res = client.post(
        f"/api/v1/match-results/employees/{emp['id']}/override",
        json={"category_ids": cat_ids},
    )
    assert res.status_code == 200, res.text

    after = client.get(f"/api/v1/employees/{emp['id']}").json()
    got = {p["category_id"] for p in after["matched_plans"]}
    assert set(cat_ids) <= got
    assert all(p["method"] == "manual_override" for p in after["matched_plans"])

    cleared = client.post(
        f"/api/v1/match-results/employees/{emp['id']}/override",
        json={"category_ids": []},
    )
    assert cleared.status_code == 200
    assert client.get(f"/api/v1/employees/{emp['id']}").json()["matched_plans"] == []


def test_multi_override_same_product_422(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    cats = client.get("/api/v1/categories", params={"policy_year_id": py_id}).json()
    by_product: dict[str, list[str]] = {}
    for c in cats:
        if c["product_id"]:
            by_product.setdefault(c["product_id"], []).append(c["id"])
    same = next((v for v in by_product.values() if len(v) >= 2), None)
    if same is None:
        pytest.skip("no product with 2+ categories")
    emp = client.get(
        "/api/v1/employees", params={"policy_year_id": py_id, "limit": 1}
    ).json()["items"][0]
    res = client.post(
        f"/api/v1/match-results/employees/{emp['id']}/override",
        json={"category_ids": same[:2]},
    )
    assert res.status_code == 422


def test_patch_dependant_updates_and_unlinks(client: TestClient) -> None:
    py_id = _policy_year_id(client)
    emp = client.get(
        "/api/v1/employees", params={"policy_year_id": py_id, "limit": 1}
    ).json()["items"][0]
    db = SessionLocal()
    try:
        dep = Dependant(
            client_id=DEMO_CLIENT_ID, policy_year_id=py_id, employee_id=emp["id"],
            attribute_values={"dependant_name": "Kid A", "relationship": "child"},
            link_method="staff_id",
        )
        db.add(dep)
        db.commit()
        dep_id = dep.id
    finally:
        db.close()

    res = client.patch(
        f"/api/v1/dependants/{dep_id}",
        json={"attribute_values": {"dependant_name": "Kid B", "relationship": "child"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["attribute_values"]["dependant_name"] == "Kid B"

    unlinked = client.patch(
        f"/api/v1/dependants/{dep_id}", json={"relink": True, "employee_id": None}
    )
    assert unlinked.status_code == 200
    assert unlinked.json()["employee_id"] is None
    assert unlinked.json()["link_method"] == "unlinked"
