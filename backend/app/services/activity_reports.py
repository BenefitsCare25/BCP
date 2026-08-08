"""Sign-in and company activity reports.

Both are pure EXPORTS over logs that already exist — nothing here adds a new
form of logging:

- **Portal Activity** reads ``auth_events`` (``models/auth.py::AuthEvent``), the
  append-only sign-in trail written by every surface's auth flow.
- **Company Activity** reads ``audit_log``, the mutation trail every broker and
  member write already appends to.

Two things about ``auth_events`` shape this module:

**It is a CONTROL table** (``db/tenancy.CONTROL_TABLES``) — authentication has to
resolve before a firm schema is known, so the rows live in ``public`` and the
Postgres ``search_path`` does NOT scope them. Every query here therefore filters
``client_id`` explicitly. Leave that off and a broker downloads every firm's
sign-ins.

**A sign-in resolves to an ACCOUNT, not to a benefit year.** ``member_accounts``
is also a control table and outlives any one year's ``Employee`` row, so the
member's name comes from the ACCOUNT first and the roster only supplies the
legal entity. Resolving name-from-roster alone renders every event nameless for
anyone who has since left — which is exactly the population a security review
looks at.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, AuthEvent, MemberAccount, PolicyYear, User
from app.models.auth import SUBJECT_MEMBER, SUBJECT_USER
from app.services.insurer_reports import (
    append_safe,
    autosize,
    bold_header,
    naive,
    report_employees,
)
from app.services.roster_attributes import first_value

# Reader-facing label per stored event type. An unmapped value falls back to a
# de-underscored form rather than being dropped: a new event type must never
# make a row silently vanish from a security report.
_ACTIVITY_LABELS: dict[str, str] = {
    "login_success": "Portal Login",
    "login_fail": "Failed Login",
    "logout": "Logout",
    "mfa_challenge": "Two-Factor Challenge",
    "mfa_success": "Two-Factor Passed",
    "mfa_fail": "Two-Factor Failed",
    "lockout": "Account Locked",
    "password_reset_request": "Password Reset Requested",
    "password_reset_complete": "Password Reset Completed",
    "token_refresh": "Session Refreshed",
    "token_reuse_detected": "Session Token Reuse Detected",
}

# Which surface the event came from, in broker vocabulary. `portal` is the
# member's own surface; `hr` is the company's HR admin surface.
_SURFACE_LABELS: dict[str, str] = {
    "portal": "employee",
    "hr": "hr",
    "broker": "broker",
}

_OUTCOME_LABELS: dict[str, str] = {
    "success": "Success",
    "fail": "Failed",
    "blocked": "Blocked",
}

PORTAL_ACTIVITY_HEADER = [
    "Entity",
    "Staff ID",
    "Employee Name",
    "User Type",
    "Activity",
    "Outcome",
    "IP Address",
    "Timestamp",
]

COMPANY_ACTIVITY_HEADER = [
    "Timestamp",
    "Actor Type",
    "Actor",
    "Action",
    "Record Type",
    "Record ID",
    "Employee Staff ID",
    "Employee Name",
    "Detail",
    "Cross-Tenant Access",
]

# Fields worth printing from an audit row's `after` blob, in this order. The
# sheet used to stop at Record Type / Record ID, which turned every report
# download into `Export / Report Workbook / <policy-year-uuid>` — the same nine
# words for the underwriting register and an unmasked insurer submission, and
# the whole reason someone opens this export is to tell those two apart. The
# list is an ALLOW-list rather than a dump of `after`: that blob also carries
# per-row before/after diffs, i.e. the roster PII this sheet is not.
_DETAIL_KEYS = (
    "workbook", "report", "report_type", "insurer", "version_no", "masked",
    "employee_status", "filename", "terminate_missing",
    "added", "changed", "deleted", "missing_terminated",
)

_ENTITY_KEYS = ("entity", "company", "subsidiary")


def _label(mapping: dict[str, str], value: str | None) -> str:
    """Mapped label, else a readable fallback built from the raw value.

    Never returns "" for a non-empty input — an unrecognised event type is
    still an event, and a blank Activity cell reads as a corrupt row.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    return mapping.get(raw) or raw.replace("_", " ").title()


def _detail(after: object) -> str:
    """One readable line from an audit row's `after`, or blank.

    Booleans print as yes/no and a `None` is dropped entirely — an
    `insurer=None` on an internal register is not a fact about it, it is the
    field not applying.
    """
    if not isinstance(after, dict):
        return ""
    parts: list[str] = []
    for key in _DETAIL_KEYS:
        if key not in after:
            continue
        value = after[key]
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "yes" if value else "no"
        parts.append(f"{key.replace('_', ' ')}: {value}")
    return " · ".join(parts)


def range_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Half-open UTC window for an inclusive date range.

    The end is the START of the day AFTER ``end`` so an event at 23:59 on the
    last day is included — a naive ``<= end`` comparison against a timestamp
    column silently truncates the final day to midnight.
    """
    lo = datetime.combine(start, time.min, tzinfo=UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    return lo, hi


@dataclass(frozen=True)
class _Person:
    """Who an event's subject is, resolved once per report."""

    staff_id: str
    name: str


def _member_subjects(db: Session, client_id: str) -> dict[str, _Person]:
    """member_account.id → (staff id, display name), for this client only."""
    rows = db.execute(
        select(MemberAccount.id, MemberAccount.staff_id, MemberAccount.display_name)
        .where(MemberAccount.client_id == client_id)
    ).all()
    return {
        mid: _Person(staff_id=staff or "", name=(name or "").strip())
        for mid, staff, name in rows
    }


def _user_subjects(db: Session, user_ids: set[str]) -> dict[str, _Person]:
    """user.id → (blank staff id, display name/email). Platform users have no
    staff id — they are not on the roster."""
    if not user_ids:
        return {}
    rows = db.execute(
        select(User.id, User.display_name, User.email).where(User.id.in_(user_ids))
    ).all()
    return {
        uid: _Person(staff_id="", name=(name or email or "").strip())
        for uid, name, email in rows
    }


def _entity_by_staff(db: Session, py: PolicyYear) -> dict[str, str]:
    """staff id → legal entity, from the year's roster.

    The roster supplies the ENTITY only. Names come from the account (see the
    module docstring) so a leaver's sign-ins stay attributable.
    """
    out: dict[str, str] = {}
    for emp in report_employees(db, py):
        entity = first_value(emp.attribute_values or {}, _ENTITY_KEYS)
        if emp.staff_id:
            out[emp.staff_id] = entity or ""
    return out


def _roster_names(db: Session, py: PolicyYear) -> dict[str, str]:
    """staff id → roster name, the fallback when an account carries no display
    name (accounts provisioned from a roster row without one)."""
    return {
        emp.staff_id: (emp.employee_name or "")
        for emp in report_employees(db, py)
        if emp.staff_id
    }


def build_portal_activity_workbook(
    db: Session, py: PolicyYear, start: date, end: date
) -> Workbook:
    """Sign-in activity for the client over an inclusive date range.

    Covers EVERY surface and every event type, not just successful member
    logins: a report that hides failed logins and lockouts cannot answer the
    question it exists for.
    """
    lo, hi = range_bounds(start, end)
    events = list(
        db.execute(
            select(AuthEvent)
            # client_id is the ONLY tenant scope here — auth_events is a control
            # table, so the Postgres search_path does not constrain it.
            .where(
                AuthEvent.client_id == py.client_id,
                AuthEvent.occurred_at >= lo,
                AuthEvent.occurred_at < hi,
            )
            .order_by(AuthEvent.occurred_at.desc())
        )
        .scalars()
        .all()
    )

    members = _member_subjects(db, py.client_id)
    users = _user_subjects(
        db,
        {
            e.subject_id
            for e in events
            if e.subject_type == SUBJECT_USER and e.subject_id
        },
    )
    entities = _entity_by_staff(db, py)
    roster_names = _roster_names(db, py)

    wb = Workbook()
    ws = wb.active
    ws.title = "Portal Sign-ins"
    append_safe(ws, PORTAL_ACTIVITY_HEADER)
    bold_header(ws)

    for ev in events:
        person: _Person | None = None
        if ev.subject_id:
            if ev.subject_type == SUBJECT_MEMBER:
                person = members.get(ev.subject_id)
            elif ev.subject_type == SUBJECT_USER:
                person = users.get(ev.subject_id)
        staff_id = person.staff_id if person else ""
        # Account name wins; roster name is the fallback for an account with no
        # display name. A failed login with no resolved subject stays blank —
        # we deliberately store only a hash of what was typed.
        name = (person.name if person else "") or roster_names.get(staff_id, "")
        append_safe(
            ws,
            [
                entities.get(staff_id, ""),
                staff_id,
                name,
                _label(_SURFACE_LABELS, ev.surface),
                _label(_ACTIVITY_LABELS, ev.event_type),
                _label(_OUTCOME_LABELS, ev.outcome),
                ev.ip or "",
                naive(ev.occurred_at),
            ],
        )

    autosize(ws)
    return wb


def build_company_activity_workbook(
    db: Session, py: PolicyYear, start: date, end: date
) -> Workbook:
    """Configuration + administration activity for the client over a date range.

    The incumbent platform has a Company Activities tab that has never been
    generated, so there is no layout to match. This is the ``audit_log`` — who
    changed what, when — which is the strictly richer answer to the same
    question.
    """
    lo, hi = range_bounds(start, end)
    rows = list(
        db.execute(
            select(AuditLog)
            .where(
                AuditLog.client_id == py.client_id,
                AuditLog.created_at >= lo,
                AuditLog.created_at < hi,
            )
            .order_by(AuditLog.created_at.desc())
        )
        .scalars()
        .all()
    )

    members = _member_subjects(db, py.client_id)
    users = _user_subjects(db, {r.user_id for r in rows if r.user_id})
    employees = {
        emp.id: emp for emp in report_employees(db, py)
    }

    wb = Workbook()
    ws = wb.active
    ws.title = "Company Changes"
    append_safe(ws, COMPANY_ACTIVITY_HEADER)
    bold_header(ws)

    for row in rows:
        # actor_type is NULL on rows predating the portal; those are all broker
        # users (see the model comment), so default rather than print blank.
        actor_type = (row.actor_type or "user").strip()
        if actor_type == "member":
            actor = members.get(row.member_account_id or "")
        else:
            actor = users.get(row.user_id or "")
        emp = employees.get(row.employee_id or "")
        append_safe(
            ws,
            [
                naive(row.created_at),
                "Member" if actor_type == "member" else "Platform user",
                actor.name if actor else "",
                _label({}, row.action),
                _label({}, row.entity_type),
                row.entity_id or "",
                emp.staff_id if emp else "",
                (emp.employee_name or "") if emp else "",
                _detail(row.after),
                "Yes" if row.cross_tenant_access else "",
            ],
        )

    autosize(ws)
    return wb
