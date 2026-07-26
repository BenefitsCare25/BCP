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

**Postgres: these are TENANT tables, so the library must be seeded into every
firm schema, not just ``public``.** ``set_search_path`` resolves an unqualified
table name to ``firm_<id>``, and Postgres never falls through to ``public`` — so
seeding only ``public`` leaves every dropdown in the app empty while a SQLite dev
box looks perfectly fine. On SQLite there are no schemas and the per-firm loop is
a no-op. Same reasoning as scripts/seed_insurers.py, which this reuses.

A firm must EXIST before it can be seeded: its schema is created by
`provision_firm_schema` when the firm is created through /admin/broker-firms.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, set_search_path
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


def seed_library(db: Session) -> None:
    """Seed all three catalogs into whatever schema `db` currently points at."""
    a_new, a_upd = _seed_attributes(db)
    p_new, p_upd = _seed_products(db)
    db.commit()
    i_new, i_upd = seed_insurers(db)  # commits internally
    print(
        f"    attributes: {a_new} created, {a_upd} updated | "
        f"products: {p_new} created, {p_upd} updated | "
        f"insurers: {i_new} created, {i_upd} updated"
    )


def seed_all_schemas(firm_id: str | None = None) -> None:
    """Seed `public`, plus each firm schema on Postgres."""
    print("public schema:")
    with SessionLocal() as db:
        seed_library(db)

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
            "No broker firms exist yet — create one first (Access & Companies, "
            "or POST /admin/broker-firms). The library lives in each firm's "
            "schema, so there is nowhere to put it until a firm exists."
        )

    for fid in firm_ids:
        # A fresh session per firm: set_search_path is connection state, so
        # reusing one session would leave the previous firm's path set.
        print(f"firm {fid}:")
        with SessionLocal() as db:
            set_search_path(db, fid)
            seed_library(db)


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
