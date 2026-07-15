"""Coverage revert + history + enrollment reset (the 'track / reset' flexibility).

Covers the three new flows:
- ``POST /employees/{id}/coverage/revert`` (target=default | baseline)
- ``GET  /employees/{id}/coverage-history`` (the timeline)
- ``POST /enrollments/{id}/reset`` (discard in-progress elections)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_coverage_revert.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import UTC, date, datetime, timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Employee,
    EmployeePlanOverride,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.employee_plan_override import OverrideSource  # noqa: E402
from app.models.enrollment import EnrollmentStatus  # noqa: E402
from app.models.enrollment_window import WindowStatus  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.coverage_resolver import load_overrides  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-0000000cf000"
PY_ID = "00000000-0000-0000-0000-0000000cf001"
PROD_ID = "00000000-0000-0000-0000-0000000cf002"
CAT_ID = "00000000-0000-0000-0000-0000000cf004"
EMP1 = "00000000-0000-0000-0000-0000000cf005"
WINDOW_ID = "00000000-0000-0000-0000-0000000cf010"
ENROLL_ID = "00000000-0000-0000-0000-0000000cf011"
USER_ID = "00000000-0000-0000-0000-0000000cf0ff"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Revert Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2031,
            start_date=date(2031, 1, 1), end_date=date(2031, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        s.add(Product(id=PROD_ID, client_id=CLIENT_ID, code="MED",
                      display_name="Medical", insurer="ACME"))
        s.flush()
        for code in ("SILVER", "GOLD"):
            s.add(Plan(id=f"e-plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                       code=code, display_name=code, status="confirmed"))
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="MED cohort", raw_description="MED cohort",
            plan_assignments={"plan_code": "SILVER"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        s.add(Employee(
            id=EMP1, client_id=CLIENT_ID, policy_year_id=PY_ID,
            staff_id="E-1", employee_name="Emp One",
            attribute_values={}, derived_attribute_values={},
            matched_categories=[{"category_id": CAT_ID, "product_code": "MED",
                                 "method": "rule", "confidence": 1.0}],
            source="csv_import", status="active",
        ))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_rows():
    yield
    with SessionLocal() as s:
        s.query(EnrollmentElection).delete()
        s.query(Enrollment).delete()
        s.query(EnrollmentWindow).delete()
        s.query(EmployeePlanOverride).delete()
        s.commit()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _add_override(plan_code: str | None = "GOLD", declined: bool = False) -> None:
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED",
            plan_code=None if declined else plan_code, declined=declined,
            source=OverrideSource.manual_admin, modified_by=USER_ID,
        ))
        s.commit()


def _add_enrollment(baseline: dict) -> None:
    with SessionLocal() as s:
        s.add(EnrollmentWindow(
            id=WINDOW_ID, policy_year_id=PY_ID, client_id=CLIENT_ID, name="OE",
            opens_at=datetime.now(UTC) - timedelta(days=1),
            closes_at=datetime.now(UTC) + timedelta(days=7),
            status=WindowStatus.open,
        ))
        s.flush()
        s.add(Enrollment(
            id=ENROLL_ID, window_id=WINDOW_ID, policy_year_id=PY_ID,
            client_id=CLIENT_ID, employee_id=EMP1,
            status=EnrollmentStatus.in_progress, baseline_snapshot=baseline,
        ))
        s.commit()


# ── Revert to default ───────────────────────────────────────────────────────


def test_revert_to_default_drops_override(client: TestClient) -> None:
    _add_override(plan_code="GOLD")
    res = client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["target"] == "default"
    assert body["changes"][0]["outcome"] == "reset_to_default"
    assert body["changes"][0]["to_plan"] == "SILVER"
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1]) == {}


def test_revert_to_default_scoped_by_product(client: TestClient) -> None:
    _add_override(plan_code="GOLD")
    # Reverting an unrelated product leaves MED untouched.
    res = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert",
        json={"target": "default", "product_codes": ["DENTAL"]},
    )
    assert res.status_code == 200
    assert res.json()["changes"][0]["outcome"] == "unchanged"
    with SessionLocal() as s:
        assert (EMP1, PROD_ID) in load_overrides(s, PY_ID, [EMP1])


# ── Revert to baseline ──────────────────────────────────────────────────────


def test_revert_to_baseline_default_state_removes_override(client: TestClient) -> None:
    # Baseline == cohort default (SILVER) → reverting drops the GOLD override.
    _add_enrollment({"products": {"MED": {
        "plan_code": "SILVER", "tier_category_id": CAT_ID, "declined": False,
        "covered_dependant_ids": None, "compulsory": False,
    }}})
    _add_override(plan_code="GOLD")
    res = client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "baseline"})
    assert res.status_code == 200, res.text
    assert res.json()["changes"][0]["outcome"] == "reverted"
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1]) == {}


def test_revert_to_baseline_nondefault_writes_override(client: TestClient) -> None:
    # Baseline = GOLD (richer than the SILVER default); the member currently sits
    # at default (no override). Reverting writes a GOLD override.
    _add_enrollment({"products": {"MED": {
        "plan_code": "GOLD", "tier_category_id": CAT_ID, "declined": False,
        "covered_dependant_ids": None, "compulsory": False,
    }}})
    res = client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "baseline"})
    assert res.status_code == 200, res.text
    assert res.json()["changes"][0]["to_plan"] == "GOLD"
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.plan_code == "GOLD"
        assert ov.source == OverrideSource.manual_admin


def test_revert_to_baseline_without_enrollment_409(client: TestClient) -> None:
    _add_override(plan_code="GOLD")
    res = client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "baseline"})
    assert res.status_code == 409


# ── Coverage history (track) ────────────────────────────────────────────────


def test_coverage_history_records_changes(client: TestClient) -> None:
    # A manual override then a revert → two newest-first timeline entries.
    client.put(f"/api/v1/employees/{EMP1}/plan-overrides/MED", json={"plan_code": "GOLD"})
    client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"})

    res = client.get(f"/api/v1/employees/{EMP1}/coverage-history")
    assert res.status_code == 200, res.text
    entries = res.json()["entries"]
    assert len(entries) >= 2
    # Both events are recorded against this member, tagged to the product. (Strict
    # newest-first position isn't asserted: func.now() is second-resolution on
    # SQLite so same-second events tie; Postgres has sub-second precision.)
    actions = {e["action"] for e in entries}
    assert "revert_coverage_to_default" in actions
    assert "set_plan_override" in actions
    assert all(e["product_code"] == "MED" for e in entries if e["product_code"])
    # The actor display name resolves (falls back to id when no User row).
    assert all("actor" in e for e in entries)


# ── Enrollment reset (discard in-progress elections) ────────────────────────


def test_reset_enrollment_clears_elections(client: TestClient) -> None:
    _add_enrollment({"products": {}})
    with SessionLocal() as s:
        s.add(EnrollmentElection(
            enrollment_id=ENROLL_ID, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", elected_plan_code="GOLD",
            action="upgrade",
        ))
        s.commit()
    res = client.post(f"/api/v1/enrollments/{ENROLL_ID}/reset")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == EnrollmentStatus.not_started
    with SessionLocal() as s:
        assert s.query(EnrollmentElection).filter_by(enrollment_id=ENROLL_ID).count() == 0


def test_reset_finalized_enrollment_409(client: TestClient) -> None:
    _add_enrollment({"products": {}})
    with SessionLocal() as s:
        s.get(Enrollment, ENROLL_ID).status = EnrollmentStatus.confirmed
        s.commit()
    res = client.post(f"/api/v1/enrollments/{ENROLL_ID}/reset")
    assert res.status_code == 409


# ── has_baseline flag + edge cases ──────────────────────────────────────────


def test_has_baseline_flag(client: TestClient) -> None:
    # No enrollment → flag is False (UI disables 'Revert to baseline').
    res = client.get(f"/api/v1/employees/{EMP1}/coverage-history")
    assert res.status_code == 200
    assert res.json()["has_baseline"] is False
    # With an enrollment snapshot → True.
    _add_enrollment({"products": {}})
    res = client.get(f"/api/v1/employees/{EMP1}/coverage-history")
    assert res.json()["has_baseline"] is True


def test_revert_to_default_records_destination_plan(client: TestClient) -> None:
    _add_override(plan_code="GOLD")
    client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"})
    res = client.get(f"/api/v1/employees/{EMP1}/coverage-history")
    entry = next(
        e for e in res.json()["entries"] if e["action"] == "revert_coverage_to_default"
    )
    # The timeline shows the destination (cohort default), not a blank target.
    assert entry["to_plan"] == "SILVER"


def test_revert_baseline_skips_out_of_baseline_override(client: TestClient) -> None:
    # Baseline snapshot has no products, but the member carries a MED override
    # (e.g. it entered the cohort after window-open). Revert must surface it as
    # 'skipped' and leave it in place rather than silently ignore it.
    _add_enrollment({"products": {}})
    _add_override(plan_code="GOLD")
    res = client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "baseline"})
    assert res.status_code == 200, res.text
    changes = res.json()["changes"]
    skipped = [c for c in changes if c["outcome"] == "skipped"]
    assert any(c["product_code"] == "MED" for c in skipped)
    with SessionLocal() as s:  # override untouched
        assert (EMP1, PROD_ID) in load_overrides(s, PY_ID, [EMP1])


def test_bulk_update_appears_in_coverage_history(client: TestClient) -> None:
    # A bulk plan change writes a per-employee audit row, so it shows in the
    # member's timeline (not only the batch record).
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1]}},
    )
    assert res.status_code == 200, res.text
    hist = client.get(f"/api/v1/employees/{EMP1}/coverage-history").json()["entries"]
    bulk = [e for e in hist if e["action"] == "bulk_plan_override"]
    assert bulk and bulk[0]["product_code"] == "MED"
