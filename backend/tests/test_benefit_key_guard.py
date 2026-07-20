"""Renaming a benefit line must not silently strand claims that reference it.

`Claim.benefit_key` is a NAME STRING (claims.py validates it against the
lowercased SOB item names; utilization.py buckets by the same key), so a rename
in the SOB editor breaks the link with nothing to catch it. These cover the
guard that turns that into a confirmable 409.

This module owns a PRIVATE engine rather than the shared `app.db.session` one.
The suite's other modules share a single SQLite file and assert on absolute row
counts (`scalar_one()`), so seeding rows through the shared engine here made
`test_dependant_participation` fail purely from ordering.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Claim, Client, Employee, PolicyYear
from app.services.benefit_key_guard import (
    orphan_conflict_detail,
    orphaned_benefit_keys,
    schedule_benefit_names,
)

TEST_DB = Path(__file__).parent / "_test_benefit_key_guard.db"
PY_ID = "py-guard-1"
FIRM_ID = "firm-guard-1"
CLIENT_ID = "client-guard-1"


@pytest.fixture(scope="module")
def db():
    TEST_DB.unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{TEST_DB}")
    Base.metadata.create_all(engine)
    session = Session(engine)

    session.add(BrokerFirm(id=FIRM_ID, name="Guard Brokers"))
    session.commit()
    session.add(Client(id=CLIENT_ID, name="Guard Co", broker_firm_id=FIRM_ID))
    session.commit()
    session.add(
        PolicyYear(
            id=PY_ID,
            client_id=CLIENT_ID,
            year=2031,
            start_date=date(2031, 1, 1),
            end_date=date(2031, 12, 31),
        )
    )
    session.commit()
    session.add(
        Employee(
            id="emp-1",
            client_id=CLIENT_ID,
            policy_year_id=PY_ID,
            staff_id="E1",
            employee_name="A",
        )
    )
    session.commit()

    def claim(cid: str, product: str, key: str | None, kind: str) -> Claim:
        return Claim(
            id=cid,
            client_id=CLIENT_ID,
            policy_year_id=PY_ID,
            employee_id="emp-1",
            product_code=product,
            benefit_key=key,
            claim_type=kind,
            incurred_date=date(2031, 3, 1),
            amount_claimed=100.0,
        )

    session.add(claim("clm-0", "GHS", "Daily Room & Board", "hospital"))
    session.add(claim("clm-1", "GHS", "  ICU  ", "hospital"))
    # NULL key: every claim created since intake dropped the benefit picker.
    session.add(claim("clm-2", "GHS", None, "hospital"))
    # A different product must never appear in GHS's orphan list.
    session.add(claim("clm-other", "GD", "Examination", "dental"))
    session.commit()

    yield session

    session.close()
    engine.dispose()
    TEST_DB.unlink(missing_ok=True)


def test_schedule_benefit_names_lowercases_and_drops_blanks():
    names = schedule_benefit_names(
        [
            {"name": "  Daily Room & Board "},
            {"name": ""},
            {"name": None},
            "not a dict",
        ]
    )
    assert names == {"daily room & board"}
    assert schedule_benefit_names(None) == set()


def test_unchanged_schedule_orphans_nothing(db):
    assert (
        orphaned_benefit_keys(
            db,
            policy_year_id=PY_ID,
            product_code="GHS",
            new_items=[{"name": "Daily Room & Board"}, {"name": "ICU"}],
        )
        == []
    )


def test_rename_is_detected_and_reports_the_original_spelling(db):
    # "ICU" renamed to "Intensive Care"; the claim still says "  ICU  ".
    orphaned = orphaned_benefit_keys(
        db,
        policy_year_id=PY_ID,
        product_code="GHS",
        new_items=[{"name": "Daily Room & Board"}, {"name": "Intensive Care"}],
    )
    assert orphaned == ["ICU"]  # trimmed, but the claim's own casing


def test_null_benefit_keys_are_ignored(db):
    orphaned = orphaned_benefit_keys(
        db, policy_year_id=PY_ID, product_code="GHS", new_items=[]
    )
    assert set(orphaned) == {"Daily Room & Board", "ICU"}
    assert None not in orphaned


def test_other_products_and_years_are_out_of_scope(db):
    assert orphaned_benefit_keys(
        db, policy_year_id=PY_ID, product_code="GD", new_items=[]
    ) == ["Examination"]
    assert (
        orphaned_benefit_keys(
            db, policy_year_id="other-year", product_code="GHS", new_items=[]
        )
        == []
    )
    assert (
        orphaned_benefit_keys(db, policy_year_id=PY_ID, product_code=None, new_items=[])
        == []
    )


def test_conflict_detail_is_actionable():
    detail = orphan_conflict_detail(["ICU", "Daily Room & Board"], "GHS")
    assert detail["code"] == "orphaned_benefit_keys"
    assert detail["orphaned_benefit_keys"] == ["ICU", "Daily Room & Board"]
    assert "acknowledge=true" in detail["message"]
    assert "'ICU'" in detail["message"]


def test_conflict_detail_truncates_long_lists():
    detail = orphan_conflict_detail([f"B{i}" for i in range(9)], "GBT")
    assert "and 4 more" in detail["message"]
