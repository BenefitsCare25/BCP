"""Provision/sync a Postgres schema for every broker firm. Idempotent.

Run on deploy. Creates each firm's schema, any *missing* tenant tables, and
*adds missing columns* to existing tenant tables (additive migrations). On
SQLite this is a no-op.

    cd backend && PYTHONPATH=. uv run python -m scripts.provision_tenants

NOTE: covers new tables + new columns. Drops, renames, type changes, and data
migrations to tenant tables need a bespoke per-schema step (see DEPLOY_RUNBOOK).
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, sync_firm_schema
from app.models import BrokerFirm
from app.services.claims_review.recovery import reconcile_legacy_reviews


def main() -> None:
    if not is_postgres(engine):
        print("Database is not Postgres — firm schemas are a no-op here.")
        return
    with SessionLocal() as db:
        firm_ids = list(db.execute(select(BrokerFirm.id)).scalars().all())
    for fid in firm_ids:
        schema = sync_firm_schema(engine, fid)
        reconciled = reconcile_legacy_reviews(fid)
        print(f"  synced {schema}; reconciled {reconciled} legacy review(s)")
    print(f"Done: {len(firm_ids)} firm schema(s) provisioned/synced.")


if __name__ == "__main__":
    main()
