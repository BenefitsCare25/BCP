"""Retained document storage — claim receipts + dependant proof documents.

Unlike `app/core/uploads.py::saved_upload` (which parses and DISCARDS the
bytes), this layer keeps them: `LocalStorage` under `backend/var/uploads/` in
dev (gitignored — the files are PII), `AzureBlobStorage` in prod
(`INSPRO_STORAGE_MODE=azure`, managed identity or connection string).

Every save streams a SHA-256 while writing — the hash is the duplicate-receipt
/ tampering signal the claims pipeline keys on.

Blob paths are namespaced `{firm}/{client}/{entity_type}/{entity_id}/{doc_id}{suffix}`
so a misrouted read can never cross a tenant boundary silently. The firm segment
is the firm that owns the RESOURCE (the company's broker firm, see
`company_firm_id`), never the firm of whoever performed the write, so a firm-less
system admin files blobs with the rest of the company's data. Read and delete
paths check a stored key against the resource with `assert_key_in_scope`.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Protocol

from app.core.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Firm segment that firm-less admins wrote before keys followed the resource.
# Those blobs are not moved: a key under it still resolves, for its own company.
LEGACY_FIRMLESS_SEGMENT = "nofirm"

# Claim receipts + dependant proofs: documents the AI pipeline can read.
DOCUMENT_SUFFIXES: frozenset[str] = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024

# Retained report versions (Reports Center) — generated spreadsheets/docs, not
# PII uploads, so they get their own allowlist + a larger ceiling (a full-roster
# insurer listing can exceed the 15MB document cap).
REPORT_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".docx", ".zip"})
MAX_REPORT_BYTES = 50 * 1024 * 1024

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class SavedBlob:
    path: str
    sha256: str
    size_bytes: int


class StorageBackend(Protocol):
    def save(self, stream: BinaryIO, path: str) -> SavedBlob: ...

    def read(self, path: str) -> bytes: ...

    def delete(self, path: str) -> None: ...

    def list_keys(self, prefix: str) -> list[str]: ...


class StorageScopeError(ValueError):
    """A stored key does not belong to the resource it is being used for."""


def _key_segment(value: str, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"Invalid storage key {label} segment: {value!r}")
    return value


def company_firm_id(db: Session, client_id: str) -> str:
    """The broker firm that owns ``client_id``: the firm every blob of the
    company is filed under, whoever performs the write."""
    from app.models import Client  # lazy: keeps storage importable without models

    client = db.get(Client, client_id)
    if client is None or not client.broker_firm_id:
        raise ValueError(f"Company {client_id!r} is not registered to a broker firm.")
    return client.broker_firm_id


def document_path(
    broker_firm_id: str,
    client_id: str,
    entity_type: str,
    entity_id: str,
    doc_id: str,
    suffix: str,
) -> str:
    """Storage key for one blob of a company's resource.

    ``broker_firm_id`` must be the firm that owns the company. A blob filed
    outside every firm can be neither attributed nor removed with that firm's
    data, so a missing firm raises ValueError rather than defaulting.
    """
    if not broker_firm_id or broker_firm_id == LEGACY_FIRMLESS_SEGMENT:
        raise ValueError("A storage key needs the broker firm that owns the resource.")
    firm = _key_segment(broker_firm_id, "firm")
    client = _key_segment(client_id, "company")
    kind = _key_segment(entity_type, "entity type")
    entity = _key_segment(entity_id, "entity")
    name = _key_segment(f"{doc_id}{suffix}", "file")
    return f"{firm}/{client}/{kind}/{entity}/{name}"


def assert_key_in_scope(key: str, broker_firm_id: str, client_id: str) -> None:
    """Refuse a stored key that is not filed under this firm and company.

    Read and delete paths pass the RESOURCE's own firm and company, so a row
    whose path was copied between tenants, tampered with or mis-written cannot
    reach another tenant's bytes. Keys under the legacy ``nofirm/`` segment
    still resolve, but only for the company named in the key.
    """
    if not broker_firm_id or not client_id:
        raise ValueError("Checking a storage key needs the resource's firm and company.")
    parts = key.split("/")
    if (
        len(parts) < 3
        or "\\" in key
        or any(part in {"", ".", ".."} for part in parts)
        or parts[0] not in {broker_firm_id, LEGACY_FIRMLESS_SEGMENT}
        or parts[1] != client_id
    ):
        raise StorageScopeError("Stored document is outside its firm and company scope.")


# Second path segment for firm-library assets (panel-card artwork shared by
# every company in a firm), in place of a company id.
LIBRARY_SEGMENT = "library"


class LocalStorage:
    """Filesystem backend (dev / single-node). Root defaults to backend/var/uploads."""

    def __init__(self, root: Path | None = None) -> None:
        configured = get_settings().storage_dir
        self.root = (
            root
            if root is not None
            else Path(configured)
            if configured
            else Path(__file__).resolve().parents[2] / "var" / "uploads"
        )

    def _full(self, path: str) -> Path:
        # Resolve and confine to the root so a crafted stored path ("../…")
        # can never escape the upload directory.
        full = (self.root / path).resolve()
        root = self.root.resolve()
        if not full.is_relative_to(root):
            raise ValueError(f"Storage path escapes root: {path!r}")
        return full

    def save(self, stream: BinaryIO, path: str) -> SavedBlob:
        full = self._full(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with full.open("wb") as out:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
        return SavedBlob(path=path, sha256=digest.hexdigest(), size_bytes=size)

    def read(self, path: str) -> bytes:
        return self._full(path).read_bytes()

    def delete(self, path: str) -> None:
        self._full(path).unlink(missing_ok=True)

    def list_keys(self, prefix: str) -> list[str]:
        """Every stored key under ``prefix`` (a ``/``-terminated folder), sorted.
        Used by the per-firm export and offboarding scripts."""
        folder = self._full(prefix)
        if not folder.is_dir():
            return []
        root = self.root.resolve()
        return sorted(
            f.relative_to(root).as_posix() for f in folder.rglob("*") if f.is_file()
        )


class AzureBlobStorage:
    """Azure Blob backend (prod). Auth: connection string when configured,
    else managed identity against `INSPRO_STORAGE_ACCOUNT_URL`."""

    def __init__(self) -> None:
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover - dep present in prod image
            raise RuntimeError(
                "INSPRO_STORAGE_MODE=azure requires the 'azure-storage-blob' "
                "package (uv add azure-storage-blob azure-identity)."
            ) from exc

        settings = get_settings()
        if settings.storage_connection_string:
            service = BlobServiceClient.from_connection_string(
                settings.storage_connection_string
            )
        elif settings.storage_account_url:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(
                settings.storage_account_url, credential=DefaultAzureCredential()
            )
        else:
            raise RuntimeError(
                "INSPRO_STORAGE_MODE=azure requires INSPRO_STORAGE_ACCOUNT_URL "
                "(managed identity) or INSPRO_STORAGE_CONNECTION_STRING."
            )
        self._container = service.get_container_client(settings.storage_container)

    def save(self, stream: BinaryIO, path: str) -> SavedBlob:
        digest = hashlib.sha256()
        size = 0
        chunks: list[bytes] = []
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            chunks.append(chunk)
        self._container.upload_blob(name=path, data=b"".join(chunks), overwrite=True)
        return SavedBlob(path=path, sha256=digest.hexdigest(), size_bytes=size)

    def read(self, path: str) -> bytes:
        try:
            return self._container.download_blob(path).readall()
        except Exception as exc:
            # Callers map FileNotFoundError to 404 (LocalStorage's contract).
            # Azure raises its own type, which used to surface as a 500/503.
            if _is_blob_missing(exc):
                raise FileNotFoundError(path) from exc
            raise

    def delete(self, path: str) -> None:
        try:
            self._container.delete_blob(path)
        except Exception as exc:
            if _is_blob_missing(exc):
                return
            raise

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(self._container.list_blob_names(name_starts_with=prefix))


def _is_blob_missing(exc: Exception) -> bool:
    try:
        from azure.core.exceptions import ResourceNotFoundError
    except ImportError:  # pragma: no cover - Azure mode includes it
        return False
    return isinstance(exc, ResourceNotFoundError)


def get_storage() -> StorageBackend:
    if get_settings().storage_mode == "azure":
        return AzureBlobStorage()
    return LocalStorage()
