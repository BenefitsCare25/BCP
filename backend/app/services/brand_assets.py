"""Brand images: the logo, mark and favicon a broker firm (or one of its
companies) shows.

An upload is identified by its DECODED format, never by its file name or the
content type the browser declared: Pillow is only allowed to try the PNG, WebP
and ICO decoders, and ICO is accepted for the favicon only. SVG is never
accepted (it is a script container). Each slot's dimension limits are checked
from the image header before any pixels are decoded; the pixels are then
re-encoded from scratch, which drops metadata (EXIF, text chunks, colour
profiles) and anything appended to the file (polyglots). The asset id is the
SHA-256 of the re-encoded bytes plus the extension, so it is content-addressed
and safe to cache forever.

Blobs are filed under the owning firm's storage prefix,
`<firm id>/brand/<asset id>` (core/storage.py's firm-first layout, like the
firm's panel-card library), and served by `GET /api/v1/public/brand-assets/<id>`
only on a host of a firm whose brand rows reference them. Removing an asset
only unreferences it: content-addressed blobs may be shared by the firm row and
its company rows, and a firm's whole prefix goes with the firm at offboarding.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import assert_key_in_scope, get_storage
from app.models.brand import BrandProfile

MAX_ASSET_BYTES = 512 * 1024
BRAND_SEGMENT = "brand"
ASSET_ID = re.compile(r"^[0-9a-f]{64}\.(?:png|webp|ico)$")
ASSET_URL_PREFIX = "/api/v1/public/brand-assets/"

_CONTENT_TYPES = {"png": "image/png", "webp": "image/webp", "ico": "image/x-icon"}
_EXTENSIONS = {"PNG": "png", "WEBP": "webp", "ICO": "ico"}
# Sizes a re-saved ICO carries, up to the source's own.
_ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


class BrandAssetError(ValueError):
    """An upload that cannot be a brand image (422)."""


class BrandAssetTooLarge(BrandAssetError):
    """An upload over `MAX_ASSET_BYTES` (413)."""


@dataclass(frozen=True)
class _SlotRule:
    formats: tuple[str, ...]
    min_width: int
    max_width: int
    min_height: int
    max_height: int
    square: bool
    label: str


_RULES: dict[str, _SlotRule] = {
    "logo": _SlotRule(("PNG", "WEBP"), 1, 1200, 64, 400, False,
                      "A logo must be at most 1200 x 400 pixels and at least 64 pixels tall."),
    "mark": _SlotRule(("PNG", "WEBP"), 64, 1024, 64, 1024, True,
                      "A mark must be square, from 64 to 1024 pixels."),
    "favicon": _SlotRule(("PNG", "WEBP", "ICO"), 16, 512, 16, 512, True,
                         "A favicon must be square, from 16 to 512 pixels."),
}


@dataclass(frozen=True)
class CleanAsset:
    """A validated, re-encoded brand image ready to store."""

    id: str
    content_type: str
    width: int
    height: int
    data: bytes

    def meta(self) -> dict[str, Any]:
        """What a brand row's `assets` holds for a slot."""
        return {
            "id": self.id,
            "content_type": self.content_type,
            "width": self.width,
            "height": self.height,
            "bytes": len(self.data),
        }


def _check_size(rule: _SlotRule, width: int, height: int) -> None:
    if (
        not rule.min_width <= width <= rule.max_width
        or not rule.min_height <= height <= rule.max_height
        or (rule.square and width != height)
    ):
        raise BrandAssetError(f"{rule.label} This image is {width} x {height}.")


def _encode(pixels: Any, fmt: str, size: tuple[int, int]) -> bytes:
    out = BytesIO()
    if fmt == "ICO":
        width = size[0]
        sizes = sorted({(n, n) for n in _ICO_SIZES if n <= width} | {size})
        pixels.save(out, format="ICO", sizes=sizes)
    elif fmt == "WEBP":
        pixels.save(out, format="WEBP", quality=90)
    else:
        pixels.save(out, format="PNG", optimize=True)
    return out.getvalue()


def clean_asset(slot: str, data: bytes) -> CleanAsset:
    """Validate an upload for `slot` and re-encode it without metadata.

    Raises `BrandAssetTooLarge` over the byte limit and `BrandAssetError` for
    anything that is not an allowed, well-formed, still image of the slot's size.
    """
    from PIL import Image  # lazy: only uploads need the decoders

    rule = _RULES.get(slot)
    if rule is None:
        raise BrandAssetError("Unknown brand image slot.")
    if len(data) > MAX_ASSET_BYTES:
        raise BrandAssetTooLarge(
            f"Brand images are limited to {MAX_ASSET_BYTES // 1024} KB."
        )
    try:
        with Image.open(BytesIO(data), formats=list(rule.formats)) as image:
            fmt = str(image.format)
            width, height = image.size
            _check_size(rule, width, height)
            if getattr(image, "is_animated", False):
                raise BrandAssetError("Animated images are not supported.")
            image.load()
            rgba = image.convert("RGBA")
    except BrandAssetError:
        raise
    except Exception as exc:
        allowed = "PNG, WebP or ICO" if "ICO" in rule.formats else "PNG or WebP"
        raise BrandAssetError(f"The file is not a valid {allowed} image.") from exc
    # A fresh image from the bare pixels carries no info/metadata at all.
    pixels = Image.frombytes("RGBA", rgba.size, rgba.tobytes())
    encoded = _encode(pixels, fmt, (width, height))
    ext = _EXTENSIONS[fmt]
    return CleanAsset(
        id=f"{hashlib.sha256(encoded).hexdigest()}.{ext}",
        content_type=_CONTENT_TYPES[ext],
        width=width,
        height=height,
        data=encoded,
    )


def asset_path(firm_id: str, asset_id: str) -> str:
    """Storage key of one of a firm's brand images."""
    if not ASSET_ID.fullmatch(asset_id):
        raise ValueError(f"Invalid brand asset id: {asset_id!r}")
    if not firm_id or "/" in firm_id or "\\" in firm_id or firm_id in {".", ".."}:
        raise ValueError(f"Invalid firm id for a brand asset: {firm_id!r}")
    return f"{firm_id}/{BRAND_SEGMENT}/{asset_id}"


def store_asset(firm_id: str, asset: CleanAsset) -> None:
    get_storage().save(BytesIO(asset.data), asset_path(firm_id, asset.id))


def read_asset(firm_id: str, asset_id: str) -> bytes:
    """A firm's brand image bytes. Raises FileNotFoundError when absent."""
    path = asset_path(firm_id, asset_id)
    assert_key_in_scope(path, firm_id, BRAND_SEGMENT)
    return get_storage().read(path)


def content_type_for(asset_id: str) -> str:
    return _CONTENT_TYPES[asset_id.rsplit(".", 1)[-1]]


def asset_url(meta: dict[str, Any] | None) -> str | None:
    """Public URL of an asset, or None when the slot is empty."""
    asset_id = (meta or {}).get("id")
    if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
        return None
    return f"{ASSET_URL_PREFIX}{asset_id}"


def firm_references_asset(db: Session, firm_id: str, asset_id: str) -> bool:
    """Whether any brand row of `firm_id` (firm or company) uses `asset_id`."""
    rows = db.scalars(
        select(BrandProfile.assets).where(BrandProfile.broker_firm_id == firm_id)
    ).all()
    return any(
        isinstance(meta, dict) and meta.get("id") == asset_id
        for assets in rows
        for meta in (assets or {}).values()
    )
