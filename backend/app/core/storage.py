"""Retained document storage — claim receipts + dependant proof documents.

Unlike `app/core/uploads.py::saved_upload` (which parses and DISCARDS the
bytes), this layer keeps them: `LocalStorage` under `backend/var/uploads/` in
dev (gitignored — the files are PII), `AzureBlobStorage` in prod
(`INSPRO_STORAGE_MODE=azure`, managed identity or connection string).

Every save streams a SHA-256 while writing — the hash is the duplicate-receipt
/ tampering signal the claims pipeline keys on.

Blob paths are namespaced `{firm}/{client}/{entity_type}/{entity_id}/{doc_id}{suffix}`
so a misrouted read can never cross a tenant boundary silently.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

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


def document_path(
    broker_firm_id: str | None,
    client_id: str,
    entity_type: str,
    entity_id: str,
    doc_id: str,
    suffix: str,
) -> str:
    firm = broker_firm_id or "nofirm"
    return f"{firm}/{client_id}/{entity_type}/{entity_id}/{doc_id}{suffix}"


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
        return self._container.download_blob(path).readall()

    def delete(self, path: str) -> None:
        try:
            self._container.delete_blob(path)
        except Exception as exc:
            try:
                from azure.core.exceptions import ResourceNotFoundError
            except ImportError:  # pragma: no cover - Azure mode includes it
                ResourceNotFoundError = ()  # type: ignore[assignment,misc]
            if isinstance(exc, ResourceNotFoundError):
                return
            raise


def get_storage() -> StorageBackend:
    if get_settings().storage_mode == "azure":
        return AzureBlobStorage()
    return LocalStorage()
