"""Product-wide voluntary age-band rate table config.

One age-band table per life product, shared by all its voluntary plans; editing
it fans the write out to every age-banded voluntary category and leaves the
compulsory baseline (and flat voluntary options) untouched.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_voluntary_rates.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Category, Client, PolicyYear, Product  # noqa: E402
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000000f100"
PY_ID = "00000000-0000-0000-0000-00000000f101"
LIFE_ID = "00000000-0000-0000-0000-00000000f102"
PA_ID = "00000000-0000-0000-0000-00000000f103"
COMP = "cat-vr-comp"
VOL_UP = "cat-vr-up"
VOL_DOWN = "cat-vr-down"
PA_VOL = "cat-vr-pa-flat"

_BANDS = [
    {"label": "34 & below", "min": None, "max": 34, "rate": 0.88},
    {"label": "35 to 44", "min": 35, "max": 44, "rate": 1.32},
]


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000f1ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


def _cat(cid, pid, name, part, pa):
    return Category(
        id=cid, policy_year_id=PY_ID, product_id=pid, priority=1,
        display_name=name, raw_description=name,
        participation_model=part, plan_assignments=pa,
        source=SourceKind.system_generated.value,
        status=CategoryStatus.confirmed.value, human_modified=False,
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="VR Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2032,
            start_date=date(2032, 1, 1), end_date=date(2032, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.add(Product(id=LIFE_ID, client_id=CLIENT_ID, code="VL", display_name="Life"))
        s.add(Product(id=PA_ID, client_id=CLIENT_ID, code="VPA", display_name="PA"))
        s.flush()
        s.add(_cat(COMP, LIFE_ID, "Exec", "compulsory", {
            "plan_code": "1", "basis": "1000000.0", "sum_insured": 2000000.0,
            "premium_rate": 1.62, "rate_basis": "per_1000_si", "num_employees": 2,
        }))
        s.add(_cat(VOL_UP, LIFE_ID, "Exec", "voluntary", {
            "plan_code": "10", "basis": "500000.0", "rate_basis": "age_banded",
            "voluntary_rates": _BANDS,
        }))
        s.add(_cat(VOL_DOWN, LIFE_ID, "Exec", "voluntary", {
            "plan_code": "17", "basis": "250000.0", "rate_basis": "age_banded",
            "voluntary_rates": _BANDS,
        }))
        # A flat voluntary option (GPA-style) — NOT age-banded, must be untouched.
        s.add(_cat(PA_VOL, PA_ID, "CEO Option", "voluntary", {
            "plan_code": "1", "basis": "500000.0", "sum_insured": 500000.0,
            "premium_rate": 0.5, "rate_basis": "per_1000_si",
        }))
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


def test_get_returns_shared_table(client: TestClient) -> None:
    res = client.get(f"/api/v1/policy-years/{PY_ID}/products/{LIFE_ID}/voluntary-rates")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["voluntary_plan_count"] == 2
    assert [b["rate"] for b in body["bands"]] == [0.88, 1.32]


def test_get_404_when_no_age_banded_voluntary(client: TestClient) -> None:
    # The PA product's voluntary option is flat (no age-band table).
    res = client.get(f"/api/v1/policy-years/{PY_ID}/products/{PA_ID}/voluntary-rates")
    assert res.status_code == 404


def test_put_fans_out_and_leaves_others_untouched(client: TestClient) -> None:
    new_bands = [
        {"label": "34 & below", "min": None, "max": 34, "rate": 1.0},
        {"label": "35 to 44", "min": 35, "max": 44, "rate": 2.0},
        {"label": "45 to 54", "min": 45, "max": 54, "rate": 3.0},
    ]
    res = client.put(
        f"/api/v1/policy-years/{PY_ID}/products/{LIFE_ID}/voluntary-rates",
        json={"bands": new_bands},
    )
    assert res.status_code == 200, res.text
    assert res.json()["voluntary_plan_count"] == 2

    with SessionLocal() as s:
        for cid in (VOL_UP, VOL_DOWN):
            pa = s.get(Category, cid).plan_assignments
            assert [b["rate"] for b in pa["voluntary_rates"]] == [1.0, 2.0, 3.0]
            assert pa["rate_basis"] == "age_banded"
            # Group/flat fields never reappear on an age-banded tier.
            assert "premium_rate" not in pa and "sum_insured" not in pa
            assert pa["basis"] == "500000.0" or pa["basis"] == "250000.0"
        # Compulsory baseline keeps its flat rate + group figures.
        comp = s.get(Category, COMP).plan_assignments
        assert comp["premium_rate"] == 1.62
        assert "voluntary_rates" not in comp
        # The flat PA voluntary option is untouched (not age-banded).
        pa_opt = s.get(Category, PA_ID and PA_VOL).plan_assignments
        assert pa_opt["premium_rate"] == 0.5
        assert "voluntary_rates" not in pa_opt


def test_put_rejects_empty_and_bad_bands(client: TestClient) -> None:
    assert client.put(
        f"/api/v1/policy-years/{PY_ID}/products/{LIFE_ID}/voluntary-rates",
        json={"bands": []},
    ).status_code == 422
    assert client.put(
        f"/api/v1/policy-years/{PY_ID}/products/{LIFE_ID}/voluntary-rates",
        json={"bands": [{"label": "bad", "min": 50, "max": 40, "rate": 1.0}]},
    ).status_code == 422
