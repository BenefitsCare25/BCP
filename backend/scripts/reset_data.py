"""Wipe operational data (uploaded slips, categories, employees, dependants,
audit log) while keeping the seed (broker firm, client, policy year,
attribute schema, product catalog).

Use during spike testing when you want to re-upload a placement slip without
manually deleting categories one-by-one in the UI. Refuses to run unless
INSPRO_ENV=dev against a SQLite database.
"""
from __future__ import annotations

from app.core.settings import assert_local_dev_database
from app.db.session import DATABASE_URL, SessionLocal
from app.models import (
    AuditLog,
    Category,
    Dependant,
    Employee,
    PlacementSlipRow,
)


def reset() -> None:
    assert_local_dev_database(DATABASE_URL, tool="scripts/reset_data.py")
    db = SessionLocal()
    try:
        deleted = {
            "categories": db.query(Category).delete(),
            "dependants": db.query(Dependant).delete(),
            "employees": db.query(Employee).delete(),
            "placement_slips": db.query(PlacementSlipRow).delete(),
            "audit_log": db.query(AuditLog).delete(),
        }
        db.commit()
        for table, n in deleted.items():
            print(f"  {table:18} {n:6} rows deleted")
        print(
            "Reset complete. Seed (broker firm, client, policy year, "
            "schema, products) preserved."
        )
    finally:
        db.close()


if __name__ == "__main__":
    reset()
