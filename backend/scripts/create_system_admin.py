"""Bootstrap (or promote) a platform system_admin.

system_admins have no broker firm and can create broker firms + cross-firm
manage. There's a chicken-and-egg otherwise: only a system_admin can create the
first firm, and only an admin can grant the role. Run this once per environment.

    cd backend && PYTHONPATH=. uv run python -m scripts.create_system_admin \
        --email ops@inspro.example --name "Platform Ops"

In Entra mode the user signs in normally; they're matched to this row by email
(the Entra oid is linked on first sign-in). Idempotent.
"""
from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from app.models import User
from app.models.user import USER_STATUS_ACTIVE


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a system_admin user.")
    parser.add_argument("--email", required=True, help="User email (login identity).")
    parser.add_argument("--name", default=None, help="Optional display name.")
    args = parser.parse_args()

    email = args.email.strip().lower()
    if "@" not in email:
        raise SystemExit(f"Invalid email: {email!r}")

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
