"""Shared file-upload utility — enforces size cap + allowlist uniformly."""
from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # brief §11.4

# Suffix allowlist shared across all workbook-upload endpoints.
WORKBOOK_SUFFIXES: frozenset[str] = frozenset({".xls", ".xlsx", ".xlsm"})

# Flex-document intake accepts heterogeneous benefit documents (PDF/image/email).
FLEX_SUFFIXES: frozenset[str] = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".msg"}
)


@asynccontextmanager
async def saved_upload(
    file: UploadFile,
    allowed_suffixes: set[str],
    max_bytes: int = DEFAULT_MAX_BYTES,
):
    """Persist an UploadFile to a temp path, yield the Path, then clean up.

    Enforces both the extension allowlist and the max-size cap. Streams in
    chunks so a too-large upload is aborted before the disk fills.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_suffixes:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type: {suffix or '(none)'}. Allowed: {sorted(allowed_suffixes)}",
        )

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)
    try:
        size = 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"File exceeds {max_bytes // (1024 * 1024)} MB",
                )
            tmp.write(chunk)
        tmp.close()
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)
