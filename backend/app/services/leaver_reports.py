"""Leaver reports — the two sheets a broker needs when someone leaves.

- **Leaver Summary**: one row per leaver with their cover window and their final
  wallet position. What the company owes them, or they owe back.
- **Leaver Details**: their claims. What is still in flight when cover ends.

Both compose existing services rather than re-querying: the wallet position
comes from `flex_ledger.member_flex` and the claim rows from
`claims_reports._claim_rows`'s inputs, so a leaver's figures are the same ones
their coverage record shows.

**A leaver's benefit END date is their last day, not the year's end.** That is
the entire point of the sheet — a claim incurred after it is not covered.
Defaulting to the policy-year end would make every leaver look covered to
31 December. The window is resolved by `insurer_reports.benefit_window`, the
same helper the wallet ledger prints, and the claims sheet carries both the end
date AND a flag per claim so a reader can settle up without cross-referencing
the other sheet.

**The allocation is PRO-RATED at assignment, never here.** When the scheme says
so (`services/flex_proration.py`), `assign_flex_membership` writes the member's
own share of the annual allowance to `Employee.flex_wallet_amount`, and every
surface — the member's wallet page, the benefit statement, the broker coverage
pane, this sheet — reports that one figure. `Annual Allocation Amt` and
`Pro-ration` print the derivation beside it, because a reduced number with
nothing explaining it is unauditable and this is the sheet people argue over.
Pro-rating in the report instead would make it the only place that disagrees
with the member's own screen.

**The balance never goes negative** and there is no shortfall column: a flex
wallet pays up to the limit, so utilisation cannot exceed the allowance. See
`flex_ledger.MemberFlex.balance` and `docs/FLEX_PRORATION_PLAN.md`.
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
from app.services.claim_fx import policy_amount
from app.services.claims import dependant_display_name
from app.services.claims_reports import status_label
from app.services.flex_ledger import WALLET_NAME, _flex_claims, member_flex
from app.services.flex_pricing_resolver import flex_year_context
from app.services.fx import POLICY_CURRENCY
from app.services.insurer_reports import (
    append_safe,
    as_date,
    autosize,
    benefit_window,
    bold_header,
    last_day_of_service,
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
    "Total Allocation Amt", "Annual Allocation Amt", "Pro-ration",
    "Selection Amt", "Leave Trading Amt",
    "Claims Payment Amt", "Total Utilized Amt",
    "Pending Claims Payment Amt", "Balance Available Allocation Amt",
]

LEAVER_DETAILS_HEADER = [
    "Insured Entity", "Staff ID", "Employee Name", "Category",
    "Benefit End Date", "Claimant", "Relation", "Reference No.",
    "Claim Type", "LOG", "Claim Category",
    "Incurred Date", "Incurred After Cover End",
    "Service Provider", "Currency", "Incurred Amt",
    f"Converted Incurred Amt ({POLICY_CURRENCY})",
    f"Payment Amt ({POLICY_CURRENCY})", "Status", "Paid Date",
    "Admin Remark",
]


@dataclass(frozen=True)
class Leaver:
    employee: Employee
    benefit_start: date | None
    benefit_end: date | None


def leavers(db: Session, py: PolicyYear) -> list[Leaver]:
    """Terminated employees whose last day falls inside the policy period.

    Reuses `report_employees`, which already applies the in-period rule the
    insurer listings use — a pre-period leaver is ancient history to both.

    Bounded at the OTHER end here, which `report_employees` deliberately is not:
    someone whose last day falls after the period closed was covered for the
    whole of it and is a leaver of the NEXT year's sheet. The insurer listing
    still wants them (it needs the date to off-bill), but a leaver sheet must
    not print a cover window that runs past the year it is headed with, and must
    not report a closing wallet position for a year the member has not finished.
    """
    out: list[Leaver] = []
    for emp in report_employees(db, py):
        if emp.status != EMPLOYEE_STATUS_TERMINATED:
            continue
        start, end = benefit_window(py, emp)
        if end is not None and py.end_date is not None and end > py.end_date:
            continue
        out.append(Leaver(employee=emp, benefit_start=start, benefit_end=end))
    return out


def _after_cover_end(incurred: date | None, benefit_end: date | None) -> str:
    """Whether a claim falls outside the cover this leaver actually held.

    The sheet's whole premise, made checkable: cover stops on the last day, so a
    claim incurred after it is the broker's to recover or refuse. Blank — NOT
    "No" — when either date is missing, because that is genuinely unknown and
    the Benefit End Date cell beside it is blank for the same reason. Printing
    "No" there would assert cover the roster never evidenced.
    """
    if incurred is None or benefit_end is None:
        return ""
    return "Yes" if incurred > benefit_end else "No"


def build_leaver_summary_workbook(
    db: Session, py: PolicyYear, *, masked: bool = True
) -> Workbook:
    rows = leavers(db, py)
    claims_by_emp = _flex_claims(db, py)
    context = flex_year_context(db, py.id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Leaver Summary"
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
            last_day_of_service(emp),
            leaver.benefit_start,
            # Their last day, NOT the year's end — see the module docstring.
            leaver.benefit_end,
            first_value(attrs, _CATEGORY_KEYS) or "",
            WALLET_NAME if flex else "",
            flex.wallet if flex else None,
            flex.annual_wallet if flex else None,
            flex.proration_note if flex else "",
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
    by_id = {leaver.employee.id: leaver for leaver in rows}

    claims = list(
        db.execute(
            select(Claim)
            .where(
                Claim.policy_year_id == py.id,
                Claim.employee_id.in_(list(by_id) or [""]),
                Claim.status != CLAIM_STATUS_DRAFT,
            )
            .order_by(Claim.incurred_date.desc())
        )
        .scalars()
        .all()
    )
    # Grouped in the order `leavers` produced — which is `report_employees`'
    # (name, staff id), the order the Summary sheet is in. Ordering the query by
    # `employee_id` sorted the sheet by an opaque identifier, so the two sheets
    # of one workbook listed the same people in unrelated orders and neither
    # read as sorted at all.
    by_employee: dict[str, list[Claim]] = {}
    for claim in claims:
        by_employee.setdefault(claim.employee_id, []).append(claim)

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
    ws.title = "Leaver Claims"
    append_safe(ws, LEAVER_DETAILS_HEADER)
    bold_header(ws)

    for leaver in rows:
        emp = leaver.employee
        attrs = emp.attribute_values or {}
        for claim in by_employee.get(emp.id, []):
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
                # Repeated per row on purpose: the flag two columns along is an
                # assertion about this date, and a reader who cannot see the
                # date cannot check the flag.
                leaver.benefit_end,
                claimant,
                relation,
                claim.reference_no or "",
                # A LOG case is marked in its OWN column, exactly as the claims
                # register does it. Writing "LOG" into Claim Type destroyed the
                # descriptive label — and `log_cases.set_case_type` deliberately
                # does NOT rewrite `claim_type` on reclassification, precisely so
                # that label survives, so this sheet was the one place it did not.
                claim.claim_type or "",
                "Yes" if claim.case_type == CASE_TYPE_LOG else "No",
                "Flexible Benefits" if is_flex else "Insurance",
                claim.incurred_date,
                _after_cover_end(claim.incurred_date, leaver.benefit_end),
                claim.provider_name or "",
                claim.currency,
                claim.amount_claimed,
                # Blank when unresolved rather than the foreign figure — the
                # column is policy-currency and gets totalled.
                policy_amount(claim),
                claim.payment_amount
                if claim.payment_amount is not None
                else claim.amount_approved,
                status_label(claim.status),
                claim.paid_on,
                claim.admin_remarks or "",
            ])

    autosize(ws)
    return wb
