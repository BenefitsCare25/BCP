"""One-time migration of existing per-client data from `public` into per-firm
schemas (Postgres). Needed only when upgrading a database that already held all
tenants' rows in `public` before schema-per-firm was introduced.

Dry-run by default — prints what *would* move. Pass --apply to execute. Each
firm is moved in a single transaction (atomic). Idempotent: re-running moves 0.

    # preview every firm
    cd backend && PYTHONPATH=. uv run python -m scripts.relocate_to_firm_schemas
    # execute for one firm
    ... -m scripts.relocate_to_firm_schemas --firm <id> --apply

Global rows (client_id NULL products/attributes) stay in `public` as the
canonical catalog and are copied into each firm schema by provisioning, so they
are NOT moved here. system-level audit rows (client_id NULL) also stay.
"""
from __future__ import annotations

import argparse

from sqlalchemy import select, text

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, schema_for_firm, shared_columns, sync_firm_schema
from app.models import (
    AISpendLog,
    AuditLog,
    BrokerFirm,
    Category,
    ClientAIConfig,
    Dependant,
    Employee,
    EmployeeAttributeSchema,
    PlacementSlipRow,
    Plan,
    PlanAttributeSchema,
    PolicyYear,
    Product,
)

_CLIENTS = "(SELECT id FROM public.clients WHERE broker_firm_id = :firm)"
_PYS = f"(SELECT id FROM public.policy_years WHERE client_id IN {_CLIENTS})"
_PRODS = f"(SELECT id FROM public.products WHERE client_id IN {_CLIENTS})"

# Parent -> child order for INSERT; reversed for DELETE. Filters reference
# public (data is only removed in the DELETE phase, after all INSERTs).
_MOVE_SPEC: list[tuple[str, str]] = [
    (PolicyYear.__tablename__, f"client_id IN {_CLIENTS}"),
    (Product.__tablename__, f"client_id IN {_CLIENTS}"),
    (PlanAttributeSchema.__tablename__, f"product_id IN {_PRODS}"),
    (EmployeeAttributeSchema.__tablename__, f"client_id IN {_CLIENTS}"),
    (Category.__tablename__, f"policy_year_id IN {_PYS}"),
    (Plan.__tablename__, f"policy_year_id IN {_PYS}"),
    (PlacementSlipRow.__tablename__, f"policy_year_id IN {_PYS}"),
    (Employee.__tablename__, f"client_id IN {_CLIENTS}"),
    (Dependant.__tablename__, f"client_id IN {_CLIENTS}"),
    (ClientAIConfig.__tablename__, f"client_id IN {_CLIENTS}"),
    (AISpendLog.__tablename__, f"client_id IN {_CLIENTS}"),
    (AuditLog.__tablename__, f"client_id IN {_CLIENTS}"),
]


def _firm_ids(specific: str | None) -> list[str]:
    with SessionLocal() as db:
        if specific:
            return [specific] if db.get(BrokerFirm, specific) else []
        return list(db.execute(select(BrokerFirm.id)).scalars().all())


def relocate_firm(firm_id: str, apply: bool) -> dict[str, int]:
    # Count (read-only) — no schema/table creation, so a dry-run never mutates.
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for tname, cond in _MOVE_SPEC:
            n = conn.execute(
                text(f"SELECT count(*) FROM public.{tname} WHERE {cond}"),
                {"firm": firm_id},
            ).scalar_one()
            if n:
                counts[tname] = n
    if not apply:
        return counts

    schema = sync_firm_schema(engine, firm_id)  # ensure schema + tables exist
    with engine.begin() as conn:
        for tname, cond in _MOVE_SPEC:  # parents first
            cols = shared_columns(conn, schema, tname)
            conn.execute(
                text(
                    f'INSERT INTO "{schema}".{tname} ({cols}) '
                    f"SELECT {cols} FROM public.{tname} WHERE {cond} "
                    f"ON CONFLICT (id) DO NOTHING"
                ),
                {"firm": firm_id},
            )
        for tname, cond in reversed(_MOVE_SPEC):  # children first
            conn.execute(
                text(f"DELETE FROM public.{tname} WHERE {cond}"), {"firm": firm_id}
            )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Relocate public data into firm schemas.")
    parser.add_argument("--firm", default=None, help="Single broker firm id (default: all).")
    parser.add_argument("--apply", action="store_true", help="Execute (default: dry-run).")
    args = parser.parse_args()

    if not is_postgres(engine):
        print("Database is not Postgres — nothing to relocate (single schema).")
        return

    firm_ids = _firm_ids(args.firm)
    if not firm_ids:
        print("No matching broker firms.")
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    for fid in firm_ids:
        counts = relocate_firm(fid, args.apply)
        total = sum(counts.values())
        print(f"[{mode}] firm {fid} -> {schema_for_firm(fid)}: {total} rows")
        for tname, n in counts.items():
            print(f"    {tname}: {n}")
    if not args.apply:
        print("\nDry-run only. Re-run with --apply to move the rows.")


if __name__ == "__main__":
    main()
