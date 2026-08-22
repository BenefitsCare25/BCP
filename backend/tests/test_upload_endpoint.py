"""End-to-end smoke tests for the v1 API endpoints.

These run against an isolated SQLite DB so they don't disturb the dev DB.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Point the engine at a temp DB BEFORE app modules load.
TEST_DB = Path(__file__).parent / "_test.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "placement_slips"
    / "STMicroelectronics - Placement Slips 2026_workingfile (1).xls"
)
VDL_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "placement_slips"
    / "VDL - Placement Slips 2026 (as at 13 Apr 2026).xls"
)

# See test_match_results_endpoint.py: the workbooks are real broker PII and are
# never committed, and these tests are sequentially dependent on the upload the
# first one performs. Skipping per-test left the dependents asserting against an
# empty database, so skip the module instead.
pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=f"Requires uncommitted PII workbook: {FIXTURE.name}",
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


def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_policy_years_seeded(client: TestClient) -> None:
    res = client.get("/api/v1/policy-years")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["year"] == 2026


def test_employee_attributes_seeded(client: TestClient) -> None:
    res = client.get("/api/v1/schemas/employee-attributes")
    assert res.status_code == 200
    attrs = res.json()
    assert {a["attribute_id"] for a in attrs} >= {
        "grade",
        "pass",
        "class",
        "salary",
        "family_status",
    }


def test_products_seeded(client: TestClient) -> None:
    res = client.get("/api/v1/schemas/products")
    assert res.status_code == 200
    codes = {p["code"] for p in res.json()}
    # Original Singapore base + new codes seen across PNG/CBRE/Hartree/CDL slips.
    assert codes >= {"GTL", "GHS", "GMM", "SP", "GPA", "GBT", "WICA"}
    assert {"GCI", "GDD", "GCGP", "GCSP", "GD", "GP", "OSI", "DENTAL"} <= codes


def test_upload_and_categories_persisted(client: TestClient) -> None:
    if not FIXTURE.exists():
        pytest.skip(f"Fixture not present: {FIXTURE}")

    py_id = client.get("/api/v1/policy-years").json()[0]["id"]

    with FIXTURE.open("rb") as f:
        res = client.post(
            "/api/v1/placement-slips/parse",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
            data={"policy_year_id": py_id},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_categories"] >= 24

    grouped = client.get(
        "/api/v1/categories/grouped", params={"policy_year_id": py_id}
    )
    assert grouped.status_code == 200
    groups = grouped.json()
    assert len(groups) >= 6

    coverage = client.get(
        "/api/v1/categories/stats/coverage", params={"policy_year_id": py_id}
    )
    assert coverage.status_code == 200
    stats = coverage.json()
    assert stats["total"] >= 24
    assert stats["needs_review"] >= 1


def test_reupload_replaces_not_duplicates(client: TestClient) -> None:
    """Re-uploading the same slip supersedes its prior unreviewed auto rows
    instead of stacking a second full copy (regression for duplicate rows)."""
    if not FIXTURE.exists():
        pytest.skip(f"Fixture not present: {FIXTURE}")

    py_id = client.get("/api/v1/policy-years").json()[0]["id"]

    def _count() -> int:
        return client.get(
            "/api/v1/categories", params={"policy_year_id": py_id}
        ).json().__len__()

    before = _count()
    assert before > 0  # earlier test already uploaded once

    with FIXTURE.open("rb") as f:
        res = client.post(
            "/api/v1/placement-slips/parse",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
            data={"policy_year_id": py_id},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    # The prior parse's unreviewed auto rows were cleared first.
    assert body["replaced_categories"] == before
    # Net count is unchanged — no duplicate stacking.
    assert _count() == before


def test_reupload_reconciles_a_confirmed_slip_category(client: TestClient) -> None:
    """Reviewed slip rows are refreshed in place instead of being duplicated."""

    py_id = client.get("/api/v1/policy-years").json()[0]["id"]
    categories = client.get(
        "/api/v1/categories", params={"policy_year_id": py_id}
    ).json()
    candidate = next(
        row
        for row in categories
        if row["source"] == "system_generated" and row["matching_rule"] is not None
    )
    confirmed = client.post(f"/api/v1/categories/{candidate['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text

    before = len(categories)
    with FIXTURE.open("rb") as file:
        response = client.post(
            "/api/v1/placement-slips/parse",
            files={"file": (FIXTURE.name, file, "application/vnd.ms-excel")},
            data={"policy_year_id": py_id},
        )
    assert response.status_code == 200, response.text

    after = client.get(
        "/api/v1/categories", params={"policy_year_id": py_id}
    ).json()
    assert len(after) == before
    refreshed = next(row for row in after if row["id"] == candidate["id"])
    assert refreshed["status"] == "confirmed"
    assert refreshed["source_ref"] != candidate["source_ref"]


def test_category_patch_flips_to_manual(client: TestClient) -> None:
    py_id = client.get("/api/v1/policy-years").json()[0]["id"]
    cats = client.get(
        "/api/v1/categories", params={"policy_year_id": py_id}
    ).json()
    cat_id = cats[0]["id"]

    res = client.patch(
        f"/api/v1/categories/{cat_id}",
        json={"display_name": cats[0]["display_name"] + " (edited)"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "manual"
    assert body["human_modified"] is True


def test_discard_setup_draft_deletes_it(client: TestClient) -> None:
    """DELETE on a product-setup removes the draft so the form opens blank."""
    if not FIXTURE.exists():
        pytest.skip(f"Fixture not present: {FIXTURE}")

    py_id = client.get("/api/v1/policy-years").json()[0]["id"]
    setups = client.get(f"/api/v1/policy-years/{py_id}/product-setups").json()
    assert len(setups) > 0
    code = setups[0]["product_code"]

    res = client.delete(f"/api/v1/policy-years/{py_id}/product-setups/{code}")
    assert res.status_code == 204

    after = client.get(f"/api/v1/policy-years/{py_id}/product-setups").json()
    assert all(s["product_code"] != code for s in after)
    # Idempotent — deleting a missing draft is still a 204.
    assert (
        client.delete(f"/api/v1/policy-years/{py_id}/product-setups/{code}").status_code
        == 204
    )


def test_clear_all_cascades_to_setups_and_plans(client: TestClient) -> None:
    """'Clear all' removes categories AND the unconfirmed setup drafts +
    provisional plans that feed the setup form (regression: form stayed
    populated after clearing categories)."""
    if not FIXTURE.exists():
        pytest.skip(f"Fixture not present: {FIXTURE}")

    py_id = client.get("/api/v1/policy-years").json()[0]["id"]

    # Pre-state: earlier uploads seeded drafts and plans.
    setups_before = client.get(
        f"/api/v1/policy-years/{py_id}/product-setups"
    ).json()
    assert len(setups_before) > 0

    res = client.delete("/api/v1/categories", params={"policy_year_id": py_id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] > 0
    assert body["setups_deleted"] > 0  # draft forms cleared

    # Categories and draft setups are gone afterwards.
    cats = client.get(
        "/api/v1/categories", params={"policy_year_id": py_id}
    ).json()
    assert cats == []
    setups_after = client.get(
        f"/api/v1/policy-years/{py_id}/product-setups"
    ).json()
    assert len(setups_after) < len(setups_before)


def test_vdl_split_ghs_upload_single_draft(client: TestClient) -> None:
    """VDL splits GHS into Locals/Secondees/Dependants — all three map to product
    GHS. The upload must not trip the (policy_year_id, product_code) unique
    constraint, and must leave exactly one GHS draft with a populated SOB whose
    template resolves with the tiered models (regression for the split-GHS crash).
    """
    if not VDL_FIXTURE.exists():
        pytest.skip(f"VDL fixture not present: {VDL_FIXTURE}")

    py_id = client.get("/api/v1/policy-years").json()[0]["id"]
    client.delete("/api/v1/categories", params={"policy_year_id": py_id})

    with VDL_FIXTURE.open("rb") as f:
        res = client.post(
            "/api/v1/placement-slips/parse",
            files={"file": (VDL_FIXTURE.name, f, "application/vnd.ms-excel")},
            data={"policy_year_id": py_id},
        )
    assert res.status_code == 200, res.text  # no UNIQUE-constraint crash

    setups = client.get(f"/api/v1/policy-years/{py_id}/product-setups").json()
    ghs = [s for s in setups if s["product_code"] == "GHS"]
    assert len(ghs) == 1, f"expected one GHS draft, got {len(ghs)}"
    answers = ghs[0]["answers"]
    selected = [p for p in answers["plans"] if p["selected"]]
    assert selected, "GHS draft has no selected plans"
    # The SOB now lives in the decoupled column model (answers.sob), not
    # replicated per plan. GHS genuinely varies per plan, so the de-dup must keep
    # more than one benefit column.
    sob = answers["sob"]
    assert len(sob["items"]) > 0, "GHS draft has an empty SOB"
    assert sob["columns"], "GHS draft has no benefit columns"
    # Every benefit column maps to ≥1 basis plan.
    assert all(c["plan_codes"] for c in sob["columns"])

    tpl = client.get(
        f"/api/v1/policy-years/{py_id}/setup-products/GHS/template"
    )
    assert tpl.status_code == 200, tpl.text
    assert tpl.json()["basis_model"] == "tiered"
    assert tpl.json()["rate_model"] == "tiered"


def test_reupload_materializes_and_replaces_plans(client: TestClient) -> None:
    """Every schedule-bearing product materializes plans (including the
    descriptive term-life / GPA / WICI layouts), and re-uploading replaces the
    prior parse's auto plans instead of orphaning stale ones."""
    if not FIXTURE.exists():
        pytest.skip(f"Fixture not present: {FIXTURE}")

    py_id = client.get("/api/v1/policy-years").json()[0]["id"]
    # Clean slate (clearing categories also drops provisional plans/setups).
    client.delete("/api/v1/categories", params={"policy_year_id": py_id})

    def _upload():
        with FIXTURE.open("rb") as f:
            return client.post(
                "/api/v1/placement-slips/parse",
                files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
                data={"policy_year_id": py_id},
            )

    def _plans() -> dict:
        return client.get(
            "/api/v1/plans", params={"policy_year_id": py_id, "limit": 200}
        ).json()

    first = _upload()
    assert first.status_code == 200, first.text
    p1 = _plans()
    count1 = p1["total"]
    assert count1 > 0, "no plans materialized from the slip"

    # Descriptive layouts (GTL/GPA/WICI) carry their value in a free-text column;
    # those benefits surface with non-empty values, not reviewer notes.
    all_items = [
        it
        for pl in p1["items"]
        if pl["benefit_schedule"]
        for it in pl["benefit_schedule"].get("items", [])
    ]
    assert any(
        (it.get("value") or "").lower().find("sum insured") != -1
        for it in all_items
    ), "descriptive Schedule of Benefits (e.g. term life) was not materialized"
    assert all(
        (it.get("value") or "") not in ("O.K", "N.A", "to check")
        for it in all_items
    ), "reviewer-note column leaked into a benefit value"

    # Re-upload: the prior auto plans are cleared first, so the net count is
    # unchanged (no orphan stacking) and the clear is reported.
    second = _upload()
    assert second.status_code == 200, second.text
    assert second.json()["replaced_plans"] >= 1
    assert _plans()["total"] == count1


def test_audit_log_has_entries(client: TestClient) -> None:
    res = client.get("/api/v1/audit-log")
    assert res.status_code == 200
    assert res.json()["total"] > 0
