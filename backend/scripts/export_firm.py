"""Export one broker firm's data: JSON lines per table plus a manifest.

    cd backend && PYTHONPATH=. uv run python -m scripts.export_firm <firm id> --out <dir>
        [--include-files] [--zip]

Writes ``<dir>/<firm slug>-<UTC timestamp>/``:

* ``firm/<table>.jsonl`` — every table of the firm's schema (``firm_<id>``);
* ``control/<table>.jsonl`` — the firm's rows in the shared ``public`` control
  tables: the firm, its companies, users, user-company access, invitations,
  member accounts, company sign-in policies, domains, identity providers,
  brand profiles, platform access grants, claim-review jobs, sign-in events
  and the platform audit trail;
* ``blobs.jsonl`` — every stored document key under the firm's storage prefix
  (and legacy ``nofirm/<company>/`` keys of its companies); with
  ``--include-files`` the bytes too, under ``files/``;
* ``manifest.json`` — firm, schema, Alembic revision, and per file the row
  count, byte size and SHA-256, plus the columns left out.

Credentials are never exported: password hashes, MFA secrets and recovery
codes, invitation/refresh/OTP hashes and encrypted keys are dropped column by
column (``SECRET_COLUMN``), and live sessions, MFA enrolments and OTP codes are
skipped entirely. The export is personal data — store it encrypted, restrict
access and delete it when the agreed retention period ends.

Read-only; runs as the runtime role (``inspro_app``) or the owner. PostgreSQL
only (SQLite has no firm schemas).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.storage import LEGACY_FIRMLESS_SEGMENT, StorageBackend, get_storage
from app.db.tenancy import is_postgres, schema_exists, schema_for_firm

EXPORT_FORMAT = 1
MANIFEST = "manifest.json"
SECRET_COLUMN = re.compile(
    r"^(password_hash|totp_secret_enc|recovery_codes|token|lease_token|refresh_hash|"
    r"code_hash|identifier_hash|encrypted_\w+|\w*secret\w*|\w+_enc)$"
)
# Control tables holding only credentials or live sessions: never exported.
NOT_EXPORTED = ("auth_sessions", "auth_mfa", "member_otp_codes")
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class FirmScope:
    firm_id: str
    slug: str
    name: str
    status: str
    is_platform_owner: bool
    client_ids: list[str]
    user_ids: list[str]
    member_ids: list[str]

    @property
    def schema(self) -> str:
        return schema_for_firm(self.firm_id)

    @property
    def params(self) -> dict[str, Any]:
        return {
            "firm": self.firm_id,
            "clients": self.client_ids,
            "users": self.user_ids,
            "members": self.member_ids,
        }


def load_scope(conn: Connection, firm_id: str) -> FirmScope | None:
    row = conn.execute(
        text(
            "SELECT id, slug, name, status, is_platform_owner FROM broker_firms WHERE id = :f"
        ),
        {"f": firm_id},
    ).first()
    if row is None:
        return None

    def ids(sql: str, **params: Any) -> list[str]:
        return [str(r[0]) for r in conn.execute(text(sql), params)]

    clients = ids("SELECT id FROM clients WHERE broker_firm_id = :f ORDER BY id", f=firm_id)
    return FirmScope(
        firm_id=str(row.id),
        slug=str(row.slug or row.id),
        name=str(row.name),
        status=str(row.status),
        is_platform_owner=bool(row.is_platform_owner),
        client_ids=clients,
        user_ids=ids("SELECT id FROM users WHERE broker_firm_id = :f ORDER BY id", f=firm_id),
        member_ids=ids(
            "SELECT id FROM member_accounts WHERE client_id = ANY(:c) ORDER BY id", c=clients
        ),
    )


# (table, WHERE clause over the scope parameters). Order is parent-first.
CONTROL_SELECTIONS: tuple[tuple[str, str], ...] = (
    ("broker_firms", "id = :firm"),
    ("clients", "broker_firm_id = :firm"),
    ("users", "broker_firm_id = :firm"),
    ("user_client_access", "user_id = ANY(:users) OR client_id = ANY(:clients)"),
    ("invitations", "broker_firm_id = :firm"),
    ("member_accounts", "client_id = ANY(:clients)"),
    ("client_auth_policy", "client_id = ANY(:clients)"),
    ("auth_credentials", "broker_firm_id = :firm OR user_id = ANY(:users)"),
    ("tenant_domains", "broker_firm_id = :firm"),
    ("identity_providers", "broker_firm_id = :firm"),
    ("brand_profiles", "broker_firm_id = :firm OR client_id = ANY(:clients)"),
    ("platform_access_grants", "broker_firm_id = :firm"),
    ("claim_review_jobs", "broker_firm_id = :firm"),
    ("auth_events", "broker_firm_id = :firm OR client_id = ANY(:clients)"),
    ("platform_audit_log", "broker_firm_id = :firm OR client_id = ANY(:clients)"),
)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(conn: Connection, sql: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    result = conn.execution_options(stream_results=True, yield_per=500).execute(
        text(sql), params
    )
    for row in result.mappings():
        yield dict(row)


def _write_table(
    conn: Connection, root: Path, rel: str, schema: str, table: str, where: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    columns = [c["name"] for c in sa_inspect(conn).get_columns(table, schema=schema)]
    kept = [c for c in columns if not SECRET_COLUMN.match(c)]
    select_list = ", ".join(f'"{c}"' for c in kept)
    sql = f'SELECT {select_list} FROM "{schema}"."{table}" WHERE {where}'
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as out:
        for row in _rows(conn, sql, params):
            out.write(json.dumps(row, default=_json_default, ensure_ascii=False, sort_keys=True))
            out.write("\n")
            count += 1
    return {
        "path": rel,
        "schema": schema,
        "table": table,
        "rows": count,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "excluded_columns": sorted(set(columns) - set(kept)),
    }


def firm_blob_keys(storage: StorageBackend, firm_id: str, client_ids: list[str]) -> list[str]:
    """Stored keys of the firm: its own prefix plus legacy keys of its companies."""
    keys = list(storage.list_keys(f"{firm_id}/"))
    for client_id in client_ids:
        keys += storage.list_keys(f"{LEGACY_FIRMLESS_SEGMENT}/{client_id}/")
    return sorted(set(keys))


def _export_blobs(
    storage: StorageBackend, scope: FirmScope, root: Path, include_files: bool
) -> dict[str, Any]:
    keys = firm_blob_keys(storage, scope.firm_id, scope.client_ids)
    listing = root / "blobs.jsonl"
    total = 0
    with listing.open("w", encoding="utf-8", newline="\n") as out:
        for key in keys:
            entry: dict[str, Any] = {"key": key}
            if include_files:
                data = storage.read(key)
                target = root / "files" / key
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                entry |= {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                total += len(data)
            out.write(json.dumps(entry, sort_keys=True) + "\n")
    return {
        "count": len(keys),
        "files_included": include_files,
        "file_bytes": total,
        "listing_sha256": sha256_file(listing),
    }


def export_firm(
    conn: Connection,
    firm_id: str,
    out_dir: Path,
    *,
    include_files: bool = False,
    storage: StorageBackend | None = None,
) -> Path:
    """Write the export and return its directory. Raises when the firm or its
    schema is missing."""
    if not is_postgres(conn):
        raise RuntimeError("Firm export needs PostgreSQL (firm schemas).")
    # First statement of the transaction: one read-only snapshot for every
    # table, so the files agree with each other and with the manifest.
    conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    scope = load_scope(conn, firm_id)
    if scope is None:
        raise LookupError(f"Broker firm {firm_id} not found")
    if not schema_exists(conn, scope.schema):
        raise LookupError(f"Firm schema {scope.schema} does not exist")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = out_dir / f"{scope.slug}-{stamp}"
    root.mkdir(parents=True, exist_ok=False)
    files = [
        _write_table(conn, root, f"control/{t}.jsonl", "public", t, where, scope.params)
        for t, where in CONTROL_SELECTIONS
    ]
    for table in sorted(sa_inspect(conn).get_table_names(schema=scope.schema)):
        rel = f"firm/{table}.jsonl"
        files.append(_write_table(conn, root, rel, scope.schema, table, "TRUE", {}))
    blobs = _export_blobs(storage or get_storage(), scope, root, include_files)
    revision = conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar()
    manifest = {
        "format": EXPORT_FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "firm": {"id": scope.firm_id, "slug": scope.slug, "name": scope.name,
                 "status": scope.status},
        "schema": scope.schema,
        "alembic_revision": revision,
        "client_ids": scope.client_ids,
        "not_exported": list(NOT_EXPORTED),
        "files": files,
        "blobs": blobs,
    }
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return root


class ExportInvalid(ValueError):
    """An export is missing, for another firm, or does not match its manifest."""


def read_verified_manifest(export: Path, firm_id: str | None = None) -> dict[str, Any]:
    """Load an export directory's manifest and check every listed file's hash.

    ``export`` may be the directory or a ``.zip`` made by ``--zip``.
    """
    if export.is_file() and export.suffix == ".zip":
        raise ExportInvalid("Unpack the zip and pass the export directory.")
    manifest_path = export / MANIFEST
    if not manifest_path.is_file():
        raise ExportInvalid(f"No {MANIFEST} in {export}")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != EXPORT_FORMAT:
        raise ExportInvalid("Unsupported export format")
    if firm_id is not None and manifest.get("firm", {}).get("id") != firm_id:
        raise ExportInvalid("The export belongs to another firm")
    for entry in manifest.get("files", []):
        path = export / entry["path"]
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            raise ExportInvalid(f"{entry['path']} is missing or does not match the manifest")
    listing = export / "blobs.jsonl"
    if not listing.is_file() or sha256_file(listing) != manifest["blobs"]["listing_sha256"]:
        raise ExportInvalid("blobs.jsonl is missing or does not match the manifest")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export one broker firm's data.")
    parser.add_argument("firm_id")
    parser.add_argument("--out", required=True, type=Path, help="parent directory")
    parser.add_argument("--include-files", action="store_true", help="copy stored documents")
    parser.add_argument("--zip", action="store_true", help="also write <export>.zip")
    args = parser.parse_args(argv)

    from app.db.session import engine

    if not is_postgres(engine):
        print("Database is not PostgreSQL; there are no firm schemas to export.")
        return 2
    try:
        with engine.connect() as conn, conn.begin():
            root = export_firm(conn, args.firm_id, args.out, include_files=args.include_files)
    except LookupError as exc:
        print(f"Export failed: {exc}")
        return 1
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    rows = sum(f["rows"] for f in manifest["files"])
    print(f"Exported {rows} row(s) in {len(manifest['files'])} file(s), "
          f"{manifest['blobs']['count']} document key(s) to {root}")
    if args.zip:
        archive = shutil.make_archive(str(root), "zip", root_dir=root)
        print(f"Archive: {archive} (sha256 {sha256_file(Path(archive))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
