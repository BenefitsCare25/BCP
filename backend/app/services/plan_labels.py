"""Presentation labels; plan codes remain the keys for coverage and elections."""
from app.models import Plan


def member_plan_label(plan: Plan) -> str:
    return (
        (plan.report_label or "").strip()
        or (plan.display_name or "").strip()
        or f"Plan {plan.code}"
    )
