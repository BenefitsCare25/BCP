"""Enrollment window CRUD + leave policy upsert (enrollment Phase 2)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_enrollment_config.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, PolicyYear  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000000c000"
PY_ID = "00000000-0000-0000-0000-00000000c001"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000c0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Cfg Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2028,
            start_date=date(2028, 1, 1), end_date=date(2028, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _window_body(**over) -> dict:
    body = {
        "name": "2028 Open Enrollment",
        "window_type": "open",
        "opens_at": "2027-11-01T00:00:00Z",
        "closes_at": "2027-11-30T00:00:00Z",
        "default_behavior": "deemed_keep_current",
        "allow_leave": True,
    }
    body.update(over)
    return body


# ── Windows ─────────────────────────────────────────────────────────────────
def test_create_list_get_window(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows", json=_window_body()
    )
    assert res.status_code == 201, res.text
    wid = res.json()["id"]
    assert res.json()["status"] == "draft"

    lst = client.get(f"/api/v1/policy-years/{PY_ID}/enrollment-windows")
    assert lst.status_code == 200
    assert any(w["id"] == wid for w in lst.json())

    got = client.get(f"/api/v1/enrollment-windows/{wid}")
    assert got.status_code == 200 and got.json()["allow_leave"] is True


def test_window_flex_config_defaults_and_roundtrip(client: TestClient) -> None:
    # Created without flex config → defaults preserve prior behavior.
    base = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows", json=_window_body(name="DefCfg")
    ).json()
    assert base["flex_drawdown_rule"] == "full"
    assert base["flex_price_source"] is None
    assert base["uses_flex"] is False

    # Created with explicit per-product source + on-change rule → persisted + read.
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
        json=_window_body(
            name="FlexCfg",
            flex_price_source={"prod-gtl": "slip", "prod-ghs": "manual"},
            flex_drawdown_rule="on_change",
        ),
    )
    assert res.status_code == 201, res.text
    wid = res.json()["id"]
    got = client.get(f"/api/v1/enrollment-windows/{wid}").json()
    assert got["flex_drawdown_rule"] == "on_change"
    assert got["flex_price_source"] == {"prod-gtl": "slip", "prod-ghs": "manual"}

    # Patch flips the rule back to full and rewrites the source map.
    patched = client.patch(
        f"/api/v1/enrollment-windows/{wid}",
        json={"flex_drawdown_rule": "full", "flex_price_source": {"prod-gtl": "manual"}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["flex_drawdown_rule"] == "full"
    assert patched.json()["flex_price_source"] == {"prod-gtl": "manual"}


def test_flex_window_fails_closed_when_configuration_is_empty(
    client: TestClient,
) -> None:
    window = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
        json=_window_body(name="Unsafe Flex draft", uses_flex=True),
    ).json()

    readiness = client.get(
        f"/api/v1/enrollment-windows/{window['id']}/readiness"
    )
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    assert {issue["code"] for issue in readiness.json()["issues"]} == {
        "no_products_in_scope"
    }

    opened = client.post(f"/api/v1/enrollment-windows/{window['id']}/open")
    assert opened.status_code == 409
    detail = opened.json()["detail"]
    assert detail["code"] == "enrollment_not_ready"
    assert {issue["code"] for issue in detail["issues"]} == {
        "no_products_in_scope"
    }


def test_window_flex_config_rejects_bad_values(client: TestClient) -> None:
    assert (
        client.post(
            f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
            json=_window_body(flex_drawdown_rule="sometimes"),
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
            json=_window_body(flex_price_source={"prod-gtl": "premium"}),
        ).status_code
        == 422
    )


def test_unified_pricing_save_needs_no_source_choice(client: TestClient) -> None:
    window = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
        json=_window_body(
            name="Unified pricing draft",
            flex_price_source={"legacy-product": "manual"},
        ),
    ).json()
    saved = client.put(
        f"/api/v1/policy-years/{PY_ID}/enrollment-pricing-config",
        json={"pricing": {"products": {}}},
    )
    assert saved.status_code == 200, saved.text
    got = client.get(f"/api/v1/enrollment-windows/{window['id']}").json()
    assert got["flex_price_source"] is None


def test_open_migrates_untouched_legacy_source_map(client: TestClient) -> None:
    window = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
        json=_window_body(
            name="Legacy pricing draft",
            flex_price_source={"legacy-product": "manual"},
        ),
    ).json()

    opened = client.post(f"/api/v1/enrollment-windows/{window['id']}/open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["window"]["flex_price_source"] is None

    closed = client.post(f"/api/v1/enrollment-windows/{window['id']}/close")
    assert closed.status_code == 200, closed.text


def test_create_window_bad_dates_422(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
        json=_window_body(opens_at="2027-12-01T00:00:00Z", closes_at="2027-11-01T00:00:00Z"),
    )
    assert res.status_code == 422


def test_patch_window(client: TestClient) -> None:
    wid = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows", json=_window_body(name="Temp")
    ).json()["id"]
    res = client.patch(
        f"/api/v1/enrollment-windows/{wid}", json={"name": "Renamed", "allow_leave": False}
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Renamed" and res.json()["allow_leave"] is False


def test_delete_draft_window(client: TestClient) -> None:
    wid = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows", json=_window_body(name="Doomed")
    ).json()["id"]
    res = client.delete(f"/api/v1/enrollment-windows/{wid}")
    assert res.status_code == 204
    assert client.get(f"/api/v1/enrollment-windows/{wid}").status_code == 404


# ── Leave policy ─────────────────────────────────────────────────────────────
def test_leave_policy_unset_404(client: TestClient) -> None:
    assert client.get(f"/api/v1/policy-years/{PY_ID}/leave-policy").status_code == 404


def test_leave_policy_upsert_and_read(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/policy-years/{PY_ID}/leave-policy",
        json={"allow_buy": True, "allow_sell": True, "min_buy_days": 0,
              "max_buy_days": 5, "min_sell_days": 0, "max_sell_days": 3,
              "increment_days": 0.5},
    )
    assert res.status_code == 200, res.text
    assert res.json()["max_buy_days"] == 5 and res.json()["increment_days"] == 0.5

    got = client.get(f"/api/v1/policy-years/{PY_ID}/leave-policy")
    assert got.status_code == 200 and got.json()["max_sell_days"] == 3


def test_leave_policy_bad_bounds_422(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/policy-years/{PY_ID}/leave-policy",
        json={"min_buy_days": 5, "max_buy_days": 2},
    )
    assert res.status_code == 422
