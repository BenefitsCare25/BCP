"""Multi-format Flex-document intake.

Normalizes a heterogeneous benefit document (PDF text, image-table PDF, PNG/JPG,
Outlook ``.msg`` with embedded image tables) into ``(extracted_text, images)`` so
the AI extractor can read it. Images are downscaled and re-encoded to PNG/base64
to bound vision token cost; tiny spacer/logo images are dropped.

The heavy parsers (pypdf, PyMuPDF, Pillow, extract-msg) are imported lazily so a
missing optional dependency surfaces as a clear runtime error on the one endpoint
that needs it, not at app import time. Every parser failure is wrapped as
``FlexIntakeError`` so the endpoint returns a clean 422, never a bare 500.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services.doc_images import DocImageError, normalize_image_bytes

logger = logging.getLogger(__name__)

# Bound vision cost: cap image count (edge/thumbnail bounds live in doc_images).
_MAX_IMAGES = 8
# A PDF page whose own text layer is this sparse is almost certainly an image /
# slide — rasterize just that page so the model can read its tables, while
# keeping the text of the prose pages. This is per-page, not whole-document.
_PDF_PAGE_SPARSE_TEXT_CHARS = 200
_PDF_RASTER_ZOOM = 2.0  # ~144 DPI — legible tables without ballooning bytes
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp")


class FlexIntakeError(RuntimeError):
    """Raised when a Flex document cannot be read into text/images."""


def _normalize_image_bytes(raw: bytes) -> dict[str, Any] | None:
    """Shared normalizer (see ``doc_images``), with the dependency-guard error
    re-raised as ``FlexIntakeError`` so the endpoint still returns a clean 422."""
    try:
        return normalize_image_bytes(raw)
    except DocImageError as exc:  # pragma: no cover - dependency guard
        raise FlexIntakeError(str(exc)) from exc


def _extract_pdf(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Extract PDF text, rasterizing only the pages whose own text is sparse.

    PyMuPDF gives per-page text and rasterization in one pass, so an image-table
    page embedded in an otherwise text-heavy document is still rasterized (its
    tables aren't silently dropped), while text pages avoid a wasteful raster.
    """
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz  # type: ignore[no-redef]
    except ImportError:
        fitz = None  # type: ignore[assignment]

    if fitz is not None:
        try:
            texts: list[str] = []
            images: list[dict[str, Any]] = []
            matrix_factory: Any = fitz.Matrix
            document_factory: Any = fitz.open
            matrix = matrix_factory(_PDF_RASTER_ZOOM, _PDF_RASTER_ZOOM)
            with document_factory(str(path)) as doc:
                for page in doc:
                    page_text = (page.get_text() or "").strip()
                    if page_text:
                        texts.append(page_text)
                    if (
                        len(page_text) < _PDF_PAGE_SPARSE_TEXT_CHARS
                        and len(images) < _MAX_IMAGES
                    ):
                        pix = page.get_pixmap(matrix=matrix)
                        block = _normalize_image_bytes(pix.tobytes("png"))
                        if block:
                            images.append(block)
            return "\n".join(texts).strip(), images
        except Exception:
            logger.warning("PyMuPDF processing failed for %s; falling back to pypdf text", path)

    # Fallback: pypdf text-only (no raster). Better than nothing for text PDFs.
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        if text:
            return text, []
    except Exception:
        logger.warning("pypdf fallback failed for %s", path)
    raise FlexIntakeError("PDF has no readable text layer and could not be rasterized.")


def _extract_image(path: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FlexIntakeError("Could not read the uploaded image.") from exc
    block = _normalize_image_bytes(raw)
    if block is None:
        raise FlexIntakeError("Uploaded image could not be read.")
    return "", [block]


def _extract_msg(path: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        import extract_msg
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise FlexIntakeError("extract-msg is not installed; cannot read .msg files.") from exc

    try:
        message_factory: Any = extract_msg.Message
        msg = message_factory(str(path))
    except Exception as exc:
        raise FlexIntakeError("Could not read the .msg file (corrupt or unsupported).") from exc

    images: list[dict[str, Any]] = []
    text = ""
    try:
        text = (msg.body or "").strip()
        for att in msg.attachments:
            if len(images) >= _MAX_IMAGES:
                break
            try:
                name = (att.longFilename or att.shortFilename or "").lower()
                if not name.endswith(_IMAGE_EXTS):
                    continue
                data = att.data
                if not isinstance(data, (bytes, bytearray)):
                    continue
                block = _normalize_image_bytes(bytes(data))
                if block:
                    images.append(block)
            except Exception:
                # One bad attachment shouldn't sink the whole document.
                logger.warning("skipping unreadable .msg attachment in %s", path)
                continue
    finally:
        msg.close()
    return text, images


def normalize_flex_document(path: Path, suffix: str) -> tuple[str, list[dict[str, Any]]]:
    """Read a Flex document into ``(extracted_text, images)`` for AI extraction.

    ``images`` is a list of ``{"media_type": ..., "data": <base64>}``. Raises
    ``FlexIntakeError`` when nothing usable can be extracted (the endpoint maps
    this to a clean 422).
    """
    ext = suffix.lower()
    if ext == ".pdf":
        text, images = _extract_pdf(path)
    elif ext in (".png", ".jpg", ".jpeg"):
        text, images = _extract_image(path)
    elif ext == ".msg":
        text, images = _extract_msg(path)
    else:  # pragma: no cover - guarded by FLEX_SUFFIXES at the endpoint
        raise FlexIntakeError(f"Unsupported Flex document type: {ext}")

    if not text and not images:
        raise FlexIntakeError(
            "No readable content found in the document (no text, no images)."
        )
    return text, images
