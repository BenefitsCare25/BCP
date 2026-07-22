"""Broker-facing claims register (.xlsx) — one row per claim for a policy year.

The claims-team analogue of the insurer listings in ``insurer_reports.py``:
a flat export of every claim (all statuses) with claimant, coverage line,
amounts and decision, for reconciliation against the insurer's claims ledger.
Reuses the injection-safe / formatting helpers so the workbook style matches
the other reports.
"""
from __future__ import annotations

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Employee, PolicyYear
from app.models.claim import CLAIM_KIND_FLEX
from app.services.claims import prefetch_claim_relations
from app.services.insurer_reports import (
    _autosize,
    _bold_header,
    _naive,
    append_safe,
)

CLAIMS_REGISTER_HEADER = [
    "Claim ID",
    "Status",
    "Staff ID",
    "Employee Name",
    "Claimant",
    "Type",
    "Coverage",
    "Claim Type",
    "Sub-type",
    "Incurred Date",
    "Provider",
    "Invoice No.",
    "Currency",
    "Amount Claimed",
    "Amount Approved",
    "Submitted On",
    "Decided On",
    "Decision Notes",
]


def _status_label(status: str) -> str:
    return status.replace("_", " ").title()


def build_claims_register_workbook(
    db: Session, policy_year: PolicyYear
) -> Workbook:
    """One row per claim in the year, newest submission first."""
    rows = list(
        db.execute(
            select(Claim, Employee)
            .join(Employee, Claim.employee_id == Employee.id)
            .where(Claim.policy_year_id == policy_year.id)
            .order_by(
                Claim.submitted_at.desc().nullslast(),
                Claim.created_at.desc(),
            )
        ).all()
    )
    claims = [c for c, _ in rows]
    _, dep_names = prefetch_claim_relations(db, claims)

    wb = Workbook()
    ws = wb.active
    ws.title = "Claims"
    ws.append(CLAIMS_REGISTER_HEADER)
    _bold_header(ws)

    for claim, employee in rows:
        is_flex = claim.claim_kind == CLAIM_KIND_FLEX
        claimant = (
            dep_names.get(claim.dependant_id)
            if claim.dependant_id
            else (employee.employee_name or employee.staff_id)
        )
        coverage = (
            claim.flex_category_name if is_flex else claim.product_code
        ) or ""
        append_safe(ws, [
            claim.id[:8],
            _status_label(claim.status),
            employee.staff_id,
            employee.employee_name or "",
            claimant or "",
            "Flex" if is_flex else "Insured",
            coverage,
            claim.claim_type or "",
            claim.sub_type or "",
            claim.incurred_date,
            claim.provider_name or "",
            claim.invoice_number or "",
            claim.currency,
            claim.amount_claimed,
            claim.amount_approved,
            _naive(claim.submitted_at),
            _naive(claim.decided_at),
            claim.decision_notes or "",
        ])

    _autosize(ws)
    return wb
