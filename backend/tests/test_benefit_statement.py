"""Employee benefit-statement assembly + endpoint.

Builds a minimal world (two products — one with dependant coverage, one without —
each with a plan carrying a Schedule of Benefits, plus a matched employee with a
spouse dependant) and asserts:
  * one coverage line per matched product, each carrying its SOB
  * dependant coverage is derived only for the has_dependants product
  * an unmatched employee yields is_matched=False / empty coverage
  * NO premium/financial values ever appear in the response (benefits-only)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_benefit_statement.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Category, Dependant, Employee, Plan, Product  # noqa: E402
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.services.benefit_statement import build_benefit_statement  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

_SOB = {"items": [{"number": "1", "name": "Daily Room & Board",
                   "value": "SGD 300/day", "note": None, "limits": [], "sub_items": []}]}

EMP_MATCHED = "00000000-0000-0000-0000-0000000000f1"
EMP_UNMATCHED = "00000000-0000-0000-0000-0000000000f2"
MED_CAT = "00000000-0000-0000-0000-0000000000f3"
LIFE_CAT = "00000000-0000-0000-0000-0000000000f4"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()

    with SessionLocal() as s:
        py_id = s.query(Employee.policy_year_id).first()
        # Fall back to any seeded policy year if no employees seeded.
        if py_id is None:
            from app.models import PolicyYear
            py_id = (s.query(PolicyYear.id).filter(PolicyYear.client_id == DEMO_CLIENT_ID)
                     .first())
        py_id = py_id[0]

        med = Product(client_id=DEMO_CLIENT_ID, code="TMED", display_name="Test Medical",
                      has_dependants=True)
        life = Product(client_id=DEMO_CLIENT_ID, code="TLIFE", display_name="Test Life",
                       has_dependants=False)
        s.add_all([med, life])
        s.flush()

        s.add_all([
            Plan(product_id=med.id, policy_year_id=py_id, code="1",
                 display_name="Med Plan 1", benefit_schedule=_SOB,
                 annual_policy_limit="SGD 1,000,000", cover_description="Inpatient cover"),
            Plan(product_id=life.id, policy_year_id=py_id, code="A",
                 display_name="Life Plan A", benefit_schedule=_SOB),
        ])

        s.add_all([
            Category(
                id=MED_CAT, policy_year_id=py_id, product_id=med.id, priority=1,
                display_name="Grade 18 and Eligible Dependants",
                raw_description="Grade 18 and Eligible Dependants",
                rule_human_readable="grade >= 18",
                # rate_tiers (a financial field) is read only to derive coverage; it
                # must never surface in the statement.
                plan_assignments={"plan_code": "1",
                                  "rate_tiers": {"EO": {"rate": 1.0, "premium": 1.0},
                                                 "ES": {"rate": 2.0, "premium": 2.0}}},
                source=SourceKind.system_generated.value,
                status=CategoryStatus.needs_review.value, human_modified=False,
            ),
            Category(
                id=LIFE_CAT, policy_year_id=py_id, product_id=life.id, priority=1,
                display_name="Grade 18 Term Life",
                raw_description="Grade 18 Term Life",
                rule_human_readable="grade >= 18",
                # Per-member basis 500k vs group total 5M, flat per-mille rate:
                # the statement must surface the PER-MEMBER figures, not the group.
                plan_assignments={"plan_code": "A", "basis": "500000.0",
                                  "sum_insured": 5000000.0, "premium_rate": 1.62,
                                  "rate_basis": "per_1000_si", "num_employees": 10},
                source=SourceKind.system_generated.value,
                status=CategoryStatus.needs_review.value, human_modified=False,
            ),
        ])

        emp = Employee(
            id=EMP_MATCHED, client_id=DEMO_CLIENT_ID, policy_year_id=py_id,
            staff_id="BS-001", employee_name="Statement Tester",
            attribute_values={"category": "18 and above", "pass": "WP"},
            derived_attribute_values={"grade": 18, "class": "PROFESSIONAL"},
            matched_category_id=MED_CAT, match_method="rule", match_confidence=0.85,
            matched_categories=[
                {"category_id": MED_CAT, "product_code": "TMED",
                 "method": "rule", "confidence": 0.85},
                {"category_id": LIFE_CAT, "product_code": "TLIFE",
                 "method": "rule", "confidence": 0.85},
            ],
            source="csv_import", status="active",
        )
        s.add(emp)
        s.add(Employee(
            id=EMP_UNMATCHED, client_id=DEMO_CLIENT_ID, policy_year_id=py_id,
            staff_id="BS-002", employee_name="No Coverage",
            attribute_values={"category": "Apprentice"},
            derived_attribute_values={"class": "APPRENTICE"},
            matched_categories=None, source="csv_import", status="active",
        ))
        s.flush()
        s.add(Dependant(
            client_id=DEMO_CLIENT_ID, policy_year_id=py_id, employee_id=EMP_MATCHED,
            attribute_values={"name": "Pat Tester", "relationship": "spouse"},
            link_method="staff_id", status="active",
        ))
        s.commit()

    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_matched_statement_has_one_line_per_product_with_sob() -> None:
    with SessionLocal() as s:
        emp = s.get(Employee, EMP_MATCHED)
        st = build_benefit_statement(s, emp)
    assert st.is_matched is True
    assert [c.product_code for c in st.coverage] == ["TLIFE", "TMED"]  # sorted
    for line in st.coverage:
        assert line.benefit_schedule and line.benefit_schedule["items"]
        assert line.match_method == "rule"
        assert line.rule_human_readable == "grade >= 18"


def test_dependant_coverage_only_for_has_dependants_product() -> None:
    with SessionLocal() as s:
        emp = s.get(Employee, EMP_MATCHED)
        st = build_benefit_statement(s, emp)
    by_code = {c.product_code: c for c in st.coverage}
    med, life = by_code["TMED"], by_code["TLIFE"]
    assert med.covers_dependants is True
    assert [d.relationship for d in med.covered_dependants] == ["spouse"]
    assert life.covers_dependants is False
    assert life.covered_dependants == []
    # The employee's dependants are always listed at the top level too.
    assert len(st.dependants) == 1


def test_key_attributes_surface_with_labels() -> None:
    with SessionLocal() as s:
        emp = s.get(Employee, EMP_MATCHED)
        st = build_benefit_statement(s, emp)
    keys = {a.key: a.value for a in st.attributes}
    assert keys.get("grade") == "18"
    assert keys.get("category") == "18 and above"


def test_unmatched_employee_is_empty() -> None:
    with SessionLocal() as s:
        emp = s.get(Employee, EMP_UNMATCHED)
        st = build_benefit_statement(s, emp)
    assert st.is_matched is False
    assert st.coverage == []


def test_endpoint_returns_statement(client: TestClient) -> None:
    res = client.get(f"/api/v1/employees/{EMP_MATCHED}/benefit-statement")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["employee"]["staff_id"] == "BS-001"
    assert body["is_matched"] is True
    assert len(body["coverage"]) == 2
    assert len(body["dependants"]) == 1


def test_endpoint_exposes_per_member_financials(client: TestClient) -> None:
    """Each coverage line surfaces the member's PER-MEMBER Amount Covered + premium
    (basis / 1000 x rate), never the group sum-insured / total premium."""
    body = client.get(f"/api/v1/employees/{EMP_MATCHED}/benefit-statement").json()
    life = next(c for c in body["coverage"] if c["product_code"] == "TLIFE")
    fin = life["financials"]
    assert fin is not None
    # Per-member basis (500k), NOT the 5,000,000 group total stored on the category.
    assert fin["sum_insured"] == 500000.0
    # 500000 / 1000 * 1.62 — the per-member premium, not num_employees x that.
    assert fin["annual_premium"] == 810.0

    # A tiered line that can't reduce to a per-member sum assured (no basis, only
    # group rate_tiers) must NOT surface — else the group rate table / sum insured
    # would leak as if it were the member's.
    med = next(c for c in body["coverage"] if c["product_code"] == "TMED")
    assert med["financials"] is None
