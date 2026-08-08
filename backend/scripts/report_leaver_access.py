"""Who loses portal access when the leaver gate goes live. READ-ONLY.

Run this BEFORE deploying `docs/LEAVER_ACCESS_PLAN.md`. Until now a terminated
employee kept their portal indefinitely; from the release, everyone whose
run-off has already expired is refused in one step. That is the fix, but it must
be observable first — a broker should be able to tell their client which people
are affected rather than learning about it from a support call.

Usage::

    cd backend && PYTHONPATH=. uv run python scripts/report_leaver_access.py
    ... --client <client_id>      # one company
    ... --csv out.csv             # machine-readable

Nothing is written. `access_map` is the same resolver the live gate and the
broker account list use, so what this prints is what will happen — not a second
implementation of the rule that could disagree with it.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

from app.db.session import SessionLocal
from app.db.tenancy import set_search_path
from app.models import Client, MemberAccount
from app.models.member_account import MEMBER_STATUS_DISABLED
from app.services.member_access import access_map

# Only these are worth a broker's attention. `active` is everyone's normal
# state, and `unknown` means we could not find a roster row — usually a benefit
# year that has not been uploaded, which is not a leaver and must not be
# reported as one.
REPORTABLE = ("ended", "settling", "run_off")


def _rows(session, client: Client) -> list[dict[str, str]]:
    # Accounts live in `public`, the roster in the firm schema — resolving
    # access reads both, so the search path has to be set per client.
    set_search_path(session, client.broker_firm_id)
    accounts = (
        session.query(MemberAccount)
        .filter(
            MemberAccount.client_id == client.id,
            # A disabled account is already shut out by hand; reporting it as
            # "about to lose access" would be describing a change that isn't one.
            MemberAccount.status != MEMBER_STATUS_DISABLED,
        )
        .order_by(MemberAccount.staff_id)
        .all()
    )
    access = access_map(session, client.id, accounts)
    out = []
    for account in accounts:
        state = access.get(account.id)
        if state is None or state.state not in REPORTABLE:
            continue
        out.append(
            {
                "company": client.name,
                "staff_id": account.staff_id,
                "name": account.display_name or "",
                "email": account.email or "",
                "state": state.state,
                "last_day": state.last_day.isoformat() if state.last_day else "",
                "access_ends_on": (
                    state.access_ends_on.isoformat() if state.access_ends_on else ""
                ),
                "signed_in_before": (
                    "yes" if account.last_sign_in_at else "no"
                ),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", help="Limit to one client id.")
    parser.add_argument("--csv", help="Write rows to this file as well.")
    args = parser.parse_args()

    rows: list[dict[str, str]] = []
    with SessionLocal() as session:
        query = session.query(Client)
        if args.client:
            query = query.filter(Client.id == args.client)
        for client in query.order_by(Client.name).all():
            rows.extend(_rows(session, client))

    counts = Counter(r["state"] for r in rows)
    print(f"{len(rows)} member accounts are no longer fully active:")
    for state in REPORTABLE:
        if counts[state]:
            print(f"  {state:<9} {counts[state]}")
    # The one that is a CHANGE. The other two keep working; only `ended` starts
    # refusing on the day this ships, and only the people who have actually used
    # the portal will notice.
    ending = [r for r in rows if r["state"] == "ended"]
    seen = [r for r in ending if r["signed_in_before"] == "yes"]
    print(
        f"\n{len(ending)} lose access on deploy, of whom {len(seen)} have signed "
        "in before."
    )
    for r in sorted(ending, key=lambda r: (r["company"], r["staff_id"])):
        print(
            f"  {r['company']:<20} {r['staff_id']:<14} {r['name'][:28]:<28} "
            f"last day {r['last_day'] or '?':<12} access ended "
            f"{r['access_ends_on'] or '?'}"
        )

    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
