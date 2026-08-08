"""Flex wallet ledger + utilisation reports.

The incumbent platform stores wallet movements in a table. **We derive them**,
because every movement already exists with a date:

| Movement          | Source                                              |
|-------------------|-----------------------------------------------------|
| Wallet Allocation | `Employee.flex_wallet_amount` @ `flex_assigned_at`  |
| Benefit Selection | `FlexPriceLine.price_tag` per product               |
| Buy/Sell Leave    | `LeaveElection.flex_amount` (signed, snapshotted)   |
| Claims Payment    | approved/settled flex claims @ `decided_at`         |

A `flex_ledger` table would need a backfill that cannot reach the per-firm
Postgres schemas (`provision_tenants.py` syncs tables and columns, never rows),
and from the moment it existed it would be a second source of truth beside the
benefit statement. Deriving keeps the ledger, `FlexPanel`, the member's wallet
page and this report provably equal — they call the same resolver.

**`B/F Allocation Amt`, `Deals Amt` and `Salary Deduction` are emitted empty.**
They are columns in the incumbent's template and are blank on every row of the
live file too — the features behind them were never built there either. They
are kept so a broker can diff the two exports column-for-column; nothing here
invents a value for them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.models import Claim, Employee, PolicyYear
from app.models.claim import CLAIM_KIND_FLEX, SETTLED_STATUSES
from app.services.flex_pricing_resolver import (
    FlexYearContext,
    flex_year_context,
    summarize_employee,
)
from app.services.flex_proration import describe
from app.services.insurer_reports import (
    _last_day_of_service,
    append_safe,
    as_date,
    autosize,
    benefit_window,
    bold_header,
    report_employees,
)
from app.services.roster_attributes import (
    EMPLOYEE_ID_KEYS,
    first_value,
    mask_nric,
)

_ENTITY_KEYS = ("entity", "company", "subsidiary")
_CATEGORY_KEYS = ("category",)
_HIRE_KEYS = ("date_of_hire", "hire_date")

# Movement descriptions, in the incumbent's own vocabulary so the two exports
# read the same way to a broker holding both.
DESC_ALLOCATION = "Wallet Allocation"
DESC_SELECTION = "Benefit Selection"
DESC_LEAVE = "Leave Trading"
DESC_CLAIM = "Claim Payment"

UTILISATION_HEADER = [
    "Entity", "Staff ID", "Employee Name", "Identification No.",
    "Date of Hire", "Last Day of Service", "Benefit Start Date",
    "Benefit End Date", "Claimant", "Relation", "Date of Transaction",
    "Wallet", "Description", "Category",
    "B/F Allocation Amt", "Allocation Amt", "Buy Leave Amt", "Sell Leave Amt",
    "Selection Amt", "Deals Amt", "Claims Payment Amt", "Salary Deduction",
]

UTILISATION_SUMMARY_HEADER = [
    "Entity", "Staff ID", "Employee Name", "Identification No.",
    "Date of Hire", "Last Day of Service", "Benefit Start Date",
    "Benefit End Date", "Category", "Wallet",
    "Total Allocation Amt", "Annual Allocation Amt", "Pro-ration",
    "Buy Leave Amt", "Sell Leave Amt",
    "Selection Amt", "Deals Amt", "Claims Payment Amt",
    "Total Utilized Amt", "Pending Claims Payment Amt",
    "Balance Available Allocation Amt", "B/F Allocation Amt to Next Year",
]

# The wallet's name. One wallet per member today; the column exists so a future
# second wallet does not change the sheet's shape.
WALLET_NAME = "FSA"


@dataclass(frozen=True)
class LedgerEntry:
    """One dated wallet movement. Amounts are signed from the WALLET's point of
    view — an allocation is positive, a selection or a claim is negative — so
    the column totals add up to the balance without a per-column sign rule."""

    when: date | None
    description: str
    category: str
    claimant: str
    relation: str
    allocation: float | None = None
    buy_leave: float | None = None
    sell_leave: float | None = None
    selection: float | None = None
    claim_payment: float | None = None


@dataclass(frozen=True)
class MemberFlex:
    """One member's whole flex position, resolved once and used by both sheets.

    Both reports read this same object; computing them separately is how the
    ledger and its summary would start disagreeing about a balance.
    """

    employee: Employee
    currency: str | None
    wallet: float | None
    entries: list[LedgerEntry]
    selection_total: float
    buy_leave: float
    sell_leave: float
    claims_settled: float
    claims_pending: float

    @property
    def annual_wallet(self) -> float | None:
        """The un-pro-rated allowance, when the wallet was pro-rated.

        Blank otherwise, so the column only carries a figure where it differs
        from the one beside it. A pro-rated number with nothing explaining it is
        unauditable, and this is the figure members dispute.
        """
        raw = getattr(self.employee, "flex_proration", None)
        if not isinstance(raw, dict):
            return None
        value = raw.get("full_amount")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        return round(float(value), 2)

    @property
    def proration_note(self) -> str:
        """"6/12 months" / "182/365 days"; empty when nothing was pro-rated."""
        return describe(getattr(self.employee, "flex_proration", None))

    @property
    def total_utilized(self) -> float:
        """Selection plus claims plus leave bought, less leave sold.

        The signed leave trade is one of the terms. Omitting it made the printed
        terms not add up to the printed total for anyone who had traded a day —
        the same defect the broker coverage pane had.
        """
        return round(
            self.selection_total
            + self.claims_settled
            + self.buy_leave
            - self.sell_leave,
            2,
        )

    @property
    def _after_cover(self) -> float | None:
        """The wallet once elected cover and any leave trade are taken off, but
        BEFORE claims — the mirror of `FlexCoverageLine.flex_balance`."""
        if self.wallet is None:
            return None
        return round(
            self.wallet - self.selection_total - self.buy_leave + self.sell_leave, 2
        )

    @property
    def balance(self) -> float | None:
        """What is left to draw.

        **Claims cannot take this below zero.** A flex wallet pays UP TO the
        limit — a member with S$500 left who presents a S$700 bill utilises
        S$500 and pays the rest themselves — so "overspent by S$x" is not a
        state the product can be in, and printing one is an indication of
        something that cannot happen. It is reachable on paper only because
        pro-ration binds forward: it can shrink a leaver's allowance below what
        was already reimbursed, and never reaches back for that money. In that
        case the row's terms deliberately do NOT subtract to this figure —
        `Total Allocation Amt` and `Total Utilized Amt` each stay the true
        number, and this reports what is left to draw, which is nothing.
        Restating either to force the subtraction would move a fact to protect
        an arithmetic identity.

        A wallet already overdrawn by ELECTED COVER is a different state and
        stays signed: the member holds cover costing more than their allowance,
        which the enrolment guard and the bulk `flex_overdraft` warning exist
        for. `utilization._flex_utilization` splits `available` the same way, so
        the sheets and the member's own screen can never disagree about what
        they have left.
        """
        base = self._after_cover
        if base is None:
            return None
        drawn_down = round(base - self.claims_settled, 2)
        return drawn_down if base < 0 else max(0.0, drawn_down)


def _flex_claims(db: Session, py: PolicyYear) -> dict[str, list[Claim]]:
    rows = list(
        db.execute(
            select(Claim).where(
                Claim.policy_year_id == py.id,
                Claim.claim_kind == CLAIM_KIND_FLEX,
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[Claim]] = {}
    for claim in rows:
        out.setdefault(claim.employee_id, []).append(claim)
    return out


def _claim_amount(claim: Claim) -> float:
    return float(claim.amount_converted or claim.amount_claimed or 0.0)


def member_flex(
    db: Session,
    py: PolicyYear,
    employee: Employee,
    claims: list[Claim],
    context: FlexYearContext | None = None,
) -> MemberFlex | None:
    """Resolve one member's ledger. None when flex does not apply to them.

    Goes through `summarize_employee` — the SAME resolver the benefit statement
    and the broker coverage pane use — so a price tag on this sheet is the price
    tag the member sees.

    ``context`` is the year-level half of that resolution, built once by the
    caller. It is the difference between 18 seconds and 4 on a 491-member
    roster: without it each member rebuilds the slip index from a full
    `list_product_tiers` load. Optional so a single-member caller stays a
    one-liner.
    """
    summary = summarize_employee(db, employee, context=context)
    if summary is None:
        return None

    entries: list[LedgerEntry] = []
    name = employee.employee_name or employee.staff_id
    category = first_value(employee.attribute_values or {}, _CATEGORY_KEYS) or ""

    # The allocation itself. Dated by when the tier was assigned, falling back
    # to the year's start — a wallet with no assignment stamp is still a real
    # allocation, and a blank date would drop it out of any period filter.
    allocated_on = (
        summary_date(employee.flex_assigned_at) or py.start_date
    )
    if summary.wallet_amount is not None:
        entries.append(LedgerEntry(
            when=allocated_on,
            description=DESC_ALLOCATION,
            category=category,
            claimant=name,
            relation="SELF",
            allocation=summary.wallet_amount,
        ))

    # One row per product the member's coverage draws on. Products with no tag
    # are skipped: a "SGD 0" row per uncovered product is the noise the coverage
    # pane rebuild removed.
    selection_total = 0.0
    for line in summary.lines:
        if not line.price_tag:
            continue
        selection_total += line.price_tag
        entries.append(LedgerEntry(
            when=allocated_on,
            description=DESC_SELECTION,
            category=line.product_code,
            claimant=name,
            relation="SELF",
            selection=line.price_tag,
        ))

    buy = sell = 0.0
    if summary.leave_flex_amount:
        amount = summary.leave_flex_amount
        # Signed: a sell CREDITS the wallet, a buy SPENDS it.
        if amount >= 0:
            sell = amount
        else:
            buy = abs(amount)
        entries.append(LedgerEntry(
            when=allocated_on,
            description=DESC_LEAVE,
            category=f"{(summary.leave_action or '').title()} "
                     f"{summary.leave_days or 0:g} day(s)".strip(),
            claimant=name,
            relation="SELF",
            buy_leave=buy or None,
            sell_leave=sell or None,
        ))

    settled = pending = 0.0
    for claim in claims:
        if claim.status in SETTLED_STATUSES:
            amount = float(claim.amount_approved or 0.0)
            settled += amount
            entries.append(LedgerEntry(
                when=summary_date(claim.decided_at) or claim.incurred_date,
                description=DESC_CLAIM,
                category=claim.flex_category_name or "",
                claimant=name,
                relation="SELF",
                claim_payment=amount,
            ))
        elif claim.status not in ("draft", "rejected"):
            # In flight. Reported separately and NEVER subtracted from the
            # balance — the member has not spent it yet.
            pending += _claim_amount(claim)

    return MemberFlex(
        employee=employee,
        currency=summary.currency,
        wallet=summary.wallet_amount,
        entries=sorted(entries, key=lambda e: (e.when or date.min, e.description)),
        selection_total=round(selection_total, 2),
        buy_leave=round(buy, 2),
        sell_leave=round(sell, 2),
        claims_settled=round(settled, 2),
        claims_pending=round(pending, 2),
    )


def summary_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def build_utilisation_workbook(
    db: Session, py: PolicyYear, *, masked: bool = True
) -> Workbook:
    """The wallet ledger — one row per dated movement."""
    employees = report_employees(db, py)
    claims_by_emp = _flex_claims(db, py)
    # ONCE per report, not once per member — see `FlexYearContext`.
    context = flex_year_context(db, py.id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    append_safe(ws, UTILISATION_HEADER)
    bold_header(ws)

    for emp in employees:
        flex = member_flex(db, py, emp, claims_by_emp.get(emp.id, []), context)
        if flex is None or not flex.entries:
            continue
        attrs = emp.attribute_values or {}
        raw_id = first_value(attrs, EMPLOYEE_ID_KEYS)
        start, end = benefit_window(py, emp)
        head = [
            first_value(attrs, _ENTITY_KEYS) or "",
            emp.staff_id,
            emp.employee_name or "",
            mask_nric(raw_id) if masked else (raw_id or ""),
            as_date(first_value(attrs, _HIRE_KEYS)),
            _last_day_of_service(emp),
            start,
            end,
        ]
        for entry in flex.entries:
            append_safe(ws, [
                *head,
                entry.claimant,
                entry.relation,
                entry.when,
                WALLET_NAME,
                entry.description,
                entry.category,
                None,  # B/F Allocation Amt — see the module docstring
                entry.allocation,
                entry.buy_leave,
                entry.sell_leave,
                entry.selection,
                None,  # Deals Amt
                entry.claim_payment,
                None,  # Salary Deduction
            ])

    autosize(ws)
    return wb


def build_utilisation_summary_workbook(
    db: Session, py: PolicyYear, *, masked: bool = True
) -> Workbook:
    """One row per member: their whole wallet position for the year."""
    employees = report_employees(db, py)
    claims_by_emp = _flex_claims(db, py)
    # ONCE per report, not once per member — see `FlexYearContext`.
    context = flex_year_context(db, py.id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    append_safe(ws, UTILISATION_SUMMARY_HEADER)
    bold_header(ws)

    for emp in employees:
        flex = member_flex(db, py, emp, claims_by_emp.get(emp.id, []), context)
        if flex is None:
            continue
        attrs = emp.attribute_values or {}
        raw_id = first_value(attrs, EMPLOYEE_ID_KEYS)
        start, end = benefit_window(py, emp)
        append_safe(ws, [
            first_value(attrs, _ENTITY_KEYS) or "",
            emp.staff_id,
            emp.employee_name or "",
            mask_nric(raw_id) if masked else (raw_id or ""),
            as_date(first_value(attrs, _HIRE_KEYS)),
            _last_day_of_service(emp),
            start,
            end,
            first_value(attrs, _CATEGORY_KEYS) or "",
            WALLET_NAME,
            flex.wallet,
            flex.annual_wallet,
            flex.proration_note,
            flex.buy_leave or None,
            flex.sell_leave or None,
            flex.selection_total or None,
            None,  # Deals Amt — see the module docstring
            flex.claims_settled or None,
            flex.total_utilized,
            flex.claims_pending or None,
            flex.balance,
            None,  # B/F Allocation Amt to Next Year
        ])

    autosize(ws)
    return wb


def flex_report_filename(kind: str, today: date | None = None) -> str:
    return f"{kind}-{(today or business_today()):%Y%m%d}.xlsx"
