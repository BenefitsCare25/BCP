"""Portal Access report — the roster beside its portal accounts.

Answers one operational question: for every person on the roster, can they get
into the portal, and have they. Provisioning gaps (no account), stalled invites
(mailed but never signed in) and disabled accounts all read off one sheet.

**The report is ROSTER-first, not account-first.** An employee with no account
is the row that matters most, and an account-first query cannot produce it. The
account is joined ON, and its absence is a value ("Not provisioned").

Account resolution mirrors ``portal_auth.resolve_member_employee`` in reverse
and in the same order — the stamped ``employees.member_account_id`` first, then
``(client_id, staff_id)``. Using only the stamp under-reports every account
provisioned before the column was stamped; using only staff id would let a
re-provisioned account outrank the real link.
"""
from __future__ import annotations

from datetime import date

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.models import AuthMfa, Client, Employee, MemberAccount, PolicyYear
from app.models.auth import SUBJECT_MEMBER
from app.services.insurer_reports import (
    append_safe,
    as_date,
    autosize,
    bold_header,
    last_day_of_service,
    naive,
    report_employees,
)
from app.services.member_invite import portal_sign_in_url
from app.services.roster_attributes import EMAIL_KEYS, first_value

_MOBILE_KEYS = ("mobile", "mobile_phone", "phone", "contact_no", "handphone")
_ENTITY_KEYS = ("entity", "company", "subsidiary")
_CATEGORY_KEYS = ("category",)
_HIRE_KEYS = ("date_of_hire", "hire_date")
_CONFIRM_KEYS = ("confirmation_date",)
_EFFECTIVE_KEYS = ("effective_date",)

# Broker-facing account state. Deliberately NOT the raw column: "invited" says
# nothing about whether the invite was actually delivered, and an undelivered
# invite is the failure this report exists to surface (see
# `member_invite._deliver_invites` — `invite_sent_at` is stamped only after the
# mailer accepts the message).
STATUS_NOT_PROVISIONED = "Not provisioned"
STATUS_INVITE_PENDING = "Invite not sent"
STATUS_INVITED = "Invited"
STATUS_ACTIVE = "Active"
STATUS_DISABLED = "Disabled"

HEADER = [
    "Entity",
    "Staff ID",
    "Employee Name",
    "Date of Hire",
    "Confirmation Date",
    "Effective Date",
    "Last Day of Service",
    "Category",
    "Email Address",
    "Mobile Phone",
    "ProfileLink",
    "UserType",
    "Login ID",
    "Status",
    "Invite Sent On",
    "Last Sign-In",
    "Two-Factor",
]


def _account_status(account: MemberAccount | None) -> str:
    if account is None:
        return STATUS_NOT_PROVISIONED
    if account.status == "disabled":
        return STATUS_DISABLED
    if account.status == "active":
        return STATUS_ACTIVE
    # Still `invited`: distinguish "we have not mailed them yet" from "mailed,
    # never used". Both look identical on the account row and need different
    # actions — press Send invites, versus chase the member.
    return STATUS_INVITED if account.invite_sent_at else STATUS_INVITE_PENDING


def _accounts_for(
    db: Session, py: PolicyYear
) -> tuple[dict[str, MemberAccount], dict[str, MemberAccount]]:
    """(by id, by staff id) indexes over this client's member accounts."""
    rows = list(
        db.execute(
            select(MemberAccount).where(MemberAccount.client_id == py.client_id)
        )
        .scalars()
        .all()
    )
    by_id = {a.id: a for a in rows}
    # Ordered by staff id so a client that somehow holds two accounts for one
    # staff id resolves the same way the invite rollout picks its winner
    # (`member_invite._roster_entries`), rather than by row order.
    by_staff: dict[str, MemberAccount] = {}
    for a in sorted(rows, key=lambda r: (r.staff_id or "", r.id)):
        by_staff.setdefault(a.staff_id, a)
    return by_id, by_staff


def _mfa_confirmed(db: Session, account_ids: set[str]) -> set[str]:
    if not account_ids:
        return set()
    rows = db.execute(
        select(AuthMfa.subject_id).where(
            AuthMfa.subject_type == SUBJECT_MEMBER,
            AuthMfa.subject_id.in_(account_ids),
            AuthMfa.confirmed_at.is_not(None),
        )
    ).all()
    return {r[0] for r in rows}


def build_portal_access_workbook(db: Session, py: PolicyYear) -> Workbook:
    employees = report_employees(db, py)
    by_id, by_staff = _accounts_for(db, py)

    client = db.get(Client, py.client_id)
    # One URL for the whole company — built by the SAME function that emails
    # the invite, so the link on this sheet is the link the member received.
    # A hand-built one drifts the moment tenant routing changes.
    profile_link = portal_sign_in_url(client.slug if client else None)

    resolved: list[tuple[Employee, MemberAccount | None]] = []
    for emp in employees:
        account = by_id.get(emp.member_account_id or "") or by_staff.get(emp.staff_id)
        resolved.append((emp, account))

    mfa = _mfa_confirmed(db, {a.id for _, a in resolved if a is not None})

    wb = Workbook()
    ws = wb.active
    ws.title = "Portal Access"
    append_safe(ws, HEADER)
    bold_header(ws)

    for emp, account in resolved:
        attrs = emp.attribute_values or {}
        append_safe(
            ws,
            [
                first_value(attrs, _ENTITY_KEYS) or "",
                emp.staff_id,
                emp.employee_name or "",
                as_date(first_value(attrs, _HIRE_KEYS)),
                as_date(first_value(attrs, _CONFIRM_KEYS)),
                as_date(first_value(attrs, _EFFECTIVE_KEYS)),
                last_day_of_service(emp),
                first_value(attrs, _CATEGORY_KEYS) or "",
                # The account's email is the one that receives the invite; the
                # roster's is what HR supplied. They differ when a roster edit
                # lands after provisioning, and the account's is the operative
                # one for anything this report is used to chase.
                (account.email if account and account.email else None)
                or first_value(attrs, EMAIL_KEYS)
                or "",
                first_value(attrs, _MOBILE_KEYS) or "",
                profile_link,
                "employee",
                (account.system_login_id or "") if account else "",
                _account_status(account),
                naive(account.invite_sent_at) if account else None,
                naive(account.last_sign_in_at) if account else None,
                "Yes" if account and account.id in mfa else "",
            ],
        )

    autosize(ws)
    return wb


def portal_access_filename(today: date | None = None) -> str:
    return f"portal-access-report-{(today or business_today()):%Y%m%d}.xlsx"
