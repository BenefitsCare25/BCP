"""Fail-closed safety checks for claim documents before retained storage or AI."""
from __future__ import annotations

import logging
import os
import subprocess
import warnings
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

MAX_IMAGE_PIXELS = 40_000_000
MAX_PDF_PAGES = 50
_PDF_ACTIVE_KEYS = {
    "/AA",
    "/EmbeddedFiles",
    "/JavaScript",
    "/JS",
    "/Launch",
    "/OpenAction",
    "/RichMedia",
}


def _unsafe(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "unsafe_document", "message": message},
    )


def _inspect_image(path: Path) -> None:
    from PIL import Image

    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise _unsafe("The image dimensions are too large to process safely.")
                image.verify()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _unsafe("The image dimensions are too large to process safely.") from exc
    except Exception as exc:
        raise _unsafe("The uploaded image is damaged or cannot be read safely.") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def _walk_pdf(value: Any, *, seen: set[int], remaining: list[int]) -> None:
    if remaining[0] <= 0:
        raise _unsafe("The PDF is too complex to process safely.")
    remaining[0] -= 1
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)

    getter = getattr(value, "get_object", None)
    if callable(getter):
        try:
            resolved = getter()
        except Exception as exc:
            raise _unsafe("The PDF contains an unreadable object.") from exc
        if resolved is not value:
            _walk_pdf(resolved, seen=seen, remaining=remaining)
            return

    if isinstance(value, dict):
        if _PDF_ACTIVE_KEYS.intersection(str(key) for key in value):
            raise _unsafe("PDFs containing scripts, launches, or embedded files are not accepted.")
        for item in value.values():
            _walk_pdf(item, seen=seen, remaining=remaining)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_pdf(item, seen=seen, remaining=remaining)


def _inspect_pdf(path: Path) -> None:
    from pypdf import PdfReader

    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise _unsafe("Password-protected PDFs cannot be scanned and are not accepted.")
        page_count = len(reader.pages)
        if page_count < 1 or page_count > MAX_PDF_PAGES:
            raise _unsafe(f"PDFs must contain between 1 and {MAX_PDF_PAGES} pages.")
        root = reader.trailer.get("/Root")
        if root is not None:
            _walk_pdf(root, seen=set(), remaining=[10_000])
    except HTTPException:
        raise
    except Exception as exc:
        raise _unsafe("The uploaded PDF is damaged or cannot be read safely.") from exc


def _malware_scan(path: Path) -> None:
    settings = get_settings()
    executable = settings.document_scan_command
    if not executable:
        if settings.require_document_scan:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "document_scan_unavailable",
                    "message": "Document scanning is temporarily unavailable. Try again later.",
                },
            )
        return
    try:
        result = subprocess.run(
            [executable, "--no-summary", "--infected", os.fspath(path)],
            capture_output=True,
            check=False,
            timeout=settings.document_scan_timeout_seconds,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("Document malware scanner unavailable", exc_info=True)
        if settings.require_document_scan:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "document_scan_unavailable",
                    "message": "Document scanning is temporarily unavailable. Try again later.",
                },
            ) from exc
        return
    if result.returncode == 1:
        logger.warning("Rejected malware-positive claim upload")
        raise _unsafe("The document did not pass the security scan.")
    if result.returncode != 0:
        logger.error(
            "Document scanner failed with code %s: %s",
            result.returncode,
            result.stderr[-500:],
        )
        if settings.require_document_scan:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "document_scan_unavailable",
                    "message": "Document scanning is temporarily unavailable. Try again later.",
                },
            )


def scan_quarantined_document(path: Path) -> None:
    """Validate a temporary upload, then malware-scan it before promotion."""
    settings = get_settings()
    # The existing upload gate already verifies the file signature and size in
    # every environment. Full parser + malware validation is the production
    # quarantine boundary and is intentionally tied to the fail-closed switch:
    # local fixtures and developer tools often use minimal signature-only files
    # that are not complete documents, while production never may.
    if settings.require_document_scan:
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            _inspect_pdf(path)
        elif suffix in {".png", ".jpg", ".jpeg"}:
            _inspect_image(path)
        else:
            raise _unsafe("This document type is not accepted.")
    _malware_scan(path)
