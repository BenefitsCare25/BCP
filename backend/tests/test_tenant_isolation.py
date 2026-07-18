"""Cross-tenant isolation regression — every endpoint that takes a tenant-
owned resource ID must refuse access when the caller's `client_id` differs.

The test sets up TWO clients (A = seed demo, B = a second client created
inline) and confirms client A signed in cannot read or mutate client B's
policy year, categories, employees, dependants, audit log, or placement slips.

A user with no `client_id` (mock without a tenant binding) should also be
refused. System admins can read across tenants but every cross-tenant access
should be recorded in the audit log with `cross_tenant_access=True`.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_tenant_isolation.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Dependant,
    Employee,
    Enrollment,
    EnrollmentWindow,
    PanelListing,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_B_ID = "00000000-0000-0000-0000-0000000000b0"
USER_B_ID = "00000000-0000-0000-0000-0000000000b1"


def _user_a() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )


def _user_b() -> CurrentUser:
    return CurrentUser(
        user_id=USER_B_ID,
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_B_ID,
        role="broker_admin",
    )


def _user_no_client() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000000c0",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=None,
        role="broker_admin",
    )


def _user_system_admin() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000000d0",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="system_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()

    # Add a second client (B) with its own policy year + category + employee
    # so every endpoint has a B-owned resource to attempt to access from A.
    with SessionLocal() as session:
        client_b = Client(
            id=CLIENT_B_ID,
            name="Client B (test)",
            broker_firm_id=DEMO_BROKER_FIRM_ID,
        )
        session.add(client_b)
        session.flush()

        py_b = PolicyYear(
            id="00000000-0000-0000-0000-0000000000b2",
            client_id=CLIENT_B_ID,
            year=2026,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status=PolicyYearStatus.draft,
        )
        session.add(py_b)
        session.flush()

        cat_b = Category(
            id="00000000-0000-0000-0000-0000000000b3",
            policy_year_id=py_b.id,
            priority=1,
            display_name="Client B secret category",
            raw_description="Client B secret category",
            source=SourceKind.system_generated.value,
            status=CategoryStatus.needs_review.value,
            human_modified=False,
        )
        session.add(cat_b)

        emp_b = Employee(
            id="00000000-0000-0000-0000-0000000000b4",
            client_id=CLIENT_B_ID,
            policy_year_id=py_b.id,
            staff_id="B-STAFF-1",
            employee_name="Bea Beta",
            attribute_values={"grade": 18},
            derived_attribute_values={},
            source="csv_import",
            status="active",
        )
        session.add(emp_b)
        session.flush()

        dep_b = Dependant(
            id="00000000-0000-0000-0000-0000000000b5",
            client_id=CLIENT_B_ID,
            policy_year_id=py_b.id,
            employee_id=emp_b.id,
            attribute_values={"relationship": "spouse"},
            link_method="staff_id",
            status="active",
        )
        session.add(dep_b)

        # A plan owned by client B (for cross-tenant plan access checks).
        product_id = session.query(Product.id).first()[0]
        plan_b = Plan(
            id=PLAN_B,
            product_id=product_id,
            policy_year_id=py_b.id,
            code="B1",
            display_name="Client B plan",
            status="needs_review",
        )
        session.add(plan_b)

        # An enrollment window owned by client B (for window-id isolation checks).
        win_b = EnrollmentWindow(
            id=WINDOW_B,
            policy_year_id=py_b.id,
            client_id=CLIENT_B_ID,
            name="Client B window",
            opens_at=datetime(2026, 1, 1, tzinfo=UTC),
            closes_at=datetime(2026, 2, 1, tzinfo=UTC),
        )
        session.add(win_b)
        session.flush()

        # An enrollment owned by client B (for enrollment-id isolation checks).
        enr_b = Enrollment(
            id=ENROLL_B,
            window_id=win_b.id,
            policy_year_id=py_b.id,
            client_id=CLIENT_B_ID,
            employee_id=emp_b.id,
        )
        session.add(enr_b)

        # A panel listing owned by client B (clinic-locator isolation checks).
        session.add(
            PanelListing(
                id=PANEL_B,
                client_id=CLIENT_B_ID,
                insurer="GE-SG",
                panel_provider="Adept",
                country="SG",
                clinic_type="gp",
            )
        )

        session.commit()

    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client_as_a() -> TestClient:
    app.dependency_overrides[get_current_user] = _user_a
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client_as_no_tenant() -> TestClient:
    app.dependency_overrides[get_current_user] = _user_no_client
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client_as_system_admin() -> TestClient:
    app.dependency_overrides[get_current_user] = _user_system_admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Client B resource IDs (created in fixture) ──────────────────────────────
PY_B = "00000000-0000-0000-0000-0000000000b2"
CAT_B = "00000000-0000-0000-0000-0000000000b3"
EMP_B = "00000000-0000-0000-0000-0000000000b4"
DEP_B = "00000000-0000-0000-0000-0000000000b5"
PLAN_B = "00000000-0000-0000-0000-0000000000b6"
# Any product id — the cross-tenant guard rejects at the policy year before the
# product is ever looked up, so this need not exist.
PRODUCT_B = "00000000-0000-0000-0000-0000000000b7"
WINDOW_B = "00000000-0000-0000-0000-0000000000b8"
ENROLL_B = "00000000-0000-0000-0000-0000000000b9"
PANEL_B = "00000000-0000-0000-0000-0000000000ba"


# ── PolicyYear ──────────────────────────────────────────────────────────────
def test_policy_year_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}")
    assert res.status_code == 404


def test_policy_year_activation_readiness_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/activation-readiness")
    assert res.status_code == 404


def test_policy_year_activate_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/policy-years/{PY_B}/activate")
    assert res.status_code == 404


def test_policy_year_snapshot_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/snapshot")
    assert res.status_code == 404


def test_reports_benefit_selection_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/reports/benefit-selection")
    assert res.status_code == 404


def test_reports_employee_listing_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(
        f"/api/v1/policy-years/{PY_B}/reports/employee-listing?insurer=AIA"
    )
    assert res.status_code == 404


def test_reports_dependant_listing_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(
        f"/api/v1/policy-years/{PY_B}/reports/dependant-listing?insurer=AIA"
    )
    assert res.status_code == 404


def test_reports_readiness_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/reports/readiness")
    assert res.status_code == 404


def test_reports_member_listing_template_cross_tenant_404(
    client_as_a: TestClient,
) -> None:
    res = client_as_a.get(
        f"/api/v1/policy-years/{PY_B}/reports/member-listing-template"
    )
    assert res.status_code == 404


def test_underwriting_cases_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/underwriting/cases")
    assert res.status_code == 404


def test_underwriting_refresh_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/policy-years/{PY_B}/underwriting/refresh")
    assert res.status_code == 404


def test_fact_find_form_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/fact-find-form")
    assert res.status_code == 404


def test_fact_find_form_download_own_tenant(client_as_a: TestClient) -> None:
    import io

    from docx import Document

    rows = client_as_a.get("/api/v1/policy-years").json()
    assert rows, "expected at least one policy year for client A"
    res = client_as_a.get(f"/api/v1/policy-years/{rows[0]['id']}/fact-find-form")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert res.content[:2] == b"PK"  # valid .docx (zip) magic
    assert "x-factfind-notes" in {k.lower() for k in res.headers}
    # The filled template opens and the company-name cell is populated.
    doc = Document(io.BytesIO(res.content))
    cells = [
        p.text
        for t in doc.tables
        for r in t.rows
        for c in r.cells
        for p in c.paragraphs
    ]
    assert any("Name of Company" in c for c in cells)


def test_policy_year_list_only_returns_own_tenant(client_as_a: TestClient) -> None:
    rows = client_as_a.get("/api/v1/policy-years").json()
    ids = {r["id"] for r in rows}
    assert PY_B not in ids


# ── Product terms (per-product coverage periods) ────────────────────────────
def test_product_terms_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/product-terms")
    assert res.status_code == 404


def test_product_term_set_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/policy-years/{PY_B}/product-terms/{PRODUCT_B}",
        json={"coverage_start": "2026-04-01", "coverage_end": "2027-03-31"},
    )
    assert res.status_code == 404


def test_product_term_reset_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete(
        f"/api/v1/policy-years/{PY_B}/product-terms/{PRODUCT_B}"
    )
    assert res.status_code == 404


def test_remove_product_cross_tenant_404(client_as_a: TestClient) -> None:
    # The policy year belongs to tenant B — load_policy_year must 404 before any
    # delete touches B's products.
    res = client_as_a.delete(f"/api/v1/policy-years/{PY_B}/products/GHS")
    assert res.status_code == 404


# ── Flex scheme (flexible benefits) ─────────────────────────────────────────
def test_flex_scheme_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/flex-scheme")
    assert res.status_code == 404


def test_flex_scheme_put_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/policy-years/{PY_B}/flex-scheme",
        json={"scheme": {"meta": {"currency": "SGD"}, "tiers": []}},
    )
    assert res.status_code == 404


def test_flex_scheme_confirm_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/policy-years/{PY_B}/flex-scheme/confirm")
    assert res.status_code == 404


def test_flex_scheme_extract_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/flex-scheme/extract",
        files={"files": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert res.status_code == 404


def test_flex_scheme_membership_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/flex-scheme/membership")
    assert res.status_code == 404


def test_flex_scheme_assign_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/policy-years/{PY_B}/flex-scheme/assign")
    assert res.status_code == 404


def test_flex_scheme_coverage_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/flex-scheme/coverage")
    assert res.status_code == 404


def test_flex_scheme_coverage_export_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/flex-scheme/coverage/export")
    assert res.status_code == 404


def test_flex_scheme_roster_vocab_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/flex-scheme/roster-vocab")
    assert res.status_code == 404


def test_flex_scheme_suggest_matches_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/flex-scheme/suggest-matches"
    )
    assert res.status_code == 404


def test_flex_pricing_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/flex-pricing")
    assert res.status_code == 404


def test_flex_pricing_put_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/policy-years/{PY_B}/flex-pricing",
        json={"pricing": {"products": {}}},
    )
    assert res.status_code == 404


def test_voluntary_rates_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(
        f"/api/v1/policy-years/{PY_B}/products/{PRODUCT_B}/voluntary-rates"
    )
    assert res.status_code == 404


def test_voluntary_rates_put_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/policy-years/{PY_B}/products/{PRODUCT_B}/voluntary-rates",
        json={"bands": [{"label": "all", "min": None, "max": None, "rate": 1.0}]},
    )
    assert res.status_code == 404


# ── Categories ──────────────────────────────────────────────────────────────
def test_category_list_cross_tenant_policy_year_404(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/categories", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_category_grouped_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/categories/grouped", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_category_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/categories/{CAT_B}")
    assert res.status_code == 404


def test_category_patch_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.patch(
        f"/api/v1/categories/{CAT_B}", json={"display_name": "hijack"}
    )
    assert res.status_code == 404


def test_category_create_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        "/api/v1/categories",
        json={"policy_year_id": PY_B, "display_name": "intruder"},
    )
    assert res.status_code == 404


def test_category_confirm_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/categories/{CAT_B}/confirm")
    assert res.status_code == 404


def test_category_bulk_confirm_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post("/api/v1/categories/bulk-confirm", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_category_delete_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete(f"/api/v1/categories/{CAT_B}")
    assert res.status_code == 404


def test_category_bulk_delete_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete("/api/v1/categories", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_category_ai_suggest_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/categories/{CAT_B}/ai-suggest")
    assert res.status_code == 404


def test_category_coverage_stats_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/categories/stats/coverage", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_placement_slips_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/placement-slips", params={"policy_year_id": PY_B})
    assert res.status_code == 404


# ── Employees / Dependants / Matches ────────────────────────────────────────
def test_employee_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/employees", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_employee_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/employees/{EMP_B}")
    assert res.status_code == 404


def test_employee_benefit_statement_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/employees/{EMP_B}/benefit-statement")
    assert res.status_code == 404


def test_employee_coverage_summary_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(
        "/api/v1/employees/coverage-summary", params={"policy_year_id": PY_B}
    )
    assert res.status_code == 404


def test_employee_coverage_export_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(
        "/api/v1/employees/coverage-summary/export", params={"policy_year_id": PY_B}
    )
    assert res.status_code == 404


def test_employee_coverage_report_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(
        "/api/v1/employees/coverage-report/export", params={"policy_year_id": PY_B}
    )
    assert res.status_code == 404


def test_dependant_coverage_report_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(
        "/api/v1/dependants/coverage-report/export", params={"policy_year_id": PY_B}
    )
    assert res.status_code == 404


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FAKE_XLSX = ("adc.xlsx", b"PK\x03\x04 not-a-real-xlsx", _XLSX_MIME)


def test_adc_template_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/adc/template")
    assert res.status_code == 404


def test_adc_preview_cross_tenant_404(client_as_a: TestClient) -> None:
    # The tenant guard fires before the upload is parsed, so a fake file is fine.
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/adc/preview", files={"file": _FAKE_XLSX}
    )
    assert res.status_code == 404


def test_adc_apply_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/adc/apply", files={"file": _FAKE_XLSX}
    )
    assert res.status_code == 404


def test_employee_portal_preview_cross_tenant_404(client_as_a: TestClient) -> None:
    for suffix in (
        "",
        "/benefit-statement",
        "/utilization",
        "/coverage-options",
        "/enrollment",
        "/dependants",
        "/claims",
    ):
        res = client_as_a.get(f"/api/v1/employees/{EMP_B}/portal-preview{suffix}")
        assert res.status_code == 404, suffix


def test_employee_bulk_delete_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete("/api/v1/employees", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_employee_patch_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.patch(
        f"/api/v1/employees/{EMP_B}", json={"employee_name": "hijack"}
    )
    assert res.status_code == 404


# ── Plan overrides (enrollment module) ──────────────────────────────────────
def test_plan_overrides_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/employees/{EMP_B}/plan-overrides")
    assert res.status_code == 404


def test_plan_overrides_put_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/employees/{EMP_B}/plan-overrides/GHS",
        json={"plan_code": "B1"},
    )
    assert res.status_code == 404


def test_plan_overrides_delete_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete(f"/api/v1/employees/{EMP_B}/plan-overrides/GHS")
    assert res.status_code == 404


def test_coverage_history_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/employees/{EMP_B}/coverage-history")
    assert res.status_code == 404


def test_coverage_revert_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/employees/{EMP_B}/coverage/revert", json={"target": "default"}
    )
    assert res.status_code == 404


def test_enrollment_reset_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/enrollments/{ENROLL_B}/reset")
    assert res.status_code == 404


def test_enrollment_reopen_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/enrollments/{ENROLL_B}/reopen")
    assert res.status_code == 404


# ── Enrollment windows + leave policy (enrollment module) ───────────────────
def test_enrollment_windows_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/enrollment-windows")
    assert res.status_code == 404


def test_enrollment_window_create_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/enrollment-windows",
        json={"name": "x", "opens_at": "2026-01-01T00:00:00Z",
              "closes_at": "2026-02-01T00:00:00Z"},
    )
    assert res.status_code == 404


def test_enrollment_window_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/enrollment-windows/{WINDOW_B}")
    assert res.status_code == 404


def test_enrollment_window_patch_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.patch(
        f"/api/v1/enrollment-windows/{WINDOW_B}", json={"name": "hijack"}
    )
    assert res.status_code == 404


def test_enrollment_window_delete_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete(f"/api/v1/enrollment-windows/{WINDOW_B}")
    assert res.status_code == 404


def test_leave_policy_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/leave-policy")
    assert res.status_code == 404


def test_leave_policy_put_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/policy-years/{PY_B}/leave-policy", json={"allow_buy": True}
    )
    assert res.status_code == 404


def test_leave_rate_options_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/leave-rate-options")
    assert res.status_code == 404


def test_window_open_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/enrollment-windows/{WINDOW_B}/open")
    assert res.status_code == 404


def test_window_close_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/enrollment-windows/{WINDOW_B}/close")
    assert res.status_code == 404


def test_window_enrollments_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/enrollment-windows/{WINDOW_B}/enrollments")
    assert res.status_code == 404


def test_enrollment_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/enrollments/{ENROLL_B}")
    assert res.status_code == 404


def test_enrollment_options_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/enrollments/{ENROLL_B}/options")
    assert res.status_code == 404


def test_enrollment_elections_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/enrollments/{ENROLL_B}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "B1"}]},
    )
    assert res.status_code == 404


def test_enrollment_leave_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/enrollments/{ENROLL_B}/leave", json={"action": "none", "days": 0}
    )
    assert res.status_code == 404


def test_enrollment_submit_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/enrollments/{ENROLL_B}/submit")
    assert res.status_code == 404


def test_enrollment_confirm_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/enrollments/{ENROLL_B}/confirm")
    assert res.status_code == 404


def test_bulk_preview_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/bulk-plan-updates/preview",
        json={"product_code": "MED", "action": "decline",
              "selector": {"employee_ids": [EMP_B]}},
    )
    assert res.status_code == 404


def test_bulk_apply_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "decline",
              "selector": {"employee_ids": [EMP_B]}},
    )
    assert res.status_code == 404


def test_orphan_overrides_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/plan-overrides/orphans")
    assert res.status_code == 404


def test_dependant_patch_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.patch(
        f"/api/v1/dependants/{DEP_B}", json={"attribute_values": {"x": 1}}
    )
    assert res.status_code == 404


def test_dependant_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/dependants", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_dependant_bulk_delete_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete("/api/v1/dependants", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_match_results_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/match-results", params={"policy_year_id": PY_B})
    assert res.status_code == 404


def test_match_run_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post("/api/v1/match-results/run", params={"policy_year_id": PY_B})
    assert res.status_code == 404


# ── Plans (Schedule of Benefits) ─────────────────────────────────────────────
def test_plan_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/plans/{PLAN_B}")
    assert res.status_code == 404


def test_plan_patch_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.patch(
        f"/api/v1/plans/{PLAN_B}", json={"display_name": "hijack"}
    )
    assert res.status_code == 404


# ── Config recommendations ──────────────────────────────────────────────────
def test_recommend_config_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(f"/api/v1/policy-years/{PY_B}/recommend-config")
    assert res.status_code == 404


def test_apply_config_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/apply-config",
        json={"attributes": [{"attribute_id": "hijack", "display_name": "x",
                              "data_type": "string"}], "products": [],
              "rerun_matching": False},
    )
    assert res.status_code == 404


# ── Product setup ───────────────────────────────────────────────────────────
def test_product_setups_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/product-setups")
    assert res.status_code == 404


def test_product_setup_get_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/product-setups/GHS")
    assert res.status_code == 404


def test_product_setup_save_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.put(
        f"/api/v1/policy-years/{PY_B}/product-setups/GHS",
        json={"answers": {}, "template_version": 1},
    )
    assert res.status_code == 404


def test_product_setup_confirm_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/product-setups/GHS/confirm",
        json={"answers": {"plans": [{"code": "1", "selected": True}]},
              "template_version": 1},
    )
    assert res.status_code == 404


def test_product_setup_discard_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete(f"/api/v1/policy-years/{PY_B}/product-setups/GHS")
    assert res.status_code == 404


def test_member_counts_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/member-counts",
        json={"product_code": "GHS", "has_dependants": True, "categories": []},
    )
    assert res.status_code == 404


# ── Audit log ───────────────────────────────────────────────────────────────
def test_audit_log_scoped_to_own_tenant(client_as_a: TestClient) -> None:
    """Audit log returns only the caller's own client rows."""
    res = client_as_a.get("/api/v1/audit-log")
    assert res.status_code == 200
    rows = res.json()["items"]
    foreign = [r for r in rows if r.get("client_id") and r["client_id"] != DEMO_CLIENT_ID]
    assert not foreign, f"audit log leaked rows from other tenants: {foreign}"


def test_audit_log_system_admin_sees_all(client_as_system_admin: TestClient) -> None:
    """System admins are exempt — they see every tenant's rows."""
    res = client_as_system_admin.get("/api/v1/audit-log")
    assert res.status_code == 200


# ── User without a client_id ────────────────────────────────────────────────
def test_no_tenant_user_cannot_list_policy_years(client_as_no_tenant: TestClient) -> None:
    res = client_as_no_tenant.get("/api/v1/policy-years")
    assert res.status_code == 400
    assert "active client" in res.text.lower()


# ── Slip template profiles (broker-corrected column mappings) ────────────────
def _put_profile_as(user_factory, payload: dict):
    """PUT the template-profile endpoint as a specific user. Sets the override
    per-call (the shared client_as_* fixtures can't both be active at once)."""
    app.dependency_overrides[get_current_user] = user_factory
    try:
        return TestClient(app).put(
            "/api/v1/placement-slips/template-profiles", json=payload
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_template_profile_is_scoped_per_tenant() -> None:
    """A and B saving the SAME fingerprint must not collide — the override is
    keyed by the caller's active client, so neither can read or overwrite the
    other's mapping."""
    fp = "shared-fp-xyz"
    a = _put_profile_as(
        _user_a, {"fingerprint": fp, "product_code": "GBT", "roles": {"name_col": 0}}
    )
    assert a.status_code == 200, a.text
    b = _put_profile_as(
        _user_b, {"fingerprint": fp, "product_code": "GBT", "roles": {"name_col": 3}}
    )
    assert b.status_code == 200, b.text

    # Two distinct rows (one per tenant), each with its own mapping.
    from app.models import SlipTemplateProfile

    with SessionLocal() as s:
        a_row = (
            s.query(SlipTemplateProfile)
            .filter_by(client_id=DEMO_CLIENT_ID, fingerprint=fp)
            .one()
        )
        b_row = (
            s.query(SlipTemplateProfile)
            .filter_by(client_id=CLIENT_B_ID, fingerprint=fp)
            .one()
        )
    assert a_row.id != b_row.id
    assert a_row.roles["name_col"] == 0  # A's mapping untouched by B's save
    assert b_row.roles["name_col"] == 3


def test_template_profile_save_requires_tenant() -> None:
    res = _put_profile_as(
        _user_no_client,
        {"fingerprint": "fp", "product_code": "GBT", "roles": {"name_col": 0}},
    )
    assert res.status_code == 400
    assert "active client" in res.text.lower()


# ── Member accounts (employee-portal provisioning) ──────────────────────────
MEMBER_ACC_B = "00000000-0000-0000-0000-0000000000ba"


def _ensure_member_account_b() -> None:
    """Idempotently create a portal member account owned by client B."""
    from app.models import MemberAccount

    with SessionLocal() as s:
        if s.get(MemberAccount, MEMBER_ACC_B) is None:
            s.add(
                MemberAccount(
                    id=MEMBER_ACC_B,
                    client_id=CLIENT_B_ID,
                    email="member@client-b.test",
                    staff_id="B-STAFF-1",
                    status="active",
                )
            )
            s.commit()


def test_member_account_create_for_b_employee_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/employees/{EMP_B}/member-account", json={"email": "x@y.test"}
    )
    assert res.status_code == 404


def test_member_account_list_excludes_other_tenant(client_as_a: TestClient) -> None:
    _ensure_member_account_b()
    res = client_as_a.get("/api/v1/member-accounts")
    assert res.status_code == 200
    ids = {item["id"] for item in res.json()["items"]}
    assert MEMBER_ACC_B not in ids


def test_member_account_patch_cross_tenant_404(client_as_a: TestClient) -> None:
    _ensure_member_account_b()
    res = client_as_a.patch(
        f"/api/v1/member-accounts/{MEMBER_ACC_B}", json={"status": "disabled"}
    )
    assert res.status_code == 404


def test_member_account_resend_invite_cross_tenant_404(client_as_a: TestClient) -> None:
    _ensure_member_account_b()
    res = client_as_a.post(f"/api/v1/member-accounts/{MEMBER_ACC_B}/resend-invite")
    assert res.status_code == 404


def test_member_account_bulk_invite_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        "/api/v1/member-accounts/bulk-invite", json={"policy_year_id": PY_B}
    )
    assert res.status_code == 404


# ── Claims (employee-portal claims module) ───────────────────────────────────
CLAIM_B = "00000000-0000-0000-0000-0000000000bc"


def _ensure_claim_b() -> None:
    """Idempotently create a claim owned by client B."""
    from datetime import date as _date

    from app.models import Claim

    with SessionLocal() as s:
        if s.get(Claim, CLAIM_B) is None:
            s.add(
                Claim(
                    id=CLAIM_B,
                    client_id=CLIENT_B_ID,
                    policy_year_id=PY_B,
                    employee_id=EMP_B,
                    claim_kind="insured",
                    product_code="GHS",
                    claim_type="outpatient",
                    incurred_date=_date(2026, 6, 1),
                    amount_claimed=100.0,
                    status="submitted",
                )
            )
            s.commit()


def test_claims_list_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/claims?policy_year_id={PY_B}")
    assert res.status_code == 404


def test_claim_get_cross_tenant_404(client_as_a: TestClient) -> None:
    _ensure_claim_b()
    res = client_as_a.get(f"/api/v1/claims/{CLAIM_B}")
    assert res.status_code == 404


def test_claim_decision_cross_tenant_404(client_as_a: TestClient) -> None:
    _ensure_claim_b()
    res = client_as_a.post(
        f"/api/v1/claims/{CLAIM_B}/decision", json={"action": "approve"}
    )
    assert res.status_code == 404


def test_claim_document_download_cross_tenant_404(client_as_a: TestClient) -> None:
    _ensure_claim_b()
    res = client_as_a.get(f"/api/v1/claims/{CLAIM_B}/documents/any-doc/download")
    assert res.status_code == 404


def test_claim_review_cross_tenant_404(client_as_a: TestClient) -> None:
    _ensure_claim_b()
    res = client_as_a.get(f"/api/v1/claims/{CLAIM_B}/review")
    assert res.status_code == 404


def test_claim_rerun_review_cross_tenant_404(client_as_a: TestClient) -> None:
    _ensure_claim_b()
    res = client_as_a.post(f"/api/v1/claims/{CLAIM_B}/rerun-review")
    assert res.status_code == 404


def test_employee_utilization_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/employees/{EMP_B}/utilization")
    assert res.status_code == 404


def test_dependant_approval_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/dependants/{DEP_B}/approval", json={"action": "approve"}
    )
    assert res.status_code == 404


def test_dependant_documents_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/dependants/{DEP_B}/documents")
    assert res.status_code == 404


# ── Panel clinic listings (clinic locator) ───────────────────────────────────
def test_panel_listing_clinics_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/panel-listings/{PANEL_B}/clinics")
    assert res.status_code == 404


def test_panel_listing_download_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/panel-listings/{PANEL_B}/download")
    assert res.status_code == 404


def test_panel_listing_patch_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.patch(
        f"/api/v1/panel-listings/{PANEL_B}", json={"label": "hijack"}
    )
    assert res.status_code == 404


def test_panel_listing_delete_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.delete(f"/api/v1/panel-listings/{PANEL_B}")
    assert res.status_code == 404


def test_panel_listing_upload_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.post(
        f"/api/v1/panel-listings/{PANEL_B}/upload",
        files={"file": ("panel.xlsx", b"stub", "application/octet-stream")},
    )
    assert res.status_code == 404


def test_panel_listing_list_excludes_other_tenant(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/panel-listings")
    assert res.status_code == 200
    assert PANEL_B not in {listing["id"] for listing in res.json()}


def test_policy_year_panels_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/policy-years/{PY_B}/panels")
    assert res.status_code == 404
    res = client_as_a.put(
        f"/api/v1/policy-years/{PY_B}/panels", json={"panel_listing_ids": []}
    )
    assert res.status_code == 404


def test_policy_year_panels_cannot_tag_foreign_listing(client_as_a: TestClient) -> None:
    """Tagging client B's listing onto client A's own policy year must 404."""
    py_a = client_as_a.get("/api/v1/policy-years").json()[0]["id"]
    res = client_as_a.put(
        f"/api/v1/policy-years/{py_a}/panels",
        json={"panel_listing_ids": [PANEL_B]},
    )
    assert res.status_code == 404


def test_portal_preview_clinics_cross_tenant_404(client_as_a: TestClient) -> None:
    res = client_as_a.get(f"/api/v1/employees/{EMP_B}/portal-preview/clinics")
    assert res.status_code == 404


def test_panel_listing_companies_cross_tenant_404(client_as_a: TestClient) -> None:
    """The multi-company enablement endpoints go through load_panel_listing —
    another tenant's PINNED listing stays invisible."""
    res = client_as_a.get(f"/api/v1/panel-listings/{PANEL_B}/companies")
    assert res.status_code == 404
    res = client_as_a.put(
        f"/api/v1/panel-listings/{PANEL_B}/companies", json={"client_ids": []}
    )
    assert res.status_code == 404
