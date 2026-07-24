"""Schema-per-broker-firm physical isolation (Postgres).

Each broker firm's operational data lives in its own Postgres schema
(``firm_<id>``). The shared **control** tables — the registry + identity needed
to authenticate and route a request before a firm is known — stay in ``public``:

    control (public): broker_firms, clients, users, user_client_access,
                      invitations, member_accounts, member_otp_codes
    per firm schema : policy_years, categories, employees, dependants, plans,
                      products, *_attribute_schemas, client_ai_configs,
                      ai_spend_log, audit_log, placement slips

Cross-schema foreign keys from tenant tables to ``public.clients`` are emitted
explicitly so each firm schema references the shared client registry.

On SQLite (dev/test) there are no schemas: everything lives in one database and
all functions here are no-ops, so the single-schema code path is unchanged.
"""
from __future__ import annotations

import logging

from sqlalchemy import MetaData, Table, UniqueConstraint, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateColumn

from app.db.base import Base

logger = logging.getLogger(__name__)

# Tables that must be reachable before a firm/schema is resolved, plus the
# shared client registry that tenant tables foreign-key into.
CONTROL_TABLES: frozenset[str] = frozenset(
    {
        "broker_firms",
        "clients",
        "users",
        "user_client_access",
        "invitations",
        # Portal member identity: must authenticate before a firm is known.
        "member_accounts",
        "member_otp_codes",
        # Local-credential auth (HR) + surface-agnostic MFA/session/event/policy:
        # authentication resolves before a firm schema is known.
        "auth_credentials",
        "auth_mfa",
        "auth_sessions",
        "auth_events",
        "client_auth_policy",
        # Platform-wide AI limits + shared-quota usage counter: global, spanning
        # all firms/clients, so they must live in public (not per-firm schemas).
        "platform_ai_settings",
        "platform_ai_usage",
    }
)


def is_postgres(bind: Engine | Connection | Session) -> bool:
    engine = bind.get_bind() if isinstance(bind, Session) else bind
    return engine.dialect.name == "postgresql"


def schema_for_firm(firm_id: str) -> str:
    """Deterministic, injection-safe schema name for a firm."""
    safe = "".join(c for c in firm_id if c.isalnum())
    return f"firm_{safe}"


def tenant_tables() -> list[Table]:
    """Operational tables that live in a per-firm schema (not control)."""
    return [
        t
        for t in Base.metadata.sorted_tables
        if t.name not in CONTROL_TABLES and t.name != "alembic_version"
    ]


def shared_columns(conn: Connection, firm_schema: str, table_name: str) -> str:
    """Quoted column list (model order) for columns present in BOTH `public`
    and the firm schema's copy of the table. Used for cross-schema
    INSERT...SELECT, where column order differs and either side may be missing
    a column mid-migration (e.g. syncing an old firm schema)."""
    insp = sa_inspect(conn)
    firm = {c["name"] for c in insp.get_columns(table_name, schema=firm_schema)}
    pub = {c["name"] for c in insp.get_columns(table_name, schema="public")}
    ordered = Base.metadata.tables[table_name].columns.keys()
    return ", ".join(f'"{c}"' for c in ordered if c in firm and c in pub)


def _referred_schema(
    table: Table, to_schema: str | None, constraint, referred_schema: str | None
) -> str | None:
    """FK target schema when copying a tenant table into a firm schema:
    control tables (clients, …) stay in public; tenant tables point at the
    firm schema."""
    referred_name = constraint.referred_table.name
    if referred_name in CONTROL_TABLES:
        return None  # public / default
    return to_schema


def provision_firm_schema(bind: Engine | Connection, firm_id: str) -> str | None:
    """Create a firm's schema and its operational tables. Idempotent.

    No-op on SQLite. Returns the schema name on Postgres, else None.
    """
    if not is_postgres(bind):
        return None
    schema = schema_for_firm(firm_id)

    def _run(conn: Connection) -> None:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        staging = MetaData()
        # Control tables are copied in at their public schema so tenant FK
        # targets (e.g. clients) resolve; they already exist, so checkfirst
        # skips re-creating them.
        for tbl in Base.metadata.sorted_tables:
            if tbl.name in CONTROL_TABLES:
                tbl.to_metadata(staging, schema=None)
        for tbl in tenant_tables():
            tbl.to_metadata(staging, schema=schema, referred_schema_fn=_referred_schema)
        staging.create_all(conn, checkfirst=True)
        # Each firm schema needs its own copy of the global (client_id NULL)
        # product + attribute catalog, sourced from the canonical public copy.
        # Order matters: products before plan_attribute_schemas (FK).
        global_copies = [
            ("products", "client_id IS NULL"),
            ("employee_attribute_schemas", "client_id IS NULL"),
            (
                "plan_attribute_schemas",
                "product_id IN (SELECT id FROM public.products WHERE client_id IS NULL)",
            ),
        ]
        for tname, cond in global_copies:
            cols = shared_columns(conn, schema, tname)
            conn.execute(
                text(
                    f'INSERT INTO "{schema}".{tname} ({cols}) '
                    f"SELECT {cols} FROM public.{tname} WHERE {cond} "
                    f"ON CONFLICT (id) DO NOTHING"
                )
            )

    if isinstance(bind, Engine):
        with bind.begin() as conn:
            _run(conn)
    else:
        _run(bind)
    return schema


def sync_firm_schema(bind: Engine | Connection, firm_id: str) -> str | None:
    """Bring a firm schema up to the current model: create missing tables,
    columns, indexes, and unique constraints (additive). Idempotent. No-op on
    SQLite.

    Limits: additive only — drops, renames, type changes, and data migrations
    need a bespoke per-schema step (see DEPLOY_RUNBOOK). A new NOT NULL column
    with no default can't be back-filled automatically, so it is added as
    NULLABLE with a warning; the operator must back-fill and SET NOT NULL.
    """
    if not is_postgres(bind):
        return None
    schema = provision_firm_schema(bind, firm_id)

    def _run(conn: Connection) -> None:
        insp = sa_inspect(conn)
        for tbl in tenant_tables():
            existing_cols = {c["name"] for c in insp.get_columns(tbl.name, schema=schema)}
            for col in tbl.columns:
                if col.name in existing_cols:
                    continue
                no_default = col.server_default is None and col.default is None
                if not col.nullable and no_default:
                    # Can't add NOT NULL with no default to a (possibly) populated
                    # table — add nullable and let the operator tighten it.
                    coltype = col.type.compile(dialect=conn.dialect)
                    conn.execute(
                        text(f'ALTER TABLE "{schema}".{tbl.name} ADD COLUMN "{col.name}" {coltype}')
                    )
                    logger.warning(
                        "sync_firm_schema: added %s.%s.%s as NULLABLE (model is NOT NULL "
                        "with no default) — back-fill then ALTER ... SET NOT NULL manually",
                        schema, tbl.name, col.name,
                    )
                else:
                    coldef = str(CreateColumn(col).compile(dialect=conn.dialect)).strip()
                    conn.execute(
                        text(f'ALTER TABLE "{schema}".{tbl.name} ADD COLUMN {coldef}')
                    )

            # Indexes + unique constraints aren't emitted by ADD COLUMN, so
            # reconcile them. Match by COLUMN SET, not name: provisioning
            # creates these via to_metadata(schema=...), which rewrites
            # auto-generated names with the firm-schema prefix, so comparing
            # names would create duplicates and never converge.
            existing_idx_cols = {
                tuple(i["column_names"])
                for i in insp.get_indexes(tbl.name, schema=schema)
            }
            for idx in tbl.indexes:
                colset = tuple(c.name for c in idx.columns)
                if colset in existing_idx_cols:
                    continue
                cols = ", ".join(f'"{c}"' for c in colset)
                unique = "UNIQUE " if idx.unique else ""
                name = idx.name or f"ix_{tbl.name}_{'_'.join(colset)}"
                conn.execute(
                    text(f'CREATE {unique}INDEX IF NOT EXISTS "{name}" '
                         f'ON "{schema}".{tbl.name} ({cols})')
                )
            existing_uc_cols = {
                tuple(u["column_names"])
                for u in insp.get_unique_constraints(tbl.name, schema=schema)
            }
            for con in tbl.constraints:
                if not isinstance(con, UniqueConstraint) or not con.name:
                    continue
                colset = tuple(c.name for c in con.columns)
                if colset in existing_uc_cols:
                    continue
                cols = ", ".join(f'"{c}"' for c in colset)
                conn.execute(
                    text(f'ALTER TABLE "{schema}".{tbl.name} '
                         f'ADD CONSTRAINT "{con.name}" UNIQUE ({cols})')
                )

    if isinstance(bind, Engine):
        with bind.begin() as conn:
            _run(conn)
    else:
        _run(bind)
    return schema


def set_search_path(session: Session, firm_id: str | None) -> None:
    """Route a session's tenant-table reads/writes to the firm's schema.

    Always sets the path deterministically on Postgres — to the firm schema
    when bound, or back to ``public`` otherwise — so a pooled connection can
    never inherit a previous request's tenant schema. Control tables remain
    resolvable via the trailing ``public``. No-op on SQLite.
    """
    if not is_postgres(session):
        return
    if firm_id is None:
        session.execute(text("SET search_path TO public"))
    else:
        schema = schema_for_firm(firm_id)
        session.execute(text(f'SET search_path TO "{schema}", public'))
