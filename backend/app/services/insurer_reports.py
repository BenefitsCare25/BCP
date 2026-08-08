"""Insurer-facing downloadable reports (the Reports page).

Phase 1 ships the benefit-selection + buy/sell-leave report; the per-insurer
employee/dependant listings build on the same helpers in later phases.

Unlike the internal coverage reports (``roster_reports.py``), these listings
INCLUDE employees who left during the policy period — insurers bill/refund
pro-rata for leavers, so a leaver row with its last day of service is part of
the deliverable. They can also emit unmasked NRIC/FIN for insurer submission;
the endpoint gates that behind a write-capable role and audits every download.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, PolicyYear, User
from app.models.employee import EMPLOYEE_STATUS_ACTIVE, EMPLOYEE_STATUS_TERMINATED
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.enrollment_window import EnrollmentWindow, WindowStatus
from app.models.leave_election import LeaveAction, LeaveElection
from app.services.roster_attributes import (
    EMPLOYEE_ID_KEYS,
    first_value,
    mask_nric,
)
from app.services.roster_parser import _FORMULA_LEADERS

# Report vocabulary for the "Status" column, keyed by Enrollment.status.
# confirmed and deemed both read "Processed" — the insurer only cares that the
# selection is final, not how it got there. Selected/Submitted still tell the
# two apart (a deemed member never touched or submitted anything).
_STATUS_VOCAB: dict[str, str] = {
    EnrollmentStatus.not_started: "Not Started",
    EnrollmentStatus.in_progress: "In Progress",
    EnrollmentStatus.submitted: "Submitted",
    EnrollmentStatus.confirmed: "Processed",
    EnrollmentStatus.deemed: "Processed",
    EnrollmentStatus.declined: "Declined",
}

# Statuses meaning "the member actively made a selection" (touched the form).
_SELECTED_STATUSES = {
    EnrollmentStatus.in_progress,
    EnrollmentStatus.submitted,
    EnrollmentStatus.confirmed,
    EnrollmentStatus.declined,
}

_XLSX_EPOCH_KEYS = ("last_day_of_service", "last_day", "termination_date")


def report_employees(db: Session, policy_year: PolicyYear) -> list[Employee]:
    """Roster for insurer reports: active staff PLUS in-period leavers.

    A terminated employee stays on the report when their last day falls on or
    after the policy-year start (or is unknown — conservative include, the
    insurer reconciles). Pre-period leavers are ancient history and excluded.

    **The date this tests is the RESOLVED last day, not the column alone.**
    `terminated_effective` is written by the listing sync (`services/adc.py` is
    its only writer); a roster imported any other way carries the leaving date
    in `attribute_values` instead, which is why `_last_day_of_service` has a
    fallback and every sheet PRINTS through it. Filtering on the bare column
    while printing the resolved value meant a leaver whose date lived only in
    the roster fell into the "unknown → include" branch, so a 2019 leaver
    appeared on a 2026 listing with their 2019 last day printed beside them.
    """
    rows = list(
        db.execute(
            select(Employee)
            .where(
                Employee.policy_year_id == policy_year.id,
                Employee.status.in_(
                    [EMPLOYEE_STATUS_ACTIVE, EMPLOYEE_STATUS_TERMINATED]
                ),
            )
            .order_by(Employee.employee_name, Employee.staff_id)
        )
        .scalars()
        .all()
    )
    start = policy_year.start_date

    def _in_period(emp: Employee) -> bool:
        if emp.status != EMPLOYEE_STATUS_TERMINATED:
            return True
        last_day = _resolved_last_day(emp)
        if last_day is None or start is None:
            return True
        return last_day >= start

    return [e for e in rows if _in_period(e)]


def latest_window(db: Session, policy_year_id: str) -> EnrollmentWindow | None:
    """The window whose selections the report describes: the most recently
    opened non-draft window (an open one outranks an older closed one)."""
    return db.execute(
        select(EnrollmentWindow)
        .where(
            EnrollmentWindow.policy_year_id == policy_year_id,
            EnrollmentWindow.status.in_([WindowStatus.open, WindowStatus.closed]),
        )
        .order_by(EnrollmentWindow.opens_at.desc())
    ).scalars().first()


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d")


def _as_date(value: object) -> date | str | None:
    """Coerce a roster date-ish value to a real ``date`` cell (Excel formats
    it) — fall back to the raw string when unparseable. Roster dates are stored
    ISO on ingest, but tolerate the common alternates + Excel serial numbers so
    a stray format lands as a real date rather than literal text."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # Excel serial date (days since 1899-12-30), if a numeric cell slipped through.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return (date(1899, 12, 30) + timedelta(days=int(value)))
        except (ValueError, OverflowError):
            return str(value)
    raw = str(value).strip().split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return str(value)


def _last_day_of_service(member: Employee | Any) -> date | str | None:
    """The member's last day AS THE SHEET PRINTS IT.

    Takes an Employee or a Dependant — both carry `terminated_effective` and
    `attribute_values`, and the listing sync writes the same two shapes to
    each. An unparseable roster value comes back as the raw string on purpose:
    a cell that reads "end of June" is worth printing, and dropping it would
    make a leaver look like they had no last day at all.
    """
    if member.terminated_effective is not None:
        return member.terminated_effective
    return _as_date(first_value(member.attribute_values or {}, _XLSX_EPOCH_KEYS))


def _resolved_last_day(member: Employee | Any) -> date | None:
    """The same value, as a real date, or None when it cannot be one.

    The DECIDING half of the pair above: only a real date can be compared
    against a policy period. A raw string is treated exactly like a missing one
    — unknown, and therefore conservatively in period.
    """
    value = _last_day_of_service(member)
    return value if isinstance(value, date) else None


def benefit_window(
    policy_year: PolicyYear, member: Employee | Any
) -> tuple[date | None, date | None]:
    """The member's own cover window inside the year.

    Start is their effective date when the roster carries one (a mid-year joiner
    is not allocated from January); end is their last day when they left. Both
    fall back to the year, which is the normal case.

    ONE definition, shared by the wallet ledger, its summary and the leaver
    sheets. It was written out twice — the leaver sheet resolved the start half
    itself — and a member's cover window is exactly the kind of figure this
    codebase keeps finding computed in two places and then disagreeing.
    """
    attrs = member.attribute_values or {}
    start = _as_date(first_value(attrs, ("effective_date",)))
    if not isinstance(start, date):
        start = policy_year.start_date
    return start, _resolved_last_day(member)


def _identification(emp: Employee, masked: bool) -> str:
    raw = first_value(emp.attribute_values or {}, EMPLOYEE_ID_KEYS)
    if masked:
        return mask_nric(raw)
    return raw or ""


def _naive(dt: datetime | None) -> datetime | None:
    """tz-naive copy for a clean Excel cell (SQLite naive / Postgres aware)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _user_names(db: Session, user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(User.id, User.display_name, User.email).where(User.id.in_(user_ids))
    ).all()
    return {uid: (name or email or uid) for uid, name, email in rows}


def _autosize(ws: Worksheet) -> None:
    for col in ws.columns:
        width = max(
            (len(str(c.value)) for c in col if c.value is not None), default=10
        )
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 48)


def _bold_header(ws: Worksheet) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True)


# Public aliases — these workbook helpers are the shared reports toolkit (also
# used by claims_register / insurer_listings / member_listing_template). Exposed
# without the leading underscore so other report modules don't reach into
# module-private names.
naive = _naive
autosize = _autosize
bold_header = _bold_header
as_date = _as_date
last_day_of_service = _last_day_of_service
resolved_last_day = _resolved_last_day


# Spreadsheet formula-injection guard. openpyxl stores any string starting with
# = + - @ (or a leading control char) as a live formula, so a roster value like
# ``=HYPERLINK(...)`` or ``=cmd|...`` would execute in the insurer's Excel when
# they open our deliverable. Prefix such strings with an apostrophe so Excel
# treats them as literal text. Applied to every cell in the insurer workbooks.
#
# The tuple lives in `roster_parser` because that module owns the READ half
# (`unescape_formula_guard`): the member-listing template is exported through
# here and uploaded back through there, so an escape with no matching unescape
# turns "+60186448967" into a phantom change on every upload.
def safe_cell(value: object) -> object:
    if isinstance(value, str) and value and value[0] in _FORMULA_LEADERS:
        return "'" + value
    return value


def append_safe(ws: Worksheet, row: list[object]) -> None:
    """Append a row with every string cell neutralized against formula
    injection (see ``safe_cell``)."""
    ws.append([safe_cell(v) for v in row])


BENEFIT_SELECTION_HEADER = [
    "Entity",
    "Staff ID",
    "Employee Name",
    "Identification No.",
    "Date of Hire",
    "Last Day of Service",
    "Status",
    "Employee Selected",
    "Employee Submitted",
    "LastUpdatedByID",
    "LastUpdatedByName",
    "LastUpdateTime",
    "ProcessedByID",
    "ProcessedByName",
    "ProcessedOn",
    "Buy or Sell Leave",
    "Days to Buy",
    "Days to Sell",
    "Currency",
    "PriceTag",
]


def build_benefit_selection_workbook(
    db: Session,
    policy_year: PolicyYear,
    masked: bool = True,
    window_id: str | None = None,
) -> Workbook:
    """One row per report employee: enrollment selection status + leave trade.

    ``window_id`` pins a specific enrollment window; default is the latest
    open/closed window for the year. Employees with no enrollment row (added
    after window open, or no window at all) report as Not Started.
    """
    employees = report_employees(db, policy_year)

    window = None
    if window_id is not None:
        window = db.get(EnrollmentWindow, window_id)
        # Only honor a window that belongs to THIS policy year — a stray/foreign
        # window_id must not silently drive the report (it would yield an
        # all-"Not Started" listing rather than the intended window's data).
        if window is not None and window.policy_year_id != policy_year.id:
            window = None
    if window is None:
        window = latest_window(db, policy_year.id)

    enrollments: dict[str, Enrollment] = {}
    leaves: dict[str, LeaveElection] = {}
    if window is not None:
        enr_rows = list(
            db.execute(
                select(Enrollment).where(Enrollment.window_id == window.id)
            ).scalars().all()
        )
        enrollments = {e.employee_id: e for e in enr_rows}
        enr_ids = [e.id for e in enr_rows]
        if enr_ids:
            leaves = {
                le.enrollment_id: le
                for le in db.execute(
                    select(LeaveElection).where(
                        LeaveElection.enrollment_id.in_(enr_ids)
                    )
                ).scalars().all()
            }

    names = _user_names(
        db,
        {
            e.confirmed_by
            for e in enrollments.values()
            if e.confirmed_by is not None
        },
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Benefit Selection"
    ws.append(BENEFIT_SELECTION_HEADER)
    _bold_header(ws)

    for emp in employees:
        attrs = emp.attribute_values or {}
        enr = enrollments.get(emp.id)
        status = enr.status if enr is not None else EnrollmentStatus.not_started
        submitted = enr is not None and enr.submitted_at is not None

        leave = leaves.get(enr.id) if enr is not None else None
        leave_action = ""
        days_buy: float | str = ""
        days_sell: float | str = ""
        currency = ""
        price_tag: float | str = ""
        if leave is not None:
            if leave.action == LeaveAction.buy:
                leave_action, days_buy = "buy", leave.days
            elif leave.action == LeaveAction.sell:
                leave_action, days_sell = "sell", leave.days
            else:
                leave_action = "no-change"
            currency = emp.flex_currency or "SGD"
            # The insurer-facing tag is the money moved, direction implied by
            # the action column — flex_amount's sign stays internal.
            price_tag = abs(leave.flex_amount) if leave.flex_amount else 0

        append_safe(ws, [
            first_value(attrs, ("entity", "company", "subsidiary")) or "",
            emp.staff_id,
            emp.employee_name or "",
            _identification(emp, masked),
            _as_date(first_value(attrs, ("date_of_hire", "hire_date"))),
            _last_day_of_service(emp),
            _STATUS_VOCAB.get(status, status.replace("_", " ").title()),
            "Yes" if status in _SELECTED_STATUSES else "No",
            "Yes" if submitted else "No",
            "",  # LastUpdatedByID — per-edit actor isn't tracked (template blank)
            "",  # LastUpdatedByName
            _naive(enr.updated_at) if enr is not None else None,
            enr.confirmed_by or "" if enr is not None else "",
            names.get(enr.confirmed_by or "", "") if enr is not None else "",
            _naive(enr.confirmed_at) if enr is not None else None,
            leave_action,
            days_buy,
            days_sell,
            currency,
            price_tag,
        ])

    _autosize(ws)
    return wb
