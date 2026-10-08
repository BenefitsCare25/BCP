"""Least-privilege database roles (D13).

Two group roles (NOLOGIN) carry every privilege; login identities are members:

* ``inspro_owner`` owns the schemas, tables, sequences and functions. Only the
  migration job (Alembic + tenant provisioning) acts as it — its login gets
  ``ALTER ROLE <login> IN DATABASE <db> SET role = inspro_owner`` so whatever
  it creates is owned by the group, not by the individual login.
* ``inspro_app`` is the runtime: CONNECT, USAGE on ``public`` and every firm
  schema, SELECT/INSERT/UPDATE/DELETE on tables and USAGE/SELECT on
  sequences. No CREATE, no TRUNCATE, no ownership — so the web app and the
  review worker cannot run DDL, drop a table or remove an audit trigger.
  Append-only audit tables also lose UPDATE/DELETE, on top of their triggers,
  and Alembic's version table is read-only.

``ALTER DEFAULT PRIVILEGES FOR ROLE inspro_owner`` (scripts/db_roles.py)
covers schemas and tables the owner creates later; ``grant_firm_schema``
re-applies the grants whenever a firm schema is provisioned or synced, which
also covers schemas an older identity created before the split.

The production names are fixed; ``INSPRO_DB_APP_ROLE`` exists so tests on a
shared disposable server can use a unique name (roles are cluster-wide).

The SQL here is PL/pgSQL ``DO`` blocks with no ``:name`` binds or ``%`` so the same
text runs through SQLAlchemy ``text()`` and prints verbatim for ``psql``.
"""
from __future__ import annotations

import logging
import os
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

DEFAULT_OWNER_ROLE = "inspro_owner"
DEFAULT_APP_ROLE = "inspro_app"

# Tables the runtime may read and append to but never change or remove rows of.
APPEND_ONLY_TABLES: tuple[str, ...] = ("audit_log", "platform_audit_log")
# Alembic's bookkeeping: readable, never written by the runtime.
READ_ONLY_TABLES: tuple[str, ...] = ("alembic_version",)

# SQL that selects the schemas the runtime works in, for use in a WHERE clause
# on pg_namespace. `left()` rather than LIKE keeps `%` out of the text.
ALL_APP_SCHEMAS = "(nspname = 'public' OR left(nspname, 5) = 'firm_')"

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# Login roles may be Entra principals named after a managed identity or group.
_LOGIN_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@ -]{0,62}$")
_SCHEMA_NAME = re.compile(r"^[A-Za-z0-9_]{1,63}$")


def validate_role_name(name: str) -> str:
    if not _ROLE_NAME.fullmatch(name):
        raise ValueError(f"Invalid database role name: {name!r}")
    return name


def validate_login_name(name: str) -> str:
    if not _LOGIN_NAME.fullmatch(name):
        raise ValueError(f"Invalid database login name: {name!r}")
    return name


def quote_ident(name: str) -> str:
    """Quote an identifier that has passed one of the validators above."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def app_role_name() -> str:
    """The runtime group role that provisioning grants to."""
    return validate_role_name(
        os.environ.get("INSPRO_DB_APP_ROLE", "").strip() or DEFAULT_APP_ROLE
    )


def role_exists(conn: Connection, role: str) -> bool:
    found = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
    return found.first() is not None


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(quote_literal(v) for v in values) + "]"


def runtime_grant_block(app_role: str, schema: str | None = None) -> str:
    """A ``DO`` block granting the runtime role its privileges on the existing
    objects of one schema, or of ``public`` and every firm schema. Idempotent."""
    app = quote_ident(validate_role_name(app_role))
    if schema is None:
        where = ALL_APP_SCHEMAS
    elif _SCHEMA_NAME.fullmatch(schema):
        where = f"nspname = {quote_literal(schema)}"
    else:
        raise ValueError(f"Invalid schema name: {schema!r}")
    return f"""DO $grant$
DECLARE sch text; tbl text; q text;
BEGIN
  FOR sch IN SELECT nspname FROM pg_namespace WHERE {where} ORDER BY nspname LOOP
    q := quote_ident(sch);
    EXECUTE 'GRANT USAGE ON SCHEMA ' || q || ' TO {app}';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ' || q || ' TO {app}';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ' || q || ' TO {app}';
    EXECUTE 'REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA ' || q
      || ' FROM {app}';
    FOREACH tbl IN ARRAY {_array(APPEND_ONLY_TABLES)} LOOP
      IF to_regclass(q || '.' || quote_ident(tbl)) IS NOT NULL THEN
        EXECUTE 'REVOKE UPDATE, DELETE ON ' || q || '.' || quote_ident(tbl) || ' FROM {app}';
      END IF;
    END LOOP;
    FOREACH tbl IN ARRAY {_array(READ_ONLY_TABLES)} LOOP
      IF to_regclass(q || '.' || quote_ident(tbl)) IS NOT NULL THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON ' || q || '.' || quote_ident(tbl)
          || ' FROM {app}';
      END IF;
    END LOOP;
  END LOOP;
END $grant$"""


def grant_firm_schema(conn: Connection, schema: str, app_role: str | None = None) -> bool:
    """Give the runtime role its grants on a (newly) provisioned firm schema.

    Called by ``tenancy.provision_firm_schema`` on the provisioning
    connection, so the grant commits with the schema. Returns False and
    changes nothing when the runtime role does not exist (SQLite, development,
    or a server where ``scripts/db_roles.py`` has not been applied — before
    the split the app connects as the owner and needs no grants).
    """
    if conn.dialect.name != "postgresql":
        return False
    role = validate_role_name(app_role or app_role_name())
    if not role_exists(conn, role):
        logger.debug("Role %s absent; skipping runtime grants on %s", role, schema)
        return False
    conn.execute(text(runtime_grant_block(role, schema)))
    return True
