"""Shared file-upload utility — enforces size cap + allowlist uniformly."""
from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Set
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # brief §11.4

# Multipart framing (boundaries, part headers, the small form fields sent with
# a file) allowed on top of the upload itself.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

# Suffix allowlist shared across all workbook-upload endpoints.
WORKBOOK_SUFFIXES: frozenset[str] = frozenset({".xls", ".xlsx", ".xlsm"})

# Flex-document intake accepts heterogeneous benefit documents (PDF/image/email).
FLEX_SUFFIXES: frozenset[str] = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".msg"}
)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class RequestSizeLimitMiddleware:
    """Refuse a request body over the upload cap before anything parses it.

    FastAPI reads a multipart body — spooling every file to disk — before any
    dependency or endpoint runs, so the per-file cap in `saved_upload` only
    applies once the whole body is already stored. No endpoint accepts more
    than one upload's worth of data per request, so a body larger than the cap
    plus `MULTIPART_OVERHEAD_BYTES` is refused here: on its declared
    ``Content-Length`` before reading, and by counting the bytes received for a
    chunked body, which declares no length.
    """

    def __init__(self, app: ASGIApp, max_upload_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_upload_bytes + MULTIPART_OVERHEAD_BYTES
        self.detail = (
            f"Uploads are limited to {max_upload_bytes // (1024 * 1024)} MB per request."
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            response = JSONResponse(
                {"detail": self.detail},
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                headers={"Connection": "close"},
            )
            await response(scope, receive, send)
            return

        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    # Raised from inside the body read, so the app's exception
                    # handling answers with an ordinary 413.
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE, self.detail
                    )
            return message

        await self.app(scope, bounded_receive, send)


@asynccontextmanager
async def saved_upload(
    file: UploadFile,
    allowed_suffixes: Set[str],
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> AsyncIterator[Path]:
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
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"File exceeds {max_bytes // (1024 * 1024)} MB",
                )
            tmp.write(chunk)
        tmp.close()
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)
