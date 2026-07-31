"""Line-tab backend behaviours — product creation with a line, setup-products
`is_client_product`/`line`, and per-product coverage rows for added products.

Mirrors the dedicated-test-client pattern (own SQLite DB + client D, auth
overridden) so it doesn't pollute the demo client's seed assertions.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_line_tabs.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    CurrentUser,
    get_current_user,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog, Client, PolicyYear, Product  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_D_ID = "00000000-0000-0000-0000-0000000000d1"
PY_D = "00000000-0000-0000-0000-0000000000d2"


def _user_d() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000000dd",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_D_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as db:
        db.add(Client(id=CLIENT_D_ID, name="Client D (test)",
                      broker_firm_id=DEMO_BROKER_FIRM_ID))
        db.flush()
        db.add(PolicyYear(
            id=PY_D, client_id=CLIENT_D_ID, year=2029,
            start_date=date(2029, 1, 1), end_date=date(2029, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(AuditLog).filter(AuditLog.client_id == CLIENT_D_ID).delete(
            synchronize_session=False)
        db.query(Product).filter(Product.client_id == CLIENT_D_ID).delete(
            synchronize_session=False)
        db.query(PolicyYear).filter(PolicyYear.id == PY_D).delete(
            synchronize_session=False)
        db.query(Client).filter(Client.id == CLIENT_D_ID).delete(
            synchronize_session=False)
        db.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(scope="module")
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user_d
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_create_product_persists_line_and_serializes_it(client: TestClient) -> None:
    # Add GPA under the Life tab — line override beats the code-inferred line
    # only when it disagrees; here both are "life", but the override is what's
    # stored. Also assert ProductOut carries `line`.
    res = client.post(
        "/api/v1/schemas/products",
        json={"code": "GPA", "display_name": "Group Personal Accident",
              "line": "life", "form_profile": "accident"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["line"] == "life"

    # A custom code filed under Flex must honour the chosen line, not inference.
    res2 = client.post(
        "/api/v1/schemas/products",
        json={"code": "FLEXWALLET", "display_name": "Flex Wallet", "line": "flex"},
    )
    assert res2.status_code == 201, res2.text
    assert res2.json()["line"] == "flex"

    listed = client.get("/api/v1/schemas/products").json()
    by_code = {p["code"]: p for p in listed}
    assert by_code["GPA"]["line"] == "life"
    assert by_code["FLEXWALLET"]["line"] == "flex"


def test_patch_product_classification_persists_metadata(client: TestClient) -> None:
    # An unknown custom code starts with the inferred defaults; the broker's
    # classification (form profile / layout family / line) rides
    # product_metadata via PATCH and an explicit null clears the override.
    res = client.post(
        "/api/v1/schemas/products",
        json={"code": "GNEWX", "display_name": "New Product"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    pid = body["id"]
    assert body["form_profile"] == "tiered_medical"  # generic default
    assert body["layout_family"] == "plan_tier"

    res2 = client.patch(
        f"/api/v1/schemas/products/{pid}",
        json={"form_profile": "sum_assured", "layout_family": "si_based",
              "line": "life"},
    )
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    assert body2["form_profile"] == "sum_assured"
    assert body2["layout_family"] == "si_based"
    assert body2["line"] == "life"

    # Clearing an override falls back to registry inference.
    res3 = client.patch(
        f"/api/v1/schemas/products/{pid}", json={"form_profile": None}
    )
    assert res3.status_code == 200, res3.text
    assert res3.json()["form_profile"] == "tiered_medical"
    assert res3.json()["layout_family"] == "si_based"  # untouched override kept


def test_setup_products_flags_client_rows_and_line(client: TestClient) -> None:
    summaries = client.get(
        f"/api/v1/policy-years/{PY_D}/setup-products"
    ).json()
    by_code = {s["code"]: s for s in summaries}
    # GPA was added by this client → is_client_product True, line life.
    assert by_code["GPA"]["is_client_product"] is True
    assert by_code["GPA"]["line"] == "life"
    # A bare global recognition row the client never added stays not-client.
    assert by_code["GHS"]["is_client_product"] is False
    assert by_code["GHS"]["line"] == "medical"


def test_added_product_gets_default_coverage_row_and_can_override(
    client: TestClient,
) -> None:
    terms = client.get(f"/api/v1/policy-years/{PY_D}/product-terms").json()
    gpa = next((t for t in terms if t["code"] == "GPA"), None)
    # Added-but-unconfigured product surfaces with a default (policy-year) row.
    assert gpa is not None, "added product should appear in coverage periods"
    assert gpa["is_default"] is True
    assert gpa["line"] == "life"
    assert gpa["coverage_start"] == "2029-01-01"

    # And its coverage period can be overridden even with no plans/categories.
    res = client.put(
        f"/api/v1/policy-years/{PY_D}/product-terms/{gpa['product_id']}",
        json={"coverage_start": "2029-04-01", "coverage_end": "2030-03-31"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_default"] is False
    assert body["line"] == "life"
    # A dates-only update asserts no GST opinion (tri-state None = inherit).
    assert body["gst_included"] is None


def test_gst_partial_update_keeps_the_other_dimension(client: TestClient) -> None:
    terms = client.get(f"/api/v1/policy-years/{PY_D}/product-terms").json()
    gpa = next(t for t in terms if t["code"] == "GPA")
    pid = gpa["product_id"]
    # Clean slate — an earlier test may have left an override on GPA.
    client.delete(f"/api/v1/policy-years/{PY_D}/product-terms/{pid}")

    # GST without dates: the row exists for GST alone; dates keep inheriting.
    res = client.put(
        f"/api/v1/policy-years/{PY_D}/product-terms/{pid}",
        json={"gst_included": True, "gst_rate": 9.0},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["gst_included"] is True and body["gst_rate"] == 9.0
    assert body["is_default"] is True
    assert body["coverage_start"] == "2029-01-01"  # policy year span

    # A subsequent DATES-only update must NOT reset the GST opinion (#4 fix).
    res = client.put(
        f"/api/v1/policy-years/{PY_D}/product-terms/{pid}",
        json={"coverage_start": "2029-04-01", "coverage_end": "2030-03-31"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_default"] is False
    assert body["gst_included"] is True and body["gst_rate"] == 9.0

    # A GST-only update must NOT wipe the now-explicit coverage dates (#4 fix).
    res = client.put(
        f"/api/v1/policy-years/{PY_D}/product-terms/{pid}",
        json={"gst_included": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["gst_included"] is False  # explicit "off" is preserved, not None
    assert body["is_default"] is False and body["coverage_start"] == "2029-04-01"

    # One date without the other is rejected; an out-of-range rate is rejected.
    assert client.put(
        f"/api/v1/policy-years/{PY_D}/product-terms/{pid}",
        json={"coverage_start": "2029-04-01"},
    ).status_code == 422
    assert client.put(
        f"/api/v1/policy-years/{PY_D}/product-terms/{pid}",
        json={"gst_included": True, "gst_rate": 150},
    ).status_code == 422

    # Reset removes the row entirely.
    assert client.delete(
        f"/api/v1/policy-years/{PY_D}/product-terms/{pid}"
    ).status_code == 204


def test_remove_product_drops_client_row_and_coverage(client: TestClient) -> None:
    # FLEXWALLET was added (client row) but never configured. Removing it deletes
    # the client catalog row and its coverage override, so it leaves the tab.
    before = client.get("/api/v1/schemas/products").json()
    assert any(p["code"] == "FLEXWALLET" for p in before)

    res = client.delete(f"/api/v1/policy-years/{PY_D}/products/FLEXWALLET")
    assert res.status_code == 204, res.text

    after = {p["code"] for p in client.get("/api/v1/schemas/products").json()}
    assert "FLEXWALLET" not in after
    # Removal is idempotent — a second delete is still a clean 204.
    assert (
        client.delete(f"/api/v1/policy-years/{PY_D}/products/FLEXWALLET").status_code
        == 204
    )


def test_create_product_firm_scope_creates_global_row(client: TestClient) -> None:
    # scope=firm as a broker_admin lands a shared firm-library row (client_id NULL)
    # visible to every company.
    res = client.post(
        "/api/v1/schemas/products?scope=firm",
        json={"code": "FIRMLIB", "display_name": "Firm Library Product"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["client_id"] is None
    with SessionLocal() as db:
        row = db.query(Product).filter(Product.code == "FIRMLIB").one()
        assert row.client_id is None


def test_create_product_firm_scope_requires_admin() -> None:
    # A writer that isn't a firm admin (client_hr passes require_write_access but
    # not the global-write gate) can't add firm-library rows.
    def _hr() -> CurrentUser:
        return CurrentUser(
            user_id="00000000-0000-0000-0000-0000000000de",
            broker_firm_id=DEMO_BROKER_FIRM_ID,
            client_id=CLIENT_D_ID,
            role="client_hr",
        )

    app.dependency_overrides[get_current_user] = _hr
    try:
        res = TestClient(app).post(
            "/api/v1/schemas/products?scope=firm",
            json={"code": "NOPE", "display_name": "Nope"},
        )
        assert res.status_code == 403, res.text
    finally:
        app.dependency_overrides[get_current_user] = _user_d


def test_envelope_paths_agree_on_a_half_written_term(client: TestClient) -> None:
    """`envelopes_for` (policy-year list) and `envelope_for` (single year) must
    resolve a ProductTerm's dates identically.

    They didn't: the batched path honoured an override only when BOTH dates were
    set, the single-year path when the START was set. `ProductTermUpdate`
    enforces both-or-neither, so only a migrated or hand-written row reaches the
    difference — but the batched path is what feeds `PolicyYearOut.coverage_*`,
    which the UI gates "Set current" on, so a disagreement there strands the
    member portal. Both now go through `term_window`.
    """
    from app.models import Category, ProductTerm
    from app.services.product_terms import envelope_for, envelopes_for

    terms = client.get(f"/api/v1/policy-years/{PY_D}/product-terms").json()
    gpa = next(t for t in terms if t["code"] == "GPA")
    client.delete(f"/api/v1/policy-years/{PY_D}/product-terms/{gpa['product_id']}")

    with SessionLocal() as s:
        # Bind the product INTO the year, so it actually shapes the envelope.
        s.add(
            Category(
                id="00000000-0000-0000-0000-0000000000dc",
                policy_year_id=PY_D,
                product_id=gpa["product_id"],
                display_name="Envelope probe",
                raw_description="Envelope probe",
                source="manual",
            )
        )
        s.add(
            ProductTerm(
                policy_year_id=PY_D,
                product_id=gpa["product_id"],
                coverage_start=date(2029, 4, 1),  # end deliberately absent
            )
        )
        s.commit()
    try:
        with SessionLocal() as s:
            py = s.get(PolicyYear, PY_D)
            batched = envelopes_for(s, [py])[PY_D]
            assert batched == envelope_for(s, py)
            # The start override is honoured; the absent end still inherits.
            assert batched == (date(2029, 4, 1), py.end_date)
    finally:
        with SessionLocal() as s:
            s.query(ProductTerm).filter(
                ProductTerm.policy_year_id == PY_D,
                ProductTerm.product_id == gpa["product_id"],
            ).delete()
            s.query(Category).filter(
                Category.id == "00000000-0000-0000-0000-0000000000dc"
            ).delete()
            s.commit()
