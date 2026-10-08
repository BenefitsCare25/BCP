"""Provision/sync a Postgres schema for every broker firm. Idempotent.

Run on deploy. Creates each firm's schema, any *missing* tenant tables, and
*adds missing columns* to existing tenant tables (additive migrations). On
SQLite this is a no-op.

Also the provisioning step for firms created while
``INSPRO_RUNTIME_PROVISIONING`` is off (D13): such a firm has a row but no
schema, and its requests return 503 ``tenant_unavailable`` until this runs as
the schema owner (the migration job). Each run also re-applies the runtime
role's grants on every firm schema (``db/roles.grant_firm_schema``).

    cd backend && PYTHONPATH=. uv run python -m scripts.provision_tenants

NOTE: covers new tables + new columns. Drops, renames, type changes, and data
migrations to tenant tables need a bespoke per-schema step (see the deployment
operations section in docs/PRODUCTION_RESILIENCE_RUNBOOK.md).
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, pending_firm_ids, sync_firm_schema
from app.models import BrokerFirm
from app.services.claims_review.recovery import reconcile_legacy_reviews


def main() -> None:
    if not is_postgres(engine):
        print("Database is not Postgres — firm schemas are a no-op here.")
        return
    with SessionLocal() as db:
        firm_ids = list(db.execute(select(BrokerFirm.id)).scalars().all())
    with engine.connect() as conn:
        pending = set(pending_firm_ids(conn))
    if pending:
        print(f"  {len(pending)} firm(s) awaiting provisioning: {', '.join(sorted(pending))}")
    for fid in firm_ids:
        schema = sync_firm_schema(engine, fid)
        reconciled = reconcile_legacy_reviews(fid)
        action = "provisioned" if fid in pending else "synced"
        print(f"  {action} {schema}; reconciled {reconciled} legacy review(s)")
    print(f"Done: {len(firm_ids)} firm schema(s) provisioned/synced.")


if __name__ == "__main__":
    main()
