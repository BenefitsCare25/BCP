"""Template memory: fingerprinting, role overrides, and the save endpoint.

A broker's column-mapping correction is stored per (tenant, template
fingerprint) and reused on later uploads — overriding the content profiler.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_slip_template.db"
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
from app.models import Client  # noqa: E402
from app.services.placement_slip_parser import (  # noqa: E402
    Cell,
    _extract_plans_from_sheet,
    _SobRoles,
    roles_from_dict,
    roles_to_dict,
    sob_template_fingerprint,
)
from app.services.slip_template_memory import (  # noqa: E402
    make_resolver,
    normalize_roles,
)
from scripts.seed_demo import seed  # noqa: E402

CLIENT_B_ID = "00000000-0000-0000-0000-0000000000c7"


def _pad(rows: list[list[Cell]]) -> list[list[Cell]]:
    width = max(len(r) for r in rows)
    return [list(r) + [""] * (width - len(r)) for r in rows]


# ── Unit: fingerprint + roles serialization (no DB) ──────────────────────────


def test_fingerprint_is_stable_and_product_sensitive() -> None:
    rows = _pad([
        ["SCHEDULE OF BENEFITS / INSURER / PLAN", "", "", "", "", "", 1.0, 2.0],
        ["Accidental Death", "", "", "", "", "", 1000.0, 500.0],
    ])
    fp1 = sob_template_fingerprint(rows, "GBT", "Chubb")
    fp2 = sob_template_fingerprint(rows, "GBT", "Chubb")
    assert fp1 and fp1 == fp2  # deterministic
    assert sob_template_fingerprint(rows, "GPA", "Chubb") != fp1  # product matters
    assert sob_template_fingerprint(rows, "GBT", "Zurich") != fp1  # insurer matters


def test_fingerprint_none_without_schedule() -> None:
    assert sob_template_fingerprint(_pad([["Rate :", "Plan"]]), "GBT", None) is None


def test_roles_dict_roundtrip() -> None:
    r = _SobRoles(
        name_col=1, key_col=0, allow_letter_keys=True, value_col=None,
        name_first=False, confidence=0.9,
    )
    assert roles_from_dict(roles_to_dict(r)) == r


def test_roles_from_partial_dict_rederives_name_first() -> None:
    # Only the columns the broker set; name_first inferred (no key + name at col0).
    r = roles_from_dict({"name_col": 0, "value_col": 6})
    assert r.key_col is None and r.name_first is True and r.name_col == 0


def test_roles_from_dict_tolerates_null_name_col() -> None:
    # The broker can clear the name-column input (sends name_col: null). This must
    # not raise int(None) — it falls back to 0, the conventional first column.
    r = roles_from_dict({"name_col": None, "key_col": None, "value_col": 6})
    assert r.name_col == 0
    # And normalize_roles (the save path) must not crash on the same payload.
    assert normalize_roles({"name_col": None, "value_col": 6})["name_col"] == 0


def test_normalize_roles_drops_unknown_keys() -> None:
    out = normalize_roles({"name_col": 1, "key_col": 0, "evil": "x", "value_col": 4})
    assert "evil" not in out and out["name_col"] == 1 and out["value_col"] == 4


def test_roles_override_changes_extracted_value() -> None:
    # Descriptive sheet: col3 and col4 both hold candidate values. The profiler
    # picks the leftmost (col3); an override forcing value_col=4 must win.
    rows = _pad([
        ["SCHEDULE OF BENEFITS / DEFINITION"],
        ["", "Death Benefit", "", "from col3", "from col4"],
        ["", "TPD Benefit", "", "c3", "c4"],
    ])
    auto = _extract_plans_from_sheet(rows)
    assert auto[0].items[0].value == "from col3"

    override = _SobRoles(
        name_col=1, key_col=None, allow_letter_keys=False, value_col=4,
        name_first=False, confidence=1.0,
    )
    forced = _extract_plans_from_sheet(rows, roles_override=override)
    assert forced[0].items[0].value == "from col4"


# ── DB / API: save endpoint + resolver + tenant isolation ────────────────────


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    db = SessionLocal()
    try:
        if db.get(Client, CLIENT_B_ID) is None:
            db.add(Client(id=CLIENT_B_ID, name="Client B", broker_firm_id=DEMO_BROKER_FIRM_ID))
            db.commit()
    finally:
        db.close()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


def _admin_a() -> CurrentUser:
    return CurrentUser(
        user_id="11111111-1111-1111-1111-111111111111",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=DEMO_CLIENT_ID, role="broker_admin",
    )


def _admin_b() -> CurrentUser:
    return CurrentUser(
        user_id="22222222-2222-2222-2222-222222222222",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_B_ID, role="broker_admin",
    )


def _save_as(user_factory, payload: dict) -> object:
    tc = TestClient(app)
    try:
        app.dependency_overrides[get_current_user] = user_factory
        return tc.put("/api/v1/placement-slips/template-profiles", json=payload)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_save_endpoint_persists_and_resolver_reuses() -> None:
    payload = {
        "fingerprint": "abc123",
        "product_code": "GBT",
        "insurer": "Chubb",
        "sheet_label": "Chubb-GBT",
        "roles": {"name_col": 0, "value_col": 6, "evil_key": "dropped"},
    }
    res = _save_as(_admin_a, payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["fingerprint"] == "abc123"
    assert "evil_key" not in body["roles"]  # normalized server-side
    assert body["roles"]["name_col"] == 0

    db = SessionLocal()
    try:
        resolved = make_resolver(db, DEMO_CLIENT_ID)("abc123")
        assert resolved is not None and resolved["value_col"] == 6
        # Other tenant cannot see it.
        assert make_resolver(db, CLIENT_B_ID)("abc123") is None
    finally:
        db.close()


def test_save_endpoint_upserts_same_fingerprint() -> None:
    base = {"fingerprint": "dup1", "product_code": "GHS", "roles": {"name_col": 1}}
    assert _save_as(_admin_a, base).status_code == 200
    updated = {**base, "roles": {"name_col": 2, "value_col": 5}}
    assert _save_as(_admin_a, updated).status_code == 200
    db = SessionLocal()
    try:
        from app.models import SlipTemplateProfile

        rows = (
            db.query(SlipTemplateProfile)
            .filter_by(client_id=DEMO_CLIENT_ID, fingerprint="dup1")
            .all()
        )
        assert len(rows) == 1 and rows[0].roles["name_col"] == 2
    finally:
        db.close()
