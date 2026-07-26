"""Seed the firm-library reference data into a real (non-demo) deployment.

Seeds the three ``client_id IS NULL`` catalogs a broker firm needs before it can
configure anything:

- employee attribute schema  (the Singapore default roster fields)
- product catalog            (which insurance lines exist)
- insurer name library       (the dropdown vocabulary)

This exists because ``seed_demo.py`` is the only other thing that writes them,
and it ALSO creates a demo broker firm, two demo clients, demo users and a demo
policy year — none of which belong in production. The catalogs themselves are not
demo data: they are the Singapore defaults, which is why they are imported from
there rather than duplicated.

    cd backend && PYTHONPATH=. uv run python scripts/seed_firm_library.py
    cd backend && PYTHONPATH=. uv run python scripts/seed_firm_library.py --firm <firm-id>

Idempotent and re-runnable — existing rows are refreshed in place, never
duplicated, so it is safe to run after every schema change to pick up newly
added attributes or a product rename.

**Postgres: these are TENANT tables, so the app reads them from ``firm_<id>``,
not ``public``.** ``set_search_path`` resolves an unqualified table name to the
firm schema and Postgres does not fall through, so ``public`` alone leaves every
dropdown empty while a SQLite dev box looks perfectly fine.

Each firm schema is brought up to date in two steps, and **the order matters** —
getting it wrong either doubles the catalogs or leaves edits stranded in
``public``. See ``seed_all_schemas`` for why. On SQLite there are no schemas and
the per-firm step is skipped entirely.

A firm must EXIST before it can be seeded — create it with
``scripts/create_system_admin.py --firm-name``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, provision_firm_schema, set_search_path
from app.models import BrokerFirm, EmployeeAttributeSchema, Product
from scripts.seed_demo import PRODUCT_CATALOG, SINGAPORE_ATTRIBUTES
from scripts.seed_insurers import seed_insurers


def _seed_attributes(db: Session) -> tuple[int, int]:
    """Upsert the default attribute schema. Returns (created, updated)."""
    existing = {
        a.attribute_id: a
        for a in db.query(EmployeeAttributeSchema)
        .filter(EmployeeAttributeSchema.client_id.is_(None))
        .all()
    }
    created = updated = 0
    for spec in SINGAPORE_ATTRIBUTES:
        row = existing.get(spec["attribute_id"])
        if row is None:
            db.add(EmployeeAttributeSchema(client_id=None, **spec))
            created += 1
            continue
        # Only the derivation logic is refreshed, matching seed_demo: label and
        # type may have been edited by the broker, and overwriting those would
        # discard their work on every re-run.
        row.derived_from = spec.get("derived_from")
        row.derivation_rule = spec.get("derivation_rule")
        updated += 1
    return created, updated


def _seed_products(db: Session) -> tuple[int, int]:
    """Upsert the product catalog. Returns (created, updated)."""
    existing = {
        p.code: p for p in db.query(Product).filter(Product.client_id.is_(None)).all()
    }
    created = updated = 0
    for spec in PRODUCT_CATALOG:
        row = existing.get(spec["code"])
        if row is None:
            db.add(Product(client_id=None, **spec))
            created += 1
            continue
        # Full re-sync here (unlike attributes): a registry rename such as
        # OSI -> Group Secondment Insurance must reach databases seeded before
        # the change, and these fields are catalog facts rather than broker input.
        for field_name, value in spec.items():
            setattr(row, field_name, value)
        updated += 1
    return created, updated


def _seed_catalogs(db: Session) -> str:
    """Attributes + products into whatever schema `db` points at. Commits."""
    a_new, a_upd = _seed_attributes(db)
    p_new, p_upd = _seed_products(db)
    db.commit()
    return (
        f"attributes: {a_new} created, {a_upd} updated | "
        f"products: {p_new} created, {p_upd} updated"
    )


def _seed_one_schema(firm_id: str | None) -> str:
    """Seed every catalog into one schema (None = `public`).

    Each step gets its OWN session with the search path re-applied. `db.commit()`
    returns the connection to the pool, and the checkin listener in
    app/db/session.py resets `search_path` to `public` — so a single session that
    commits midway would silently write the rest to the wrong schema.
    """
    with SessionLocal() as db:
        if firm_id:
            set_search_path(db, firm_id)
        catalogs = _seed_catalogs(db)
    with SessionLocal() as db:
        if firm_id:
            set_search_path(db, firm_id)
        i_new, i_upd = seed_insurers(db)
    return f"{catalogs} | insurers: {i_new} created, {i_upd} updated"


def seed_all_schemas(firm_id: str | None = None) -> None:
    """Seed `public`, then propagate into each firm schema on Postgres.

    ORDER IS LOAD-BEARING: public -> provision_firm_schema -> seed the firm.

    `provision_firm_schema` copies the `client_id IS NULL` rows out of `public`
    (app/db/tenancy.py) with `ON CONFLICT (id) DO NOTHING`. That has two
    consequences which together dictate the sequence:

    - Seeding a firm schema BEFORE the copy inserts rows with fresh UUIDs, and
      the copy then adds public's rows alongside them — the id-based conflict
      clause cannot see they are the same catalog entry, so every dropdown
      silently DOUBLES (this happened to prod: 48 attributes, 50 products).
    - The copy only ever INSERTS. A row it skips is never refreshed, so an
      edited catalog entry — a renamed product, a corrected derivation_rule —
      would reach `public` and stop there, while the app reads the firm schema
      and keeps the stale value.

    Copying first and then seeding resolves both: the copy brings new rows in at
    their canonical ids, and the seed matches on the NATURAL key (attribute_id /
    code / name) so it updates those rows in place and inserts nothing.

    Insurers are seeded per-firm for a different reason: `insurers` is not in the
    copy list at all, so seeding only `public` leaves the firm's table empty and
    Postgres resolves an unqualified `insurers` to the firm schema without
    falling through — the insurer dropdown comes up blank.
    """
    print("public schema:")
    print(f"    {_seed_one_schema(None)}")

    if not is_postgres(engine):
        # SQLite has no schemas — the loop below would re-seed the same tables.
        return

    with SessionLocal() as db:
        if firm_id:
            if db.get(BrokerFirm, firm_id) is None:
                raise SystemExit(f"No broker firm with id {firm_id!r}.")
            firm_ids = [firm_id]
        else:
            firm_ids = list(db.execute(select(BrokerFirm.id)).scalars().all())

    if not firm_ids:
        raise SystemExit(
            "No broker firms exist yet — create one first with\n"
            "  scripts/create_system_admin.py --firm-name \"<name>\"\n"
            "The library lives in each firm's schema, so there is nowhere to "
            "put it until a firm exists."
        )

    for fid in firm_ids:
        print(f"firm {fid}:")
        # 1. Copy any NEW catalog rows down from public, at canonical ids.
        provision_firm_schema(engine, fid)
        # 2. Then seed, which matches on the natural key — so it refreshes the
        #    rows the copy skipped and inserts nothing.
        print(f"    {_seed_one_schema(fid)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--firm",
        default=None,
        help="Seed only this broker firm's schema (default: every firm).",
    )
    args = parser.parse_args()
    seed_all_schemas(args.firm)
    print("Done.")


if __name__ == "__main__":
    main()
