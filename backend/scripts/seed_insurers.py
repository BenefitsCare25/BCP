"""Seed the shared Singapore insurer library (client_id NULL rows).

Idempotent and re-runnable: an existing library row with the same name is
updated in place (legal name / aliases / notes refreshed), never duplicated,
and a row's ``name`` is never rewritten — that string is what products already
store, so changing it here would silently orphan them.

    cd backend && PYTHONPATH=. uv run python scripts/seed_insurers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Insurer
from app.services.insurer_catalog import SG_INSURERS


def seed_insurers(db) -> tuple[int, int]:
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


def main() -> None:
    with SessionLocal() as db:
        created, updated = seed_insurers(db)
    print(f"Insurer library: {created} created, {updated} updated.")


if __name__ == "__main__":
    main()
