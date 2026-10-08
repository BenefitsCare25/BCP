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
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import MetaData, Table, UniqueConstraint, event, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateColumn
from sqlalchemy.sql.elements import conv

from app.db.base import Base
from app.db.roles import grant_firm_schema

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
        "ai_policy_versions",
        "platform_ai_usage",
        # Durable claim-review work is polled before a tenant is selected.
        "claim_review_jobs",
        # White-label routing and platform administration: a request's host is
        # resolved to a firm before its schema is known, and platform actions
        # span firms.
        "tenant_domains",
        "platform_access_grants",
        "platform_audit_log",
        "identity_providers",
        # Brand is served before sign-in, from the host's firm.
        "brand_profiles",
        # Cached FX reference rates. A rate is a fact about the market on a
        # date, owned by no firm — per-schema copies would let two firms convert
        # the same receipt to two different figures, and would multiply the
        # outbound calls by the tenant count. See `models/fx_rate.py`.
        "fx_rates",
    }
)


def is_postgres(bind: Engine | Connection | Session) -> bool:
    engine = bind.get_bind() if isinstance(bind, Session) else bind
    return engine.dialect.name == "postgresql"


def schema_for_firm(firm_id: str) -> str:
    """Deterministic, injection-safe schema name for a firm."""
    safe = "".join(c for c in firm_id if c.isalnum())
    return f"firm_{safe}"


def _register_models() -> None:
    """Make sure every model is on `Base.metadata` before reading it.

    A script that imports only this module would otherwise see an empty or
    partial metadata and "provision" a schema with no tables — failing later on
    the audit trigger with a misleading missing-relation error.
    """
    import app.models  # noqa: F401


def tenant_tables() -> list[Table]:
    """Operational tables that live in a per-firm schema (not control)."""
    _register_models()
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
    table: Table,
    to_schema: str | None,
    constraint: Any,
    referred_schema: str | None,
) -> str | None:
    """FK target schema when copying a tenant table into a firm schema:
    control tables (clients, …) stay in public; tenant tables point at the
    firm schema."""
    referred_name = constraint.referred_table.name
    if referred_name in CONTROL_TABLES:
        return None  # public / default
    return to_schema


def _ensure_audit_append_only(conn: Connection, schema: str) -> None:
    """Install the audit immutability guard for a newly provisioned tenant."""
    conn.execute(
        text(
            "CREATE OR REPLACE FUNCTION public.inspro_prevent_audit_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'audit_log is append-only'; END; $$"
        )
    )
    conn.execute(
        text(f'DROP TRIGGER IF EXISTS audit_log_append_only ON "{schema}".audit_log')
    )
    conn.execute(
        text(
            f'CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE '
            f'ON "{schema}".audit_log FOR EACH ROW '
            "EXECUTE FUNCTION public.inspro_prevent_audit_mutation()"
        )
    )


POLICY_YEAR_EXCLUSION = "ex_policy_year_client_dates"


def policy_year_overlap_exists(conn: Connection, schema: str) -> bool:
    """Whether a schema already holds two overlapping years for one company."""
    return conn.execute(
        text(
            f'SELECT 1 FROM "{schema}".policy_years a '
            f'JOIN "{schema}".policy_years b ON a.client_id = b.client_id '
            "AND a.id < b.id AND a.start_date <= b.end_date "
            "AND b.start_date <= a.end_date LIMIT 1"
        )
    ).first() is not None


def ensure_policy_year_exclusion(conn: Connection, schema: str) -> bool:
    """Enforce non-overlapping benefit years per company in one schema.

    The original constraint (migration e1b2c3d4f5a6) was added to `public` only
    and is not expressible in the models, so firm schemas — where the real rows
    live — relied on an application check alone. Idempotent. Returns False and
    logs, rather than raising, when existing rows already overlap: provisioning
    runs for every firm on each deploy and one firm's data must not block the
    rest. The Alembic migration that introduces this raises instead.
    """
    present = conn.execute(
        text(
            "SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = :schema AND t.relname = 'policy_years' AND c.conname = :name"
        ),
        {"schema": schema, "name": POLICY_YEAR_EXCLUSION},
    ).first()
    if present is not None:
        return True
    if policy_year_overlap_exists(conn, schema):
        logger.error(
            "%s.policy_years has overlapping benefit years; %s not added — fix the data",
            schema, POLICY_YEAR_EXCLUSION,
        )
        return False
    btree_gist = text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
    if conn.execute(btree_gist).first() is None:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    conn.execute(
        text(
            f'ALTER TABLE "{schema}".policy_years ADD CONSTRAINT "{POLICY_YEAR_EXCLUSION}" '
            "EXCLUDE USING gist (client_id WITH =, "
            "daterange(start_date, end_date, '[]') WITH &&)"
        )
    )
    return True


def _firm_metadata(schema: str) -> MetaData:
    _register_models()
    staging = MetaData()
    for tbl in Base.metadata.sorted_tables:
        if tbl.name in CONTROL_TABLES:
            tbl.to_metadata(staging)
    for tbl in tenant_tables():
        copied = tbl.to_metadata(staging, schema=schema, referred_schema_fn=_referred_schema)
        # PostgreSQL indexes are already scoped to their table's schema. The
        # generated 37-character firm prefix forces names past PG's 63-byte
        # limit, where SQLAlchemy's four-hex suffix can collide. Keep the
        # original short naming convention within each separate schema.
        prefix = f"ix_{schema}_"
        for index in copied.indexes:
            if index.name and index.name.startswith(prefix):
                index.name = conv("ix_" + index.name[len(prefix):])
    return staging


def provision_firm_schema(bind: Engine | Connection, firm_id: str) -> str | None:
    """Create a firm's schema and its operational tables. Idempotent.

    No-op on SQLite. Returns the schema name on Postgres, else None.
    """
    if not is_postgres(bind):
        return None
    schema = schema_for_firm(firm_id)

    def _run(conn: Connection) -> None:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        # Control tables are copied in at their public schema so tenant FK
        # targets (e.g. clients) resolve; they already exist, so checkfirst
        # skips re-creating them.
        staging = _firm_metadata(schema)
        # Only the firm's own tables. The control tables are in `staging` so
        # tenant foreign keys resolve, but creating them is Alembic's job: a
        # provisioning run against an older database must never create
        # `public` tables behind the migrations' back.
        staging.create_all(
            conn,
            tables=[tbl for tbl in staging.sorted_tables if tbl.schema == schema],
            checkfirst=True,
        )
        _ensure_audit_append_only(conn, schema)
        ensure_policy_year_exclusion(conn, schema)
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
        # Least-privilege runtime role (D13): no-op until the role exists.
        grant_firm_schema(conn, schema)

    if isinstance(bind, Engine):
        with bind.begin() as conn:
            _run(conn)
    else:
        _run(bind)
    return schema


def schema_exists(conn: Connection, schema: str) -> bool:
    return conn.execute(text("SELECT to_regnamespace(:s)"), {"s": schema}).scalar() is not None


def provision_new_firm(conn: Connection, firm_id: str) -> bool:
    """Provision a firm created at runtime, when the runtime may (D13).

    With ``INSPRO_RUNTIME_PROVISIONING`` on (the default) the schema is created
    on the caller's connection, so it commits or rolls back with the firm row.
    Off — once the app connects as the least-privilege ``inspro_app`` role,
    which cannot run DDL — the firm is left pending: its schema is absent, its
    requests fail closed with 503 ``tenant_unavailable`` (``_require_schema``),
    and the next migration-job run (``scripts.provision_tenants``) creates it.
    Returns True when the schema exists on return. Always True on SQLite.
    """
    from app.core.settings import get_settings

    if not is_postgres(conn):
        return True
    if get_settings().runtime_provisioning:
        provision_firm_schema(conn, firm_id)
        return True
    logger.info("Firm %s left for the migration job to provision", firm_id)
    return False


def pending_firm_ids(conn: Connection) -> list[str]:
    """Firms whose schema has not been provisioned yet (Postgres only)."""
    if not is_postgres(conn):
        return []
    ids = [str(r[0]) for r in conn.execute(text("SELECT id FROM broker_firms ORDER BY id"))]
    return [fid for fid in ids if not schema_exists(conn, schema_for_firm(fid))]


def sync_firm_schema(bind: Engine | Connection, firm_id: str) -> str | None:
    """Bring a firm schema up to the current model: create missing tables,
    columns, indexes, and unique constraints (additive). Idempotent. No-op on
    SQLite.

    Limits: additive only — drops, renames, type changes, and data migrations
    need a bespoke per-schema step (see the deployment operations section in
    docs/PRODUCTION_RESILIENCE_RUNBOOK.md). A new NOT NULL column without a
    server default can't be back-filled automatically, so it is added as
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
                # Only a SERVER default back-fills existing rows. A Python-side
                # `default=` applies to ORM inserts alone, so treating it as a
                # default emitted `ADD COLUMN ... NOT NULL` with no DEFAULT,
                # which fails on any populated table.
                if not col.nullable and col.server_default is None:
                    # Add nullable and let the operator tighten it.
                    coltype = col.type.compile(dialect=conn.dialect)
                    conn.execute(
                        text(f'ALTER TABLE "{schema}".{tbl.name} ADD COLUMN "{col.name}" {coltype}')
                    )
                    logger.warning(
                        "sync_firm_schema: added %s.%s.%s as NULLABLE (model is NOT NULL "
                        "with no server default) — back-fill then ALTER ... SET NOT NULL "
                        "manually",
                        schema, tbl.name, col.name,
                    )
                else:
                    coldef = str(CreateColumn(col).compile(dialect=conn.dialect)).strip()
                    conn.execute(
                        text(f'ALTER TABLE "{schema}".{tbl.name} ADD COLUMN {coldef}')
                    )

            # Indexes + unique constraints aren't emitted by ADD COLUMN, so
            # reconcile them. Match by COLUMN SET, not name: provisioning
            # older provisioning used names with the firm-schema prefix, so
            # comparing names would duplicate existing indexes.
            existing_idx_signatures = {
                (
                    tuple(i["column_names"]),
                    bool(i.get("unique")),
                    str((i.get("dialect_options") or {}).get("postgresql_where") or ""),
                )
                for i in insp.get_indexes(tbl.name, schema=schema)
            }
            for idx in tbl.indexes:
                colset = tuple(c.name for c in idx.columns)
                predicate = idx.dialect_options["postgresql"].get("where")
                predicate_text = str(predicate) if predicate is not None else ""
                signature = (colset, bool(idx.unique), predicate_text)
                if signature in existing_idx_signatures:
                    continue
                cols = ", ".join(f'"{c}"' for c in colset)
                unique = "UNIQUE " if idx.unique else ""
                name = idx.name or f"ix_{tbl.name}_{'_'.join(colset)}"
                where = (
                    " WHERE " + str(predicate.compile(dialect=conn.dialect))
                    if predicate is not None
                    else ""
                )
                conn.execute(
                    text(f'CREATE {unique}INDEX IF NOT EXISTS "{name}" '
                         f'ON "{schema}".{tbl.name} ({cols}){where}')
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


# Where the session's chosen search_path is remembered so it can be re-applied
# to every LATER transaction on that session (see `_reapply_search_path`).
_SEARCH_PATH_KEY = "inspro_search_path"

# Firm schemas this process has seen exist. Firms are never renamed and schemas
# are dropped only by offboarding, so a hit needs no round trip; a miss is
# re-checked every time, so a newly provisioned firm routes at once.
_KNOWN_SCHEMAS: set[str] = set()


def _require_schema(session: Session, schema: str) -> None:
    """Fail closed when a firm's schema does not exist.

    Postgres silently skips a missing schema in ``search_path``, so routing to
    one sent every tenant read and write to ``public`` — the shared fallback
    that must never hold a firm's rows.
    """
    if schema in _KNOWN_SCHEMAS:
        return
    found = session.execute(text("SELECT to_regnamespace(:s)"), {"s": schema}).scalar()
    if found is None:
        logger.error("Firm schema %s does not exist; refusing to route to it", schema)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": "tenant_unavailable",
                "message": "This workspace is not available right now.",
            },
        )
    _KNOWN_SCHEMAS.add(schema)


def set_search_path(session: Session, firm_id: str | None) -> None:
    """Route a session's tenant-table reads/writes to the firm's schema.

    Always sets the path deterministically on Postgres — to the firm schema
    when bound, or back to ``public`` otherwise. A firm whose schema is missing
    raises 503 ``tenant_unavailable`` instead of falling through to ``public``.
    Control tables remain resolvable via the trailing ``public``. No-op on
    SQLite.

    ``SET LOCAL`` scopes the path to the current transaction, so it can never
    outlive the request on a pooled server connection (safe behind PgBouncer
    transaction pooling). The choice is STORED on the session and re-applied at
    the start of every later transaction — see ``_reapply_search_path``.
    """
    if not is_postgres(session):
        return
    if firm_id is None:
        statement = "SET LOCAL search_path TO public"
    else:
        schema = schema_for_firm(firm_id)
        _require_schema(session, schema)
        statement = f'SET LOCAL search_path TO "{schema}", public'
    session.info[_SEARCH_PATH_KEY] = statement
    session.execute(text(statement))


@event.listens_for(Session, "after_begin")
def _reapply_search_path(
    session: Session, _transaction: Any, connection: Connection
) -> None:
    """Re-establish the firm schema on every new transaction of a session.

    The routing ``SET LOCAL`` ends with its transaction, and a Session releases
    its connection back to the pool at ``commit()``. Without this, any read the
    handler performs AFTER committing (``db.refresh``, ``db.get``, a follow-up
    query — 50+ such call sites) ran against ``public``, where tenant tables are
    empty. On Postgres that surfaced as ``Could not refresh instance`` /
    silently-missing rows; SQLite has no schemas, so no test could catch it.

    Re-applying here makes the routing survive commit boundaries. Uses the raw
    connection rather than ``session.execute`` — we are already inside the
    session's transaction-begin hook and must not re-enter it.
    """
    statement = session.info.get(_SEARCH_PATH_KEY)
    if not statement or connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(statement)
