"""Offboard a broker firm: freeze it, then remove its data. Dry run by default.

    cd backend
    # 1. Plan (changes nothing):
    PYTHONPATH=. uv run python -m scripts.offboard_firm <firm id>
    # 2. Freeze: suspend the firm, end its sessions, disable its domains.
    #    Without --export this stops after freezing and asks for one:
    PYTHONPATH=. uv run python -m scripts.offboard_firm <firm id> \
        --confirm <firm slug> --operator <name/email>
    # 3. Export the frozen firm (scripts.export_firm), then remove:
    PYTHONPATH=. uv run python -m scripts.offboard_firm <firm id> \
        --confirm <firm slug> --operator <name/email> --export <export dir>

Removal, after the export's manifest is verified (every file hash, and the
firm-schema row counts still equal to the live ones):

1. ``DROP SCHEMA firm_<id> CASCADE``;
2. the firm's control rows, children first: sessions, MFA enrolments, sign-in
   events, review jobs, OTP codes, member accounts, company sign-in policies,
   user-company access, brand profiles, credentials, invitations, domains,
   identity providers, access grants, users, companies, the firm;
3. (after the database commit) every stored document under the firm's prefix
   and the legacy ``nofirm/<company>/`` prefixes of its companies.

Steps 1-2 are one transaction: any remaining reference rolls all of it back.
``platform_audit_log`` is append-only and is kept: it records the offboarding.
If document deletion is interrupted, rerun the same command — once the firm
row is gone it resumes from the export manifest and deletes only documents.

Refuses the platform owner's firm. Needs the schema owner's rights (DROP
SCHEMA): run it as ``inspro_owner``, not as the runtime role.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core.storage import StorageBackend, get_storage
from app.db.tenancy import is_postgres, schema_exists, schema_for_firm
from app.models.auth import SUBJECT_MEMBER, SUBJECT_USER
from scripts.export_firm import (
    ExportInvalid,
    FirmScope,
    firm_blob_keys,
    load_scope,
    read_verified_manifest,
)

BROKER_SUBJECT = "broker"  # app.core.broker_auth.SUBJECT (avoids importing auth config)

# (table, WHERE over the scope parameters), deleted in this order.
DELETE_ORDER: tuple[tuple[str, str], ...] = (
    ("auth_sessions",
     "broker_firm_id = :firm OR client_id = ANY(:clients) "
     "OR (subject_type IN (:broker, :user) AND subject_id = ANY(:users)) "
     "OR (subject_type = :member AND subject_id = ANY(:members))"),
    ("auth_mfa",
     "(subject_type IN (:broker, :user) AND subject_id = ANY(:users)) "
     "OR (subject_type = :member AND subject_id = ANY(:members))"),
    ("auth_events", "broker_firm_id = :firm OR client_id = ANY(:clients)"),
    ("claim_review_jobs", "broker_firm_id = :firm OR client_id = ANY(:clients)"),
    ("member_otp_codes", "member_account_id = ANY(:members)"),
    ("member_accounts", "client_id = ANY(:clients)"),
    ("client_auth_policy", "client_id = ANY(:clients)"),
    ("user_client_access", "user_id = ANY(:users) OR client_id = ANY(:clients)"),
    ("brand_profiles", "broker_firm_id = :firm OR client_id = ANY(:clients)"),
    ("auth_credentials", "broker_firm_id = :firm OR user_id = ANY(:users)"),
    ("invitations", "broker_firm_id = :firm"),
    ("tenant_domains", "broker_firm_id = :firm"),
    ("identity_providers", "broker_firm_id = :firm"),
    ("platform_access_grants", "broker_firm_id = :firm OR user_id = ANY(:users)"),
    ("users", "broker_firm_id = :firm"),
    ("clients", "broker_firm_id = :firm"),
    ("broker_firms", "id = :firm"),
)

_LIVE_SESSIONS = f"revoked_at IS NULL AND ({DELETE_ORDER[0][1]})"


class OffboardRefused(RuntimeError):
    """The firm cannot be offboarded as asked; nothing further was changed."""


def _params(scope: FirmScope) -> dict[str, Any]:
    return scope.params | {
        "broker": BROKER_SUBJECT, "user": SUBJECT_USER, "member": SUBJECT_MEMBER,
    }


def _count(conn: Connection, table: str, where: str, params: dict[str, Any]) -> int:
    return int(conn.execute(text(f'SELECT count(*) FROM public."{table}" WHERE {where}'),
                            params).scalar() or 0)


def _audit(conn: Connection, scope_id: str, action: str, detail: dict[str, Any]) -> None:
    conn.execute(
        text(
            "INSERT INTO public.platform_audit_log (id, occurred_at, actor_user_id, action, "
            "entity_type, entity_id, broker_firm_id, client_id, detail, ip_address, "
            "user_agent, request_id) VALUES (:id, :at, NULL, :action, 'broker_firm', :firm, "
            ":firm, NULL, CAST(:detail AS json), NULL, 'scripts.offboard_firm', :rid)"
        ),
        {
            "id": str(uuid4()), "at": datetime.now(UTC), "action": action, "firm": scope_id,
            "detail": json.dumps(detail, sort_keys=True, default=str), "rid": str(uuid4()),
        },
    )


def _firm_table_counts(conn: Connection, schema: str) -> dict[str, int]:
    return {
        t: int(conn.execute(text(f'SELECT count(*) FROM "{schema}"."{t}"')).scalar() or 0)
        for t in sorted(sa_inspect(conn).get_table_names(schema=schema))
    }


def _public_tenant_leftovers(conn: Connection, scope: FirmScope) -> dict[str, int]:
    """Rows of this firm's companies in ``public`` copies of tenant tables.
    They should not exist; removing the companies would fail on them."""
    from app.db.tenancy import tenant_tables

    insp = sa_inspect(conn)
    present = set(insp.get_table_names(schema="public"))
    found: dict[str, int] = {}
    for table in tenant_tables():
        if table.name in present and "client_id" in table.c:
            n = _count(conn, table.name, "client_id = ANY(:clients)", scope.params)
            if n:
                found[table.name] = n
    return found


def plan(conn: Connection, scope: FirmScope, storage: StorageBackend) -> dict[str, Any]:
    """What a confirmed run would change, without changing it."""
    params = _params(scope)
    exists = schema_exists(conn, scope.schema)
    return {
        "firm": {"id": scope.firm_id, "slug": scope.slug, "name": scope.name,
                 "status": scope.status},
        "schema": scope.schema,
        "schema_exists": exists,
        "schema_rows": _firm_table_counts(conn, scope.schema) if exists else {},
        "live_sessions": _count(conn, "auth_sessions", _LIVE_SESSIONS, params),
        "domains_to_disable": _count(conn, "tenant_domains",
                                     "broker_firm_id = :firm AND status <> 'disabled'", params),
        "control_rows": {t: _count(conn, t, where, params) for t, where in DELETE_ORDER},
        "public_tenant_leftovers": _public_tenant_leftovers(conn, scope),
        "documents": len(firm_blob_keys(storage, scope.firm_id, scope.client_ids)),
    }


def freeze(conn: Connection, scope: FirmScope, operator: str) -> dict[str, int]:
    """Suspend the firm, end every live session of it, disable its domains."""
    params = _params(scope)
    now = datetime.now(UTC)
    suspended = conn.execute(
        text("UPDATE broker_firms SET status = 'suspended', updated_at = :now "
             "WHERE id = :firm AND status <> 'suspended'"),
        params | {"now": now},
    ).rowcount
    revoked = conn.execute(
        text(f"UPDATE auth_sessions SET revoked_at = :now WHERE {_LIVE_SESSIONS}"),
        params | {"now": now},
    ).rowcount
    disabled = conn.execute(
        text("UPDATE tenant_domains SET status = 'disabled', updated_at = :now "
             "WHERE broker_firm_id = :firm AND status <> 'disabled'"),
        params | {"now": now},
    ).rowcount
    result = {"suspended": int(suspended or 0), "sessions_revoked": int(revoked or 0),
              "domains_disabled": int(disabled or 0)}
    if any(result.values()):  # a rerun on an already frozen firm records nothing
        detail = result | {"operator": operator, "host": socket.gethostname()}
        _audit(conn, scope.firm_id, "firm.offboard.freeze", detail)
    return result


def verify_export(conn: Connection, scope: FirmScope, export: Path) -> dict[str, Any]:
    """The export must be for this firm, intact, and as current as the data."""
    manifest = read_verified_manifest(export, scope.firm_id)
    exported = {f["table"]: f["rows"] for f in manifest["files"] if f["schema"] == scope.schema}
    live = _firm_table_counts(conn, scope.schema) if schema_exists(conn, scope.schema) else {}
    stale = sorted(t for t in set(exported) | set(live) if exported.get(t) != live.get(t))
    if stale:
        raise ExportInvalid(
            "The export no longer matches the firm's data (re-export the frozen firm): "
            + ", ".join(stale[:10])
        )
    return manifest


def remove_data(
    conn: Connection, scope: FirmScope, operator: str, manifest_path: Path
) -> dict[str, int]:
    """Drop the schema and delete the control rows. Caller owns the transaction."""
    leftovers = _public_tenant_leftovers(conn, scope)
    if leftovers:
        raise OffboardRefused(f"Rows of this firm's companies are in public tables: {leftovers}")
    params = _params(scope)
    conn.execute(text(f'DROP SCHEMA IF EXISTS "{scope.schema}" CASCADE'))
    deleted: dict[str, int] = {}
    for table, where in DELETE_ORDER:
        deleted[table] = int(
            conn.execute(text(f'DELETE FROM public."{table}" WHERE {where}'), params).rowcount or 0
        )
    _audit(conn, scope.firm_id, "firm.offboard.remove_data", {
        "operator": operator, "schema_dropped": scope.schema, "rows_deleted": deleted,
        "export_manifest": str(manifest_path), "host": socket.gethostname(),
    })
    return deleted


def delete_documents(storage: StorageBackend, firm_id: str, client_ids: list[str]) -> int:
    keys = firm_blob_keys(storage, firm_id, client_ids)
    for key in keys:
        storage.delete(key)
    return len(keys)


def _resume_documents(
    engine: Engine, firm_id: str, confirm: str, export: Path, storage: StorageBackend
) -> int:
    """The firm row is gone: finish an interrupted run's document deletion."""
    manifest = read_verified_manifest(export, firm_id)
    if manifest["firm"]["slug"] != confirm:
        raise OffboardRefused("--confirm does not match the exported firm's slug")
    count = delete_documents(storage, firm_id, list(manifest["client_ids"]))
    with engine.begin() as conn:
        _audit(conn, firm_id, "firm.offboard.complete", {"documents_deleted": count,
                                                          "resumed": True})
    print(f"Firm already removed; deleted {count} remaining document(s).")
    return 0


def _dry_run(engine: Engine, scope: FirmScope, export: Path | None,
             storage: StorageBackend) -> int:
    with engine.connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        report = plan(conn, scope, storage)
        if export is not None:
            try:
                verify_export(conn, scope, export)
                report["export"] = "verified"
            except ExportInvalid as exc:
                report["export"] = f"invalid: {exc}"
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Dry run: nothing changed. To proceed: --confirm {scope.slug} --operator <you>")
    return 0


def run(
    engine: Engine,
    firm_id: str,
    *,
    confirm: str | None = None,
    operator: str | None = None,
    export: Path | None = None,
    storage: StorageBackend | None = None,
) -> int:
    """Exit code: 0 done (or dry run), 1 not found, 2 refused, 3 frozen and
    waiting for an export."""
    if not is_postgres(engine):
        raise OffboardRefused("Offboarding needs PostgreSQL (firm schemas).")
    store = storage or get_storage()
    with engine.connect() as conn:
        scope = load_scope(conn, firm_id)
    if scope is None:
        if confirm and export is not None:
            return _resume_documents(engine, firm_id, confirm, export, store)
        print(f"Broker firm {firm_id} not found.")
        return 1
    if scope.is_platform_owner:
        raise OffboardRefused("The platform owner's firm cannot be offboarded.")
    if confirm is None:
        return _dry_run(engine, scope, export, store)
    if confirm != scope.slug:
        raise OffboardRefused(f"--confirm must be the firm's slug ({scope.slug!r}).")
    if not operator or not operator.strip():
        raise OffboardRefused("--operator is required with --confirm.")
    with engine.begin() as conn:
        frozen = freeze(conn, scope, operator)
    print(f"Frozen {scope.slug}: {frozen}")
    if export is None:
        print("Now export the frozen firm and rerun with --export <dir>:\n"
              f"  python -m scripts.export_firm {scope.firm_id} --out <dir> --include-files")
        return 3
    with engine.begin() as conn:
        conn.execute(text("SELECT 1 FROM broker_firms WHERE id = :f FOR UPDATE"), {"f": firm_id})
        verify_export(conn, scope, export)
        deleted = remove_data(conn, scope, operator, export)
    print(f"Removed schema {schema_for_firm(firm_id)} and control rows: {deleted}")
    documents = delete_documents(store, scope.firm_id, scope.client_ids)
    with engine.begin() as conn:
        _audit(conn, scope.firm_id, "firm.offboard.complete",
               {"operator": operator, "documents_deleted": documents})
    print(f"Deleted {documents} document(s). Offboarding of {scope.slug} complete; "
          "restart the app so no process keeps the dropped schema cached.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offboard a broker firm (dry run by default).")
    parser.add_argument("firm_id")
    parser.add_argument("--confirm", metavar="FIRM_SLUG", help="the firm's slug, to act")
    parser.add_argument("--operator", help="who is offboarding (recorded in the audit log)")
    parser.add_argument("--export", type=Path, help="the verified export directory")
    args = parser.parse_args(argv)

    from app.db.session import engine

    try:
        return run(engine, args.firm_id, confirm=args.confirm, operator=args.operator,
                   export=args.export)
    except (OffboardRefused, ExportInvalid) as exc:
        print(f"Refused: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
