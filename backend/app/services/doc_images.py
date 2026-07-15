"""Shared document → vision content-block helpers.

Used by both the Flex-scheme intake (``flex_intake.py``) and the claims AI
review pipeline (``claims_review/``) so image normalization lives in one
place. ``normalize_image_bytes`` downscales / re-encodes raw image bytes into
a base64 vision block; ``vision_blocks_for_document`` turns a stored claim
document (PDF or image) into ready-to-send Anthropic content blocks — PDFs go
as native ``document`` blocks (the provider reads them directly), images as
``image`` blocks.
"""
from __future__ import annotations

import base64
import io
from typing import Any

# Bound vision cost: max edge (px), and drop sub-thumbnail images.
MAX_EDGE_PX = 2000
MIN_EDGE_PX = 200


class DocImageError(RuntimeError):
    """Raised when document bytes cannot be turned into vision blocks."""


def normalize_image_bytes(raw: bytes) -> dict[str, Any] | None:
    """Normalize raw image bytes to a base64 vision block.

    Returns ``{"media_type": ..., "data": <base64>}`` or ``None`` when the bytes
    aren't a usable image (corrupt, or a tiny spacer/logo). Images that already
    fit the budget (PNG/JPEG, in-size, RGB/L) are passed through unchanged —
    only images needing a resize or mode conversion are re-encoded to PNG.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise DocImageError("Pillow is not installed; cannot process images.") from exc

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None

    if max(img.size) < MIN_EDGE_PX:
        return None  # spacer / logo — not a usable document image

    fmt = (img.format or "").upper()
    needs_convert = img.mode not in ("RGB", "L")
    needs_resize = max(img.size) > MAX_EDGE_PX

    # Fast path: already a web-safe format at an acceptable size — no re-encode.
    if not needs_convert and not needs_resize and fmt in ("PNG", "JPEG"):
        media = "image/png" if fmt == "PNG" else "image/jpeg"
        return {"media_type": media, "data": base64.b64encode(raw).decode("ascii")}

    if needs_convert:
        img = img.convert("RGB")
    if needs_resize:
        scale = MAX_EDGE_PX / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {"media_type": "image/png", "data": base64.b64encode(buf.getvalue()).decode("ascii")}


def vision_blocks_for_document(raw: bytes, suffix: str) -> list[dict[str, Any]]:
    """Anthropic content blocks for one stored claim document.

    ``.pdf`` becomes a native ``document`` block (the model reads the PDF
    directly — no rasterization needed); ``.png/.jpg/.jpeg`` become an
    ``image`` block. Raises ``DocImageError`` when the bytes are unusable.
    """
    ext = suffix.lower()
    if ext == ".pdf":
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            }
        ]
    if ext in (".png", ".jpg", ".jpeg"):
        block = normalize_image_bytes(raw)
        if block is None:
            raise DocImageError("Uploaded image could not be read.")
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block["media_type"],
                    "data": block["data"],
                },
            }
        ]
    raise DocImageError(f"Unsupported document type for AI review: {ext}")
