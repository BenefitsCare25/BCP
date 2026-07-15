"""Member-facing benefit statement — the broker statement with financials gated off.

`build_benefit_statement` carries per-member premium figures and matching
internals (method/confidence/rule text) that are broker-facing. The portal view
must never expose them (see the gating note in `benefit_statement.py`), so this
wrapper nulls them out while keeping the same response shape — the frontend
statement components are shared between both surfaces.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Employee
from app.schemas.api import BenefitStatementOut
from app.services.benefit_statement import build_benefit_statement


def build_member_statement(db: Session, employee: Employee) -> BenefitStatementOut:
    statement = build_benefit_statement(db, employee)
    coverage = [
        line.model_copy(
            update={
                "financials": None,
                "match_method": None,
                "match_confidence": None,
                "rule_human_readable": None,
            }
        )
        for line in statement.coverage
    ]
    return statement.model_copy(update={"coverage": coverage})
