"""Bootstrap the platform master admin, and optionally the platform owner's firm.

Run once per environment. The master admin (`system_admin`) has no broker firm
of its own: it has standing access to the platform owner's firm (Inspro's own)
and reaches any other firm only through a time-limited access grant. Further
broker firms are created in the platform console; `--firm-name` creates the
platform owner's firm on a fresh deployment, which has no console host to
reach until that firm exists.

    cd backend && PYTHONPATH=. uv run python -m scripts.create_system_admin \
        --email ops@inspro.com.sg --name "Platform Ops" \
        --entra-object-id <verified-user-object-id> \
        --firm-name "Inspro Insurance Broker" --firm-slug inspro-broker

Then seed that firm's reference library (attributes / products / insurers):

    cd backend && PYTHONPATH=. uv run python scripts/seed_firm_library.py

In Entra mode, a new or unlinked admin requires the verified Entra user object
ID from the platform directory (`INSPRO_ENTRA_TENANT_ID`); the binding records
that directory, since an object ID is unique only within its own. Email claims
never establish broker access. Re-running preserves an existing binding and
never duplicates the firm or demotes the admin.
"""

from __future__ import annotations

import argparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.platform import derive_firm_slug, validate_firm_slug
from app.core.sessions import revoke_all_for_subject
from app.core.settings import get_settings
from app.core.tenancy_host import SlugError
from app.db.session import SessionLocal
from app.db.tenancy import provision_firm_schema
from app.models import BrokerFirm, User
from app.models.user import USER_STATUS_ACTIVE


def _ensure_firm(name: str, slug: str) -> None:
    """Create the platform owner's firm on a fresh deployment.

    With firms already present nothing is created; a deployment whose firms
    predate the owner flag has its sole firm marked as the owner (with a slug,
    if it has none). Exactly one firm owns the platform.

    The schema is provisioned on the same connection and transaction as the
    row: an orphaned firm (row with no schema) fails every login for it, so a
    provisioning failure must roll the row back. A second connection would
    also wait forever on this transaction's uncommitted firm row (tenant tables
    reference broker_firms). Mirrors api/v1/platform.py::create_firm.
    No-op on SQLite.
    """
    with SessionLocal() as db:
        existing = list(db.execute(select(BrokerFirm)).scalars().all())
        if existing:
            owner = next((f for f in existing if f.is_platform_owner), None)
            if owner is None and len(existing) == 1:
                owner = existing[0]
                owner.is_platform_owner = True
                if not owner.slug:
                    owner.slug = slug
                db.commit()
                print(f"marked {owner.name} ({owner.slug}) as the platform owner's firm.")
                return
            names = ", ".join(f.name for f in existing)
            print(f"Broker firm already exists ({names}) — leaving it alone.")
            return
        firm = BrokerFirm(name=name, slug=slug, is_platform_owner=True)
        db.add(firm)
        db.flush()
        provision_firm_schema(db.connection(), firm.id)
        db.commit()
        print(f"created the platform owner's firm: {name} ({firm.slug}, {firm.id})")
        print("  next: PYTHONPATH=. uv run python scripts/seed_firm_library.py")


def _find_account(db: Session, email: str) -> User | None:
    """The account to promote: a platform admin's first, else the only one.

    Email is unique per firm, so several firms may each hold this address;
    promoting one of those would be a guess, so it is refused.
    """
    rows = db.query(User).filter(User.email == email).all()
    platform = next((u for u in rows if u.broker_firm_id is None), None)
    if platform is not None:
        return platform
    if len(rows) > 1:
        raise SystemExit(
            "Several broker firms have an account with this email. "
            "Use another email for the platform admin."
        )
    return rows[0] if rows else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a system_admin user.")
    parser.add_argument("--email", required=True, help="User email (login identity).")
    parser.add_argument("--name", default=None, help="Optional display name.")
    parser.add_argument("--entra-object-id", help="Verified Microsoft Entra user object ID.")
    parser.add_argument(
        "--firm-name",
        default=None,
        help="Create the platform owner's broker firm with this name if no firm exists.",
    )
    parser.add_argument(
        "--firm-slug",
        default=None,
        help="Slug for that firm (a DNS label); derived from --firm-name when omitted.",
    )
    args = parser.parse_args()
    firm_slug = None
    if args.firm_name:
        try:
            firm_slug = validate_firm_slug(
                args.firm_slug or derive_firm_slug(args.firm_name.strip())
            )
        except SlugError as exc:
            parser.error(f"--firm-slug: {exc}")
    try:
        oid = str(UUID(args.entra_object_id)) if args.entra_object_id else None
    except ValueError:
        parser.error("--entra-object-id must be a valid UUID")

    email = args.email.strip().lower()
    if "@" not in email:
        raise SystemExit(f"Invalid email: {email!r}")

    settings = get_settings()
    # The platform admin signs in through the platform directory.
    directory = settings.entra_tenant_id or None
    with SessionLocal() as db:
        user = _find_account(db, email)
        if settings.auth_mode == "entra" and not (oid or (user and user.external_id)):
            parser.error("--entra-object-id is required for a new or unlinked Microsoft admin")
        if user and user.external_tid and directory and user.external_tid != directory:
            raise SystemExit(
                "Account is bound to a Microsoft identity in another directory; "
                "the platform admin signs in through the platform directory."
            )
        if oid and db.query(User).filter(User.external_id == oid, User.email != email).first():
            parser.error("Microsoft identity is already registered to another account")
        if user is None:
            db.add(
                User(
                    external_id=oid,
                    external_tid=directory if oid else None,
                    email=email,
                    display_name=args.name,
                    broker_firm_id=None,
                    role="system_admin",
                    status=USER_STATUS_ACTIVE,
                )
            )
            action = "created"
        else:
            changed = (
                user.role != "system_admin"
                or user.status != USER_STATUS_ACTIVE
                or user.broker_firm_id is not None
                or (oid and user.external_id != oid)
            )
            if oid:
                if user.external_id and user.external_id != oid:
                    raise SystemExit("Account is already bound to another Microsoft identity.")
                user.external_id = oid
            if user.external_id and directory:
                user.external_tid = directory
            user.role = "system_admin"
            user.broker_firm_id = None
            user.status = USER_STATUS_ACTIVE
            if args.name:
                user.display_name = args.name
            if changed:
                revoke_all_for_subject(db, "broker", user.id)
                revoke_all_for_subject(db, "user", user.id)
            action = "promoted existing user to"
        db.commit()
    if args.firm_name and firm_slug:
        _ensure_firm(args.firm_name.strip(), firm_slug)
    print(f"{action} system_admin: {email}")


if __name__ == "__main__":
    main()
