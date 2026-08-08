"""Leaver reports — the two sheets a broker needs when someone leaves.

- **Leaver Summary**: one row per leaver with their cover window and their final
  wallet position. What the company owes them, or they owe back.
- **Leaver Details**: their claims. What is still in flight when cover ends.

Both compose existing services rather than re-querying: the wallet position
comes from `flex_ledger.member_flex` and the claim rows from
`claims_reports._claim_rows`'s inputs, so a leaver's figures are the same ones
their coverage record shows.

**A leaver's benefit END date is their last day, not the year's end.** That is
the entire point of the sheet — the wallet is pro-rated to it and any claim
incurred after it is not covered. Defaulting to the policy-year end would make
every leaver look covered to 31 December.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Employee, PolicyYear
from app.models.claim import (
    CASE_TYPE_LOG,
    CLAIM_KIND_FLEX,
    CLAIM_STATUS_DRAFT,
)
from app.models.employee import EMPLOYEE_STATUS_TERMINATED
from app.services.claims import dependant_display_name
from app.services.claims_reports import status_label
from app.services.flex_ledger import WALLET_NAME, _flex_claims, member_flex
from app.services.flex_pricing_resolver import flex_year_context
from app.services.insurer_reports import (
    _last_day_of_service,
    append_safe,
    as_date,
    autosize,
    bold_header,
    report_employees,
)
from app.services.roster_attributes import (
    EMPLOYEE_ID_KEYS,
    REL_KEYS,
    first_value,
    mask_nric,
)

_ENTITY_KEYS = ("entity", "company", "subsidiary")
_CATEGORY_KEYS = ("category",)
_HIRE_KEYS = ("date_of_hire", "hire_date")

LEAVER_SUMMARY_HEADER = [
    "Entity", "Staff ID", "Employee Name", "Identification No.",
    "Date of Hire", "Last Day of Service", "Benefit Start Date",
    "Benefit End Date", "Category", "Wallet",
    "Total Allocation Amt", "Selection Amt", "Leave Trading Amt",
    "Claims Payment Amt", "Total Utilized Amt",
    "Pending Claims Payment Amt", "Balance Available Allocation Amt",
]

LEAVER_DETAILS_HEADER = [
    "Insured Entity", "Staff ID", "Employee Name", "Category",
    "Claimant", "Relation", "Reference No.", "Claim Type", "Claim Category",
    "Incurred Date", "Service Provider", "Currency", "Incurred Amt",
    "Converted Incurred Amt", "Payment Amt", "Status", "Paid Date",
    "Admin Remark",
]


@dataclass(frozen=True)
class Leaver:
    employee: Employee
    benefit_end: date | None


def leavers(db: Session, py: PolicyYear) -> list[Leaver]:
    """Terminated employees whose last day falls inside the policy period.

    Reuses `report_employees`, which already applies the in-period rule the
    insurer listings use — a pre-period leaver is ancient history to both.
    """
    out: list[Leaver] = []
    for emp in report_employees(db, py):
        if emp.status != EMPLOYEE_STATUS_TERMINATED:
            continue
        last_day = _last_day_of_service(emp)
        out.append(
            Leaver(
                employee=emp,
                benefit_end=last_day if isinstance(last_day, date) else None,
            )
        )
    return out


def _benefit_start(py: PolicyYear, emp: Employee) -> date | None:
    attrs = emp.attribute_values or {}
    start = as_date(first_value(attrs, ("effective_date",)))
    return start if isinstance(start, date) else py.start_date


def build_leaver_summary_workbook(
    db: Session, py: PolicyYear, *, masked: bool = True
) -> Workbook:
    rows = leavers(db, py)
    claims_by_emp = _flex_claims(db, py)
    context = flex_year_context(db, py.id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    append_safe(ws, LEAVER_SUMMARY_HEADER)
    bold_header(ws)

    for leaver in rows:
        emp = leaver.employee
        attrs = emp.attribute_values or {}
        raw_id = first_value(attrs, EMPLOYEE_ID_KEYS)
        flex = member_flex(db, py, emp, claims_by_emp.get(emp.id, []), context)
        # The signed leave trade as ONE figure: a leaver's sheet is read to
        # settle up, and splitting a trade the member made once across two
        # columns makes it look like two movements.
        leave_amount = (
            round((flex.sell_leave or 0.0) - (flex.buy_leave or 0.0), 2)
            if flex
            else None
        )
        append_safe(ws, [
            first_value(attrs, _ENTITY_KEYS) or "",
            emp.staff_id,
            emp.employee_name or "",
            mask_nric(raw_id) if masked else (raw_id or ""),
            as_date(first_value(attrs, _HIRE_KEYS)),
            _last_day_of_service(emp),
            _benefit_start(py, emp),
            # Their last day, NOT the year's end — see the module docstring.
            leaver.benefit_end,
            first_value(attrs, _CATEGORY_KEYS) or "",
            WALLET_NAME if flex else "",
            flex.wallet if flex else None,
            (flex.selection_total or None) if flex else None,
            leave_amount or None,
            (flex.claims_settled or None) if flex else None,
            flex.total_utilized if flex else None,
            (flex.claims_pending or None) if flex else None,
            flex.balance if flex else None,
        ])

    autosize(ws)
    return wb


def build_leaver_details_workbook(
    db: Session, py: PolicyYear, *, masked: bool = True
) -> Workbook:
    """Every claim belonging to a leaver, flex and insured alike.

    Includes claims still in flight on purpose: a claim that has not settled by
    the time cover ends is the one thing on this sheet that still needs doing.
    """
    rows = leavers(db, py)
    by_id = {leaver.employee.id: leaver.employee for leaver in rows}

    claims = list(
        db.execute(
            select(Claim)
            .where(
                Claim.policy_year_id == py.id,
                Claim.employee_id.in_(list(by_id) or [""]),
                Claim.status != CLAIM_STATUS_DRAFT,
            )
            .order_by(Claim.employee_id, Claim.incurred_date.desc())
        )
        .scalars()
        .all()
    )
    deps = {}
    if claims:
        from app.models import Dependant

        deps = {
            d.id: d
            for d in db.execute(
                select(Dependant).where(
                    Dependant.id.in_(
                        [c.dependant_id for c in claims if c.dependant_id] or [""]
                    )
                )
            ).scalars()
        }

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    append_safe(ws, LEAVER_DETAILS_HEADER)
    bold_header(ws)

    for claim in claims:
        emp = by_id[claim.employee_id]
        attrs = emp.attribute_values or {}
        is_flex = claim.claim_kind == CLAIM_KIND_FLEX
        if claim.dependant_id:
            dep = deps.get(claim.dependant_id)
            claimant = dependant_display_name(dep) or ""
            relation = first_value(
                (dep.attribute_values or {}) if dep else {}, REL_KEYS
            ) or "Dependant"
        else:
            claimant = emp.employee_name or emp.staff_id
            relation = "Self"
        append_safe(ws, [
            first_value(attrs, _ENTITY_KEYS) or "",
            emp.staff_id,
            emp.employee_name or "",
            first_value(attrs, _CATEGORY_KEYS) or "",
            claimant,
            relation,
            claim.reference_no or "",
            # A LOG case is marked, not segregated — same rule the claims
            # register follows.
            "LOG" if claim.case_type == CASE_TYPE_LOG else (claim.claim_type or ""),
            "Flexible Benefits" if is_flex else "Insurance",
            claim.incurred_date,
            claim.provider_name or "",
            claim.currency,
            claim.amount_claimed,
            claim.amount_converted
            if claim.amount_converted is not None
            else claim.amount_claimed,
            claim.payment_amount
            if claim.payment_amount is not None
            else claim.amount_approved,
            status_label(claim.status),
            claim.paid_on,
            claim.admin_remarks or "",
        ])

    autosize(ws)
    return wb
