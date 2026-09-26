"""Member-facing benefit statement — the broker statement with financials gated off.

`build_benefit_statement` carries per-member premium figures and matching
internals (method/confidence/rule text) that are broker-facing. The portal view
must never expose them (see the gating note in `benefit_statement.py`), so this
wrapper nulls them out while keeping the same response shape — the frontend
statement components are shared between both surfaces.

`build_member_statement` still carries every matched plan's STORED schedule:
claim submission, the AI review pipeline and the broker's claim checks all read
it and must see what the slip says. The publication rules — confirmed plans
only, the cleaned member schedule — are applied at the API edge by
`member_visible_statement`, the one function every member/preview response
passes through.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Employee
from app.models.category import CategoryStatus
from app.schemas.api import BenefitStatementOut, CoverageLine
from app.schemas.claims import UtilizationOut
from app.services.benefit_statement import build_benefit_statement
from app.services.member_schedule import member_schedule


def build_member_statement(db: Session, employee: Employee) -> BenefitStatementOut:
    statement = build_benefit_statement(db, employee)
    coverage = [
        line.model_copy(
            update={
                "financials": None,
                "premium_note": None,
                "match_method": None,
                "match_confidence": None,
                "rule_human_readable": None,
            }
        )
        for line in statement.coverage
    ]
    return statement.model_copy(update={"coverage": coverage})


def member_visible_code(code: str | None) -> bool:
    """GTL is a death benefit and belongs only in an offered enrolment choice."""
    return (code or "").strip().upper() != "GTL"


def is_published(line: CoverageLine) -> bool:
    """A plan reaches members only once the broker has confirmed its setup.

    Slip upload writes provisional plans (``needs_review``) so the broker can
    review them; until confirmation their values are extraction output, which
    has been wrong in ways a member would act on (a dental price list shifted
    one row). A line with no matched plan row at all is not a published plan
    either.
    """
    return line.plan_status == CategoryStatus.confirmed.value


def _member_line(line: CoverageLine) -> CoverageLine:
    if not is_published(line):
        return line.model_copy(
            update={
                "published": False,
                "plan_status": None,
                "benefit_schedule": None,
                "annual_policy_limit": None,
                "cover_description": None,
            }
        )
    return line.model_copy(
        update={
            "published": True,
            "plan_status": None,
            "benefit_schedule": member_schedule(line.benefit_schedule),
        }
    )


def member_visible_statement(statement: BenefitStatementOut) -> BenefitStatementOut:
    """Apply employee-display rules at the API edge, preserving internal coverage."""
    return statement.model_copy(
        update={
            "coverage": [
                _member_line(line)
                for line in statement.coverage
                if member_visible_code(line.product_code)
            ],
        }
    )


def published_product_codes(statement: BenefitStatementOut) -> set[str]:
    return {
        line.product_code.strip().upper()
        for line in statement.coverage
        if is_published(line)
    }


def member_visible_utilization(
    usage: UtilizationOut, published: set[str] | None = None
) -> UtilizationOut:
    """Usage buckets a member may see: no GTL and, when ``published`` is given,
    only products whose plan is confirmed — a balance computed against an
    unreviewed limit is exactly the figure the publish gate exists to stop."""
    return usage.model_copy(
        update={
            "insured": [
                bucket for bucket in usage.insured
                if member_visible_code(bucket.product_code)
                and (
                    published is None
                    # Claims against cover the member no longer holds carry
                    # no limit at all; they stay so the member still sees
                    # what they filed.
                    or bucket.orphaned
                    or (bucket.product_code or "").strip().upper() in published
                )
            ],
        }
    )
