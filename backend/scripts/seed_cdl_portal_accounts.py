"""Provision employee-portal member accounts for real CDL roster employees
(LOCAL DEV ONLY — so the portal can be tested with real profiles instead of
the seeded dummy accounts).

What it does, idempotently:

1. Finds the CDL client + its policy year, and ACTIVATES that year if it's
   still a draft (the portal's `resolve_member_employee` hard-requires an
   active year — nothing renders otherwise).
2. Creates a `MemberAccount` (status=active) for the first N active CDL
   employees that have a valid roster email and no account yet, stamping
   `employees.member_account_id` exactly like the broker bulk-invite flow.
3. Prints the sign-in list.

Sign-in (dev+mock): POST /portal/auth/request-code returns a `debug_code`
in the response (and the portal sign-in screen shows it) — no real email is
sent (mail runs in log/mock mode locally).

Usage:
    cd backend && PYTHONPATH=. uv run python scripts/seed_cdl_portal_accounts.py [COUNT]

COUNT defaults to 10. Re-running only fills gaps; existing accounts are kept.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Client, Employee, MemberAccount, PolicyYear
from app.models.member_account import MEMBER_STATUS_ACTIVE
from app.models.policy_year import PolicyYearStatus
from app.services.roster_attributes import EMAIL_KEYS, first_value

CDL_NAME = "CDL"


def _valid_email(raw: str | None) -> str | None:
    if not raw:
        return None
    email = raw.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        return None
    return email


def main(count: int) -> int:
    db = SessionLocal()
    try:
        client = db.execute(
            select(Client).where(Client.name == CDL_NAME)
        ).scalars().first()
        if client is None:
            print(f"ERROR: no client named {CDL_NAME!r} in this database.")
            return 1

        # The CDL policy year (most recent by start date).
        year = db.execute(
            select(PolicyYear)
            .where(PolicyYear.client_id == client.id)
            .order_by(PolicyYear.start_date.desc())
        ).scalars().first()
        if year is None:
            print(f"ERROR: {CDL_NAME} has no policy year.")
            return 1

        prev_status = getattr(year.status, "value", year.status)
        if year.status != PolicyYearStatus.active:
            year.status = PolicyYearStatus.active
            print(
                f"Activated CDL policy year {year.year} ({year.id}) "
                f"[was {prev_status}]."
            )
        else:
            print(f"CDL policy year {year.year} ({year.id}) already active.")

        # Accounts already provisioned for this client (skip, don't duplicate).
        existing = db.execute(
            select(MemberAccount).where(MemberAccount.client_id == client.id)
        ).scalars().all()
        known_emails = {a.email for a in existing}
        known_staff = {a.staff_id for a in existing}

        employees = db.execute(
            select(Employee)
            .where(
                Employee.client_id == client.id,
                Employee.policy_year_id == year.id,
                Employee.status == "active",
            )
            .order_by(Employee.staff_id)
        ).scalars().all()

        provisioned: list[tuple[MemberAccount, str]] = []
        for emp in employees:
            if len(provisioned) >= count:
                break
            email = _valid_email(first_value(emp.attribute_values or {}, EMAIL_KEYS))
            if email is None:
                continue
            if email in known_emails or emp.staff_id in known_staff:
                continue
            account = MemberAccount(
                client_id=client.id,
                email=email,
                staff_id=emp.staff_id,
                display_name=emp.employee_name,
                status=MEMBER_STATUS_ACTIVE,
                invited_by=None,
            )
            db.add(account)
            db.flush()
            emp.member_account_id = account.id
            known_emails.add(email)
            known_staff.add(emp.staff_id)
            provisioned.append((account, emp.employee_name or ""))

        db.commit()

        # Report — new this run, plus the full active set for the client.
        active_accounts = db.execute(
            select(MemberAccount)
            .where(
                MemberAccount.client_id == client.id,
                MemberAccount.status == MEMBER_STATUS_ACTIVE,
            )
            .order_by(MemberAccount.staff_id)
        ).scalars().all()

        print(f"\nProvisioned {len(provisioned)} new account(s) this run.")
        print(f"CDL portal sign-in accounts ({len(active_accounts)} total):\n")
        print(f"  {'STAFF ID':<10} {'NAME':<28} EMAIL (sign-in)")
        print(f"  {'-' * 10} {'-' * 28} {'-' * 30}")
        for a in active_accounts:
            print(f"  {a.staff_id:<10} {(a.display_name or ''):<28} {a.email}")
        print(
            "\nSign in at http://localhost:5173/portal/sign-in — enter an email "
            "above;\nthe one-time code is returned as `debug_code` (shown on "
            "screen in dev)."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    raise SystemExit(main(n))
