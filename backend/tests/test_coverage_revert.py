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
        # Every revert now records a batch (that is what makes it undoable), so
        # these accumulate across tests unless cleared — and a test asserting on
        # the year's batch list would then see the previous test's reverts.
        from app.models.bulk_plan_update import BulkPlanUpdate

        s.query(BulkPlanUpdate).delete()
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


# ── The underwriting resync must not read pre-delete coverage ────────────────
#
# `SessionLocal` is built `autoflush=False`, and `refresh_underwriting_cases`
# re-reads effective coverage with its own `select()`. A PENDING `db.delete()`
# is therefore invisible to it, so the NEL case for coverage just removed is
# re-derived as still in force and never retired — and because undecided cases
# hold a guaranteed-SI snapshot nothing else re-reads, the queue stays wrong
# until some unrelated trigger fires.


def _spy_on_resync(monkeypatch) -> dict:
    """Capture the overrides visible to the resync at the moment it runs."""
    import app.api.v1.plan_overrides as mod

    seen: dict = {}

    def spy(db, py, employee_ids=None):
        seen["overrides"] = load_overrides(db, PY_ID, [EMP1])

    monkeypatch.setattr(mod, "refresh_underwriting_cases", spy)
    return seen


def test_resync_does_not_see_a_deleted_override(client: TestClient, monkeypatch) -> None:
    _add_override(plan_code="GOLD")
    seen = _spy_on_resync(monkeypatch)
    assert client.delete(f"/api/v1/employees/{EMP1}/plan-overrides/MED").status_code == 204
    assert (EMP1, PROD_ID) not in seen["overrides"]


def test_resync_does_not_see_an_override_dropped_by_revert(
    client: TestClient, monkeypatch
) -> None:
    """`revert_to_default` only deletes — it never flushes — so this path relied
    entirely on the helper's own flush."""
    _add_override(plan_code="GOLD")
    seen = _spy_on_resync(monkeypatch)
    res = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"}
    )
    assert res.status_code == 200, res.text
    assert (EMP1, PROD_ID) not in seen["overrides"]


# ── A per-member revert is undoable, through the bulk undo endpoint ──────────
#
# Reverting deletes overrides outright. The bulk path — doing the same thing to
# many members at once — has had undo all along; this closes the gap without a
# second restore mechanism, which is what let the two paths drift apart on
# underwriting in the first place.


def test_revert_to_default_is_undoable(client: TestClient) -> None:
    _add_override(plan_code="GOLD")
    res = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"}
    )
    assert res.status_code == 200, res.text
    batch_id = res.json()["batch_id"]
    assert batch_id
    with SessionLocal() as s:  # gone
        assert (EMP1, PROD_ID) not in load_overrides(s, PY_ID, [EMP1])

    undo = client.post(f"/api/v1/bulk-plan-updates/{batch_id}/undo", json={})
    assert undo.status_code == 200, undo.text
    with SessionLocal() as s:  # back, with its plan
        ov = load_overrides(s, PY_ID, [EMP1]).get((EMP1, PROD_ID))
        assert ov is not None and ov.plan_code == "GOLD"


def test_revert_to_baseline_is_undoable(client: TestClient) -> None:
    _add_enrollment({"products": {"MED": {
        "plan_code": "GOLD", "tier_category_id": CAT_ID, "declined": False,
    }}})
    _add_override(plan_code="SILVER")
    res = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "baseline"}
    )
    assert res.status_code == 200, res.text
    batch_id = res.json()["batch_id"]
    assert batch_id
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "GOLD"

    assert client.post(
        f"/api/v1/bulk-plan-updates/{batch_id}/undo", json={}
    ).status_code == 200
    with SessionLocal() as s:  # the pre-revert override is restored
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "SILVER"


def test_a_no_op_revert_records_no_batch(client: TestClient) -> None:
    """Nothing changed → nothing to undo. A record here would offer an Undo
    button that does nothing."""
    res = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"}
    )
    assert res.status_code == 200
    assert res.json()["batch_id"] is None


def test_undo_refuses_to_clobber_a_later_change(client: TestClient) -> None:
    """`undo_batch`'s superseded detection has to apply here too — the whole
    reason for reusing it rather than writing a second restore path."""
    _add_override(plan_code="GOLD")
    batch_id = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"}
    ).json()["batch_id"]
    _add_override(plan_code="SILVER")  # somebody moves them again afterwards

    undo = client.post(f"/api/v1/bulk-plan-updates/{batch_id}/undo", json={})
    assert undo.status_code == 200, undo.text
    assert undo.json()["counts"].get("skipped") == 1
    with SessionLocal() as s:  # the later change stands
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "SILVER"


def test_revert_batch_is_listed_but_not_re_runnable(client: TestClient) -> None:
    """A revert IS a coverage change and belongs in the year's history — but it
    names no product, so replaying it as a selection would 404. The flag is what
    lets the history offer Undo without offering Re-run."""
    _add_override(plan_code="GOLD")
    batch_id = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"}
    ).json()["batch_id"]

    rows = client.get(f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates").json()
    row = next(r for r in rows if r["id"] == batch_id)
    assert row["is_revert"] is True
    assert row["restorable"] == 1  # undo still has something to put back
    # A real bulk batch is unaffected.
    assert all(not r["is_revert"] for r in rows if r["id"] != batch_id)


def test_the_batch_records_which_revert_it_was(client: TestClient) -> None:
    """It used to hardcode `revert_to_default`, so a baseline revert was stored
    as the wrong action."""
    _add_enrollment({"products": {"MED": {
        "plan_code": "GOLD", "tier_category_id": CAT_ID, "declined": False,
    }}})
    _add_override(plan_code="SILVER")
    batch_id = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "baseline"}
    ).json()["batch_id"]
    with SessionLocal() as s:
        from app.models.bulk_plan_update import BulkPlanUpdate

        assert s.get(BulkPlanUpdate, batch_id).action == "revert_to_baseline"


def test_undoing_a_revert_shows_in_the_coverage_timeline(client: TestClient) -> None:
    """The Undo button lives inside the card that renders this timeline, so an
    undo missing from it left the history contradicting the coverage above it."""
    _add_override(plan_code="GOLD")
    batch_id = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"}
    ).json()["batch_id"]
    assert client.post(
        f"/api/v1/bulk-plan-updates/{batch_id}/undo", json={}
    ).status_code == 200

    actions = [
        e["action"]
        for e in client.get(f"/api/v1/employees/{EMP1}/coverage-history").json()["entries"]
    ]
    assert "bulk_plan_override_undone" in actions


def test_revert_to_baseline_drops_a_dangling_tier_id(client: TestClient) -> None:
    """A slip re-upload REPLACES categories, so a baseline's `tier_category_id`
    usually points at a row that is gone (87% of them on CDL). Writing that dead
    id back would pin the member to a category that no longer exists — precisely
    what `find_orphan_overrides` exists to flag — so it is read as "no tier
    opinion" and the override is written without one."""
    _add_enrollment({"products": {"MED": {
        "plan_code": "GOLD", "tier_category_id": "deleted-by-a-re-upload",
        "declined": False, "covered_dependant_ids": None,
    }}})
    res = client.post(
        f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "baseline"}
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.plan_code == "GOLD"          # the plan IS restored
        assert ov.tier_category_id is None     # the dead category id is not
