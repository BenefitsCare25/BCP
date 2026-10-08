"""Harden tenancy invariants found by the October 2026 audit.

Revision ID: b6d4f2a8c1e3
Revises: 5d8e2a7c4b16
Create Date: 2026-10-08

- ``audit_log.client_id`` loses its foreign key in ``public`` and every firm
  schema. ON DELETE SET NULL made Postgres UPDATE audit rows when a company was
  deleted, which the append-only trigger rejects, so any company with audit
  history could not be deleted.
- The non-overlapping benefit-year exclusion constraint, previously added to
  ``public`` only, is added to every firm schema (where the rows live).
- ``password_token_issued_at`` on ``member_accounts`` and ``auth_credentials``
  lets a reissued set-password link cancel every earlier one.
- A platform administrator (``system_admin``) never belongs to a broker firm.
  Existing rows are corrected; Postgres also enforces it with a CHECK.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import sqlite_fk_guard

revision: str = "b6d4f2a8c1e3"
down_revision: str | None = "5d8e2a7c4b16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT_FK = "fk_audit_log_client_id_clients"
_EXCLUSION = "ex_policy_year_client_dates"
_FIRMLESS_ADMIN = "ck_users_system_admin_firmless"


def _firm_schemas(bind: sa.engine.Connection) -> list[str]:
    if bind.dialect.name != "postgresql":
        return []
    return [
        "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    ]


def _has_table(bind: sa.engine.Connection, schema: str, table: str) -> bool:
    return bool(
        bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f'"{schema}".{table}'}
        )
    )


def _drop_audit_client_fk_postgres(bind: sa.engine.Connection, schema: str) -> None:
    names = bind.execute(
        sa.text(
            "SELECT c.conname FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = :schema AND t.relname = 'audit_log' AND c.contype = 'f' "
            "AND c.confrelid = 'public.clients'::regclass"
        ),
        {"schema": schema},
    ).scalars().all()
    for name in names:
        bind.execute(sa.text(f'ALTER TABLE "{schema}".audit_log DROP CONSTRAINT "{name}"'))


def _add_policy_year_exclusion(bind: sa.engine.Connection, schema: str) -> None:
    from app.db.tenancy import ensure_policy_year_exclusion, policy_year_overlap_exists

    if policy_year_overlap_exists(bind, schema):
        raise RuntimeError(
            f"{schema}.policy_years has overlapping benefit years for one company. "
            "Correct the dates, then rerun the migration."
        )
    ensure_policy_year_exclusion(bind, schema)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    for table in ("member_accounts", "auth_credentials"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("password_token_issued_at", sa.DateTime(timezone=True), nullable=True)
            )

    bind.execute(
        sa.text(
            "UPDATE users SET broker_firm_id = NULL "
            "WHERE role = 'system_admin' AND broker_firm_id IS NOT NULL"
        )
    )

    if dialect == "sqlite":
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("audit_log", recreate="always") as batch:
                batch.drop_constraint(_AUDIT_FK, type_="foreignkey")
        return

    if dialect != "postgresql":
        return

    for schema in ["public", *_firm_schemas(bind)]:
        if _has_table(bind, schema, "audit_log"):
            _drop_audit_client_fk_postgres(bind, schema)
    for schema in _firm_schemas(bind):
        if _has_table(bind, schema, "policy_years"):
            _add_policy_year_exclusion(bind, schema)

    bind.execute(
        sa.text(
            f'ALTER TABLE users ADD CONSTRAINT "{_FIRMLESS_ADMIN}" '
            "CHECK (role <> 'system_admin' OR broker_firm_id IS NULL) NOT VALID"
        )
    )
    bind.execute(sa.text(f'ALTER TABLE users VALIDATE CONSTRAINT "{_FIRMLESS_ADMIN}"'))


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        bind.execute(sa.text(f'ALTER TABLE users DROP CONSTRAINT IF EXISTS "{_FIRMLESS_ADMIN}"'))
        for schema in _firm_schemas(bind):
            if _has_table(bind, schema, "policy_years"):
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".policy_years '
                        f'DROP CONSTRAINT IF EXISTS "{_EXCLUSION}"'
                    )
                )
        for schema in ["public", *_firm_schemas(bind)]:
            if _has_table(bind, schema, "audit_log"):
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".audit_log ADD CONSTRAINT "{_AUDIT_FK}" '
                        "FOREIGN KEY (client_id) REFERENCES public.clients (id) "
                        "ON DELETE SET NULL NOT VALID"
                    )
                )
    elif dialect == "sqlite":
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("audit_log", recreate="always") as batch:
                batch.create_foreign_key(
                    _AUDIT_FK, "clients", ["client_id"], ["id"], ondelete="SET NULL"
                )

    for table in ("auth_credentials", "member_accounts"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("password_token_issued_at")
