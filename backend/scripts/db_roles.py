"""Bootstrap the least-privilege database roles (D13). Idempotent.

Prints the SQL by default; changes a database only with ``--apply`` and an
explicit URL taken from an environment variable (never argv, so a password
cannot leak into the process list or shell history)::

    cd backend
    # Review the SQL:
    uv run python -m scripts.db_roles --owner-login inspro-migrate --app-login inspro-portal
    # Apply as the server administrator, then verify:
    INSPRO_ROLES_DATABASE_URL=... uv run python -m scripts.db_roles --apply \
        --url-env INSPRO_ROLES_DATABASE_URL --owner-login inspro-migrate \
        --app-login inspro-portal --app-login inspro-portal-review-worker
    INSPRO_ROLES_DATABASE_URL=... uv run python -m scripts.db_roles --verify \
        --url-env INSPRO_ROLES_DATABASE_URL --app-login inspro-portal

What it does (see app/db/roles.py for the model):

1. Creates the NOLOGIN group roles ``inspro_owner`` and ``inspro_app``.
2. Database: owner gets CONNECT/CREATE/TEMPORARY, the app CONNECT only;
   ``public`` loses CREATE for PUBLIC and gives the owner USAGE + CREATE.
3. Transfers ownership of firm schemas and of every table, view, sequence,
   function and type in ``public`` and the firm schemas (extension objects
   excepted) to ``inspro_owner``.
4. Grants the runtime privileges on everything that exists now, and
   ``ALTER DEFAULT PRIVILEGES FOR ROLE inspro_owner`` for what it creates later.
5. Makes each ``--owner-login`` a member that acts as the owner
   (``SET role``), and each ``--app-login`` a member of the app role only.

Login roles themselves are NOT created here — a password login is created
with ``CREATE ROLE ... LOGIN`` + ``\\password`` in psql, an Entra identity with
``pgaadauth_create_principal`` (docs/DATABASE_ROLES_RUNBOOK.md).

The applying identity must own the existing objects or be a member of their
owner (the Azure server administrator does), and must be able to grant
membership in the roles it creates.
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url

from app.db.roles import (
    ALL_APP_SCHEMAS,
    APPEND_ONLY_TABLES,
    DEFAULT_OWNER_ROLE,
    app_role_name,
    quote_ident,
    quote_literal,
    runtime_grant_block,
    validate_login_name,
    validate_role_name,
)

_NOT_EXTENSION = (
    "NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = CAST('{catalog}' AS regclass) "
    "AND d.objid = {oid} AND d.deptype = 'e')"
)


def _create_role(role: str) -> str:
    return (
        "DO $role$ BEGIN\n"
        f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {quote_literal(role)}) THEN\n"
        f"    CREATE ROLE {quote_ident(role)} NOLOGIN;\n"
        "  END IF;\n"
        "END $role$"
    )


def _database_grants(owner: str, app: str) -> str:
    o, a = quote_ident(owner), quote_ident(app)
    return (
        "DO $db$ BEGIN\n"
        "  EXECUTE 'GRANT CONNECT, CREATE, TEMPORARY ON DATABASE ' "
        f"|| quote_ident(current_database()) || ' TO {o}';\n"
        "  EXECUTE 'GRANT CONNECT ON DATABASE ' "
        f"|| quote_ident(current_database()) || ' TO {a}';\n"
        "END $db$"
    )


def _ownership_block(owner: str) -> str:
    """Hand every non-extension object in public and the firm schemas to the
    owner role. Sequences owned by a column move with their table."""
    o, lit = quote_ident(owner), quote_literal(owner)
    rel_ext = _NOT_EXTENSION.format(catalog="pg_class", oid="c.oid")
    proc_ext = _NOT_EXTENSION.format(catalog="pg_proc", oid="p.oid")
    type_ext = _NOT_EXTENSION.format(catalog="pg_type", oid="t.oid")
    return f"""DO $own$
DECLARE r record; owner_oid oid := (SELECT oid FROM pg_roles WHERE rolname = {lit});
BEGIN
  FOR r IN SELECT nspname FROM pg_namespace
           WHERE left(nspname, 5) = 'firm_' AND nspowner <> owner_oid LOOP
    EXECUTE 'ALTER SCHEMA ' || quote_ident(r.nspname) || ' OWNER TO {o}';
  END LOOP;
  FOR r IN SELECT n.nspname, c.relname, c.relkind FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE {ALL_APP_SCHEMAS.replace("nspname", "n.nspname")}
             AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND c.relowner <> owner_oid
             AND {rel_ext}
             AND NOT (c.relkind = 'S' AND EXISTS (
               SELECT 1 FROM pg_depend d WHERE d.classid = CAST('pg_class' AS regclass)
               AND d.objid = c.oid AND d.deptype IN ('a', 'i'))) LOOP
    EXECUTE 'ALTER ' || CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
      WHEN 'S' THEN 'SEQUENCE' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END
      || ' ' || quote_ident(r.nspname) || '.' || quote_ident(r.relname) || ' OWNER TO {o}';
  END LOOP;
  FOR r IN SELECT p.oid, p.prokind FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE {ALL_APP_SCHEMAS.replace("nspname", "n.nspname")}
             AND p.proowner <> owner_oid AND {proc_ext} LOOP
    EXECUTE 'ALTER ' || CASE r.prokind WHEN 'p' THEN 'PROCEDURE' WHEN 'a' THEN 'AGGREGATE'
      ELSE 'FUNCTION' END || ' ' || CAST(CAST(r.oid AS regprocedure) AS text) || ' OWNER TO {o}';
  END LOOP;
  FOR r IN SELECT t.oid, t.typtype FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
           WHERE {ALL_APP_SCHEMAS.replace("nspname", "n.nspname")}
             AND t.typtype IN ('e', 'd', 'r') AND t.typowner <> owner_oid AND {type_ext} LOOP
    EXECUTE 'ALTER ' || CASE r.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END || ' '
      || CAST(CAST(r.oid AS regtype) AS text) || ' OWNER TO {o}';
  END LOOP;
END $own$"""


def _login_statements(
    owner: str, app: str, owner_logins: list[str], app_logins: list[str]
) -> list[str]:
    out: list[str] = []
    pairs = [(name, owner, app) for name in owner_logins] + [
        (name, app, owner) for name in app_logins
    ]
    for login, role, other in pairs:
        lg = quote_ident(validate_login_name(login))
        out += [
            f"REVOKE {quote_ident(other)} FROM {lg}",
            f"GRANT {quote_ident(role)} TO {lg}",
            # Sessions act AS the group: whatever the owner login creates is
            # owned by the group, and the app login can use no privilege
            # beyond the app role's, whatever the login itself holds.
            "DO $login$ BEGIN EXECUTE 'ALTER ROLE ' || "
            f"{quote_literal(lg)} || ' IN DATABASE ' || quote_ident(current_database()) "
            f"|| ' SET role = ' || quote_literal({quote_literal(role)}); END $login$",
        ]
    return out


def bootstrap_statements(
    owner: str, app: str, *, owner_logins: list[str], app_logins: list[str]
) -> list[str]:
    """Every statement, in order. Each is idempotent."""
    validate_role_name(owner)
    validate_role_name(app)
    if owner == app:
        raise ValueError("The owner and app roles must differ.")
    overlap = set(owner_logins) & set(app_logins)
    if overlap:
        raise ValueError(f"A login cannot be both owner and app: {sorted(overlap)}")
    o, a = quote_ident(owner), quote_ident(app)
    default_privileges = [
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} GRANT USAGE ON SCHEMAS TO {a}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {a}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} GRANT USAGE, SELECT ON SEQUENCES TO {a}",
    ]
    return [
        _create_role(owner),
        _create_role(app),
        f"REVOKE {o} FROM {a}",
        # Lets the applying identity transfer ownership and set default
        # privileges for the owner role. Harmless when already a member.
        f"GRANT {o} TO CURRENT_USER",
        _database_grants(owner, app),
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {o}",
        _ownership_block(owner),
        runtime_grant_block(app),
        *default_privileges,
        *_login_statements(owner, app, owner_logins, app_logins),
    ]


def verify(conn: Connection, owner: str, app: str, app_logins: list[str]) -> list[str]:
    """Problems with the role split as it stands, or an empty list."""
    problems: list[str] = []
    q = conn.execute
    for role in (owner, app):
        row = q(text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
        if row is None:
            problems.append(f"role {role} does not exist")
        elif row[0]:
            problems.append(f"role {role} can log in; it must be a NOLOGIN group")
    if problems:
        return problems
    for subject in (app, *app_logins):
        attrs = q(
            text(
                "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
                "FROM pg_roles WHERE rolname = :r"
            ),
            {"r": subject},
        ).first()
        if attrs is None:
            problems.append(f"login {subject} does not exist")
            continue
        if any(attrs):
            problems.append(f"{subject} has SUPERUSER/CREATEROLE/CREATEDB/BYPASSRLS")
        checks = {
            "is a member of the owner role": "pg_has_role(:s, :o, 'MEMBER')",
            "can CREATE in the database": (
                "has_database_privilege(:s, current_database(), 'CREATE')"
            ),
            "can CREATE in public": "has_schema_privilege(:s, 'public', 'CREATE')",
        }
        for label, expr in checks.items():
            if q(text(f"SELECT {expr}"), {"s": subject, "o": owner}).scalar():
                problems.append(f"{subject} {label}")
    problems += _verify_objects(conn, owner, app)
    return problems


def _verify_objects(conn: Connection, owner: str, app: str) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT n.nspname, c.relname, pg_get_userbyid(c.relowner), "
            "has_table_privilege(:a, c.oid, 'SELECT'), "
            "has_table_privilege(:a, c.oid, 'INSERT'), "
            "has_table_privilege(:a, c.oid, 'UPDATE'), "
            "has_table_privilege(:a, c.oid, 'DELETE'), "
            "has_table_privilege(:a, c.oid, 'TRUNCATE'), "
            "has_schema_privilege(:a, n.oid, 'USAGE'), "
            "has_schema_privilege(:a, n.oid, 'CREATE') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE {ALL_APP_SCHEMAS.replace('nspname', 'n.nspname')} "
            "AND c.relkind IN ('r', 'p') ORDER BY 1, 2"
        ),
        {"a": app},
    ).all()
    problems: list[str] = []
    for schema, table, table_owner, sel, ins, upd, dele, trunc, usage, create in rows:
        name = f"{schema}.{table}"
        if table_owner != owner:
            problems.append(f"{name} is owned by {table_owner}, not {owner}")
        if not usage or create:
            problems.append(f"{app} schema privileges on {schema} are wrong")
        if trunc:
            problems.append(f"{app} can TRUNCATE {name}")
        if table == "alembic_version":
            if not sel or ins or upd or dele:
                problems.append(f"{app} must have read-only access to {name}")
        elif table in APPEND_ONLY_TABLES:
            if not (sel and ins) or upd or dele:
                problems.append(f"{app} must have SELECT/INSERT only on {name}")
        elif not (sel and ins and upd and dele):
            problems.append(f"{app} lacks SELECT/INSERT/UPDATE/DELETE on {name}")
    return sorted(set(problems))


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--owner-role", default=DEFAULT_OWNER_ROLE)
    p.add_argument("--app-role", default=None, help="default: INSPRO_DB_APP_ROLE or inspro_app")
    p.add_argument("--owner-login", action="append", default=[], help="migration job login")
    p.add_argument("--app-login", action="append", default=[], help="runtime login (repeat)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="execute the SQL, then verify")
    mode.add_argument("--verify", action="store_true", help="only check the current state")
    p.add_argument("--url-env", help="environment variable holding the database URL")
    return p.parse_args(argv)


def _engine_url(url_env: str | None) -> str:
    if not url_env:
        raise SystemExit("--apply/--verify need --url-env NAME (the URL is read from $NAME).")
    url = os.environ.get(url_env, "").strip()
    if not url:
        raise SystemExit(f"${url_env} is empty.")
    if make_url(url).get_backend_name() != "postgresql":
        raise SystemExit("Database roles apply to PostgreSQL only.")
    return url


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    owner = validate_role_name(args.owner_role)
    app = validate_role_name(args.app_role or app_role_name())
    statements = bootstrap_statements(
        owner, app, owner_logins=args.owner_login, app_logins=args.app_login
    )
    if not (args.apply or args.verify):
        print("\n\n".join(s.rstrip(";") + ";" for s in statements))
        return 0
    engine = create_engine(_engine_url(args.url_env), pool_size=1, max_overflow=0)
    try:
        if args.apply:
            with engine.begin() as conn:
                for statement in statements:
                    conn.execute(text(statement))
            print(f"Applied {len(statements)} statement(s).")
        with engine.connect() as conn:
            problems = verify(conn, owner, app, args.app_login)
    finally:
        engine.dispose()
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    print("Role split verified." if not problems else f"{len(problems)} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
