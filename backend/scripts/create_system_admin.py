"""Bootstrap a platform system_admin, and optionally the broker firm itself.

Run once per environment. A system_admin has no broker firm of their own and
manages across firms — but on this deployment there is exactly ONE firm, because
Inspro owns the platform rather than being one tenant among many. That is why
`--firm-name` lives here: there is deliberately no create-firm UI, so without it
a fresh deployment would have no firm and nothing could be configured.

    cd backend && PYTHONPATH=. uv run python -m scripts.create_system_admin \
        --email ops@inspro.com.sg --name "Platform Ops" \
        --firm-name "Inspro Insurance Broker"

Then seed that firm's reference library (attributes / products / insurers):

    cd backend && PYTHONPATH=. uv run python scripts/seed_firm_library.py

In Entra mode the user signs in normally; they're matched to this row by email
(the Entra oid is linked on first sign-in). Idempotent — re-running neither
duplicates the firm nor demotes the admin.
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db.session import SessionLocal, engine
from app.db.tenancy import provision_firm_schema
from app.models import BrokerFirm, User
from app.models.user import USER_STATUS_ACTIVE


def _ensure_firm(name: str) -> None:
    """Create the platform's single broker firm, if it doesn't exist yet.

    Inspro owns this platform rather than being one tenant among many, so there
    is deliberately no create-firm UI — which means bootstrap has to happen here
    or a fresh deployment has no firm and nothing can be configured.

    The schema is provisioned BEFORE the row is committed: an orphaned firm (row
    with no schema) 500s every login for it, so a provisioning failure must roll
    the row back rather than leave that behind. Mirrors the same ordering in
    api/v1/admin.py::create_broker_firm. No-op on SQLite.
    """
    with SessionLocal() as db:
        existing = list(db.execute(select(BrokerFirm)).scalars().all())
        if existing:
            names = ", ".join(f.name for f in existing)
            print(f"Broker firm already exists ({names}) — leaving it alone.")
            return
        firm = BrokerFirm(name=name)
        db.add(firm)
        db.flush()
        provision_firm_schema(engine, firm.id)
        db.commit()
        print(f"created broker firm: {name} ({firm.id})")
        print("  next: PYTHONPATH=. uv run python scripts/seed_firm_library.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a system_admin user.")
    parser.add_argument("--email", required=True, help="User email (login identity).")
    parser.add_argument("--name", default=None, help="Optional display name.")
    parser.add_argument(
        "--firm-name",
        default=None,
        help="Create the platform's broker firm with this name if none exists.",
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    if "@" not in email:
        raise SystemExit(f"Invalid email: {email!r}")

    if args.firm_name:
        _ensure_firm(args.firm_name.strip())

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one_or_none()
        if user is None:
            db.add(
                User(
                    external_id=None,
                    email=email,
                    display_name=args.name,
                    broker_firm_id=None,
                    role="system_admin",
                    status=USER_STATUS_ACTIVE,
                )
            )
            action = "created"
        else:
            user.role = "system_admin"
            user.broker_firm_id = None
            user.status = USER_STATUS_ACTIVE
            if args.name:
                user.display_name = args.name
            action = "promoted existing user to"
        db.commit()
    print(f"{action} system_admin: {email}")


if __name__ == "__main__":
    main()
