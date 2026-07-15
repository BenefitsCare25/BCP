"""Employee vs dependant participation split + the compulsory-dependant flex gate.

A slip Participation cell can scope employees and dependants separately
("Compulsory - Employees / Voluntary - Dependents"). The split is preserved in
``Category.participation_detail`` and drives flex: a **compulsory** dependant is
auto-covered + employer-funded (no member flex draw), while a **voluntary**
dependant is an opt-in that draws flex.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_dependant_participation.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from sqlalchemy import select  # noqa: E402

from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import PolicyYear  # noqa: E402
from app.models.category import Category  # noqa: E402
from app.services.flex_pricing_resolver import (  # noqa: E402
    compulsory_dependant_category_ids,
)
from app.services.placement_slip_parser import parse_participation  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402


def test_parse_splits_employee_and_dependant_participation() -> None:
    s = parse_participation("Compulsory - Employees / Voluntary - Dependents")
    assert s.employee == "compulsory"
    assert s.dependant == "voluntary"


@pytest.fixture(scope="module", autouse=True)
def _db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    Base.metadata.drop_all(bind=engine)


def _cat(cid: str, py_id: str, dependant: str) -> Category:
    return Category(
        id=cid,
        policy_year_id=py_id,
        display_name=f"Cat {cid}",
        raw_description=f"Cat {cid}",
        participation_model="compulsory",
        participation_detail={"employee": "compulsory", "dependant": dependant},
        source="system_generated",
        status="needs_review",
    )


def testcompulsory_dependant_category_ids_filters_correctly() -> None:
    with SessionLocal() as s:
        py_id = s.execute(
            select(PolicyYear.id).where(PolicyYear.client_id == DEMO_CLIENT_ID)
        ).scalar_one()
        s.add(_cat("cat-comp", py_id, "compulsory"))
        s.add(_cat("cat-vol", py_id, "voluntary"))
        s.commit()
        got = compulsory_dependant_category_ids(s, {"cat-comp", "cat-vol"})
    # Only the compulsory-dependant category draws no member flex.
    assert got == {"cat-comp"}


def test_empty_input_returns_empty() -> None:
    with SessionLocal() as s:
        assert compulsory_dependant_category_ids(s, set()) == set()
