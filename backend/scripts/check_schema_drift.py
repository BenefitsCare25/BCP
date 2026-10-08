"""Fail when a live schema no longer matches the models (D12). Read-only.

Compares the control tables in ``public`` and the tenant tables of every
``firm_<id>`` schema with the SQLAlchemy model metadata: tables, columns,
nullability, primary/unique keys, indexes and foreign keys (details and
severity rules in app/db/schema_drift.py). Runs in the release migration job
after ``alembic upgrade head`` and ``scripts.provision_tenants``; a non-zero
exit stops the deployment before the new image ships.

    cd backend && PYTHONPATH=. uv run python -m scripts.check_schema_drift
    ... --schema firm_<id>     # one schema (repeatable)
    ... --strict               # warnings fail too

Exit codes: 0 no errors, 1 drift errors (or warnings with --strict),
2 not PostgreSQL. On SQLite there are no schemas to compare.
"""
from __future__ import annotations

import argparse
import sys

from app.db.schema_drift import check_drift
from app.db.session import engine
from app.db.tenancy import is_postgres


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare live schemas with the models.")
    parser.add_argument("--schema", action="append", help="limit to this schema (repeatable)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)
    if not is_postgres(engine):
        print("Database is not PostgreSQL; the drift check needs firm schemas.")
        return 2
    with engine.connect() as conn:
        report = check_drift(conn, schemas=args.schema)
    print(report.render(), flush=True)
    if report.failed(strict=args.strict):
        print("Schema drift detected: fix the schema (or the model) before releasing.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
