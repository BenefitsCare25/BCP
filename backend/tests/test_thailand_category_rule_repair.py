"""Regression test for the affected Thailand Plan 1 AI mapping."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Category, Client, PolicyYear, Product

_DESCRIPTION = (
    "Officer and All Employees based in Thailand (except for Director) "
    "(Job Category: J1 to J3, JA to JC)"
)
_JOB_RULE = {"in": ["job_category", ["J1", "J2", "J3", "JA", "JB", "JC"]]}
_BAD_RULE = {
    "in": [
        "category",
        ["All Employees based in Thailand (except for Director)", "Officer"],
    ]
}


def test_repair_copies_only_reviewed_sibling_rule() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "b6c9e2d4f7a1_repair_thailand_plan_ai_rule.py"
    )
    spec = importlib.util.spec_from_file_location("repair_thailand_rule", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        db.add(Client(id="client", name="Client", broker_firm_id="firm"))
        db.flush()
        db.add(
            PolicyYear(
                id="year",
                client_id="client",
                year=2026,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
        )
        db.add(
            Product(
                id="product", client_id="client", code="GCGP", display_name="GCGP"
            )
        )
        db.flush()
        bad = Category(
            id="bad",
            policy_year_id="year",
            product_id="product",
            display_name=_DESCRIPTION,
            raw_description=_DESCRIPTION,
            matching_rule=_BAD_RULE,
            rule_validation={
                "state": "needs_review",
                "errors": [
                    "Matching rule omitted explicit employee attribute: job_category"
                ],
                "warnings": ["2 employees also match an equally specific employee cohort"],
                "expected_count": 55,
            },
            rule_status="needs_review",
            status="needs_review",
            source="ai_extracted",
            human_modified=False,
            source_ref="placement_slip://slip/GCGP/row_24",
        )
        reviewed = Category(
            id="reviewed",
            policy_year_id="year",
            product_id="product",
            display_name=_DESCRIPTION,
            raw_description=_DESCRIPTION,
            matching_rule=_JOB_RULE,
            rule_validation={
                "state": "validated",
                "errors": [],
                "warnings": [],
                "matched_count": 55,
            },
            rule_status="validated",
            status="needs_review",
            source="manual",
            human_modified=True,
            source_ref="placement_slip://slip/GCGP/row_31",
        )
        untouched = Category(
            id="untouched",
            policy_year_id="year",
            product_id="product",
            display_name="Other",
            raw_description="Other",
            matching_rule=_BAD_RULE,
            rule_status="needs_review",
            status="needs_review",
            source="ai_extracted",
            human_modified=False,
            source_ref="placement_slip://slip/GCGP/row_25",
        )
        db.add_all([bad, reviewed, untouched])
        db.commit()

    with engine.begin() as connection:
        assert migration._repair_schema(connection, None) == 1
        assert migration._repair_schema(connection, None) == 0

    with Session(engine) as db:
        repaired = db.get(Category, "bad")
        assert repaired is not None
        assert repaired.matching_rule == _JOB_RULE
        assert repaired.rule_status == "validated"
        assert repaired.status == "needs_review"
        assert repaired.rule_validation["errors"] == []
        assert repaired.rule_validation["overlap_count"] == 0
        assert repaired.rule_validation["repaired_from_category_id"] == "reviewed"
        assert db.get(Category, "untouched").matching_rule == _BAD_RULE
