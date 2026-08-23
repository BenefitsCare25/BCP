"""Seed the shared Singapore insurer library (client_id NULL rows).

Idempotent and re-runnable: an existing library row with the same name is
updated in place (legal name / aliases / notes refreshed), never duplicated,
and a row's ``name`` is never rewritten — that string is what products already
store, so changing it here would silently orphan them.

    cd backend && PYTHONPATH=. uv run python scripts/seed_insurers.py

**Postgres: the library must be seeded into EVERY firm schema.** ``insurers``
is a tenant table (not in ``CONTROL_TABLES``), so each firm gets its own copy
via ``sync_firm_schema``, and ``set_search_path`` resolves an unqualified
``insurers`` to ``firm_<id>`` — Postgres picks the first schema on the path
that has the table and never falls through to ``public``. Seeding only
``public`` would leave every broker's dropdown empty. On SQLite there are no
schemas, so the per-firm loop is a no-op and this seeds once.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, set_search_path
from app.models import BrokerFirm, Insurer
from app.services.insurer_catalog import SG_INSURERS


def seed_insurers(db: Session) -> tuple[int, int]:
    """Upsert every library entry. Returns (created, updated)."""
    existing = {
        row.name.strip().lower(): row
        for row in db.execute(
            select(Insurer).where(Insurer.client_id.is_(None))
        ).scalars()
    }
    created = updated = 0
    for entry in SG_INSURERS:
        row = existing.get(entry["name"].strip().lower())
        if row is None:
            db.add(
                Insurer(
                    client_id=None,
                    name=entry["name"],
                    legal_name=entry.get("legal_name"),
                    aliases=entry.get("aliases") or None,
                    notes=entry.get("notes"),
                )
            )
            created += 1
            continue
        row.legal_name = entry.get("legal_name")
        row.aliases = entry.get("aliases") or None
        row.notes = entry.get("notes")
        updated += 1
    db.commit()
    return created, updated


def seed_all_schemas() -> None:
    """Seed `public` plus every firm schema (Postgres). SQLite seeds once."""
    with SessionLocal() as db:
        created, updated = seed_insurers(db)
    print(f"Insurer library (public): {created} created, {updated} updated.")

    if not is_postgres(engine):
        return
    with SessionLocal() as db:
        firm_ids = list(db.execute(select(BrokerFirm.id)).scalars().all())
    for firm_id in firm_ids:
        # A fresh session per firm: set_search_path is connection state, and
        # reusing one session would leave the last firm's path set.
        with SessionLocal() as db:
            set_search_path(db, firm_id)
            created, updated = seed_insurers(db)
        print(f"  firm {firm_id}: {created} created, {updated} updated.")


def main() -> None:
    seed_all_schemas()


if __name__ == "__main__":
    main()
