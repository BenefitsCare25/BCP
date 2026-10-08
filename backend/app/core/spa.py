"""Serve the built SPA from the API process.

Only used by single-host deployments. When the platform has no per-tenant
subdomains, the SPA and the API MUST share an origin — the HR refresh cookie is
host-only and `SameSite=Strict` (`core/hr_auth.set_refresh_cookie`), so it is
only ever returned to the exact host that set it. A separately-hosted frontend
calling a different API host could never refresh a session.

Mounting is conditional on the build output existing, so local dev (Vite on
:5173 proxying to :8000) is completely unaffected.

White-label: the app shell is branded server-side for the host's firm before
any script runs, so the tab title, theme colour and icons are the broker's
from the first paint. `index.html` is read once (re-read when the file
changes); a firm with no brand of its own gets the file exactly as built. The
same holds for `/site.webmanifest`, which is generated per host and wins over
the static file.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.core.tenant_resolution import request_firm
from app.services.brand import DEFAULT_BRAND, Brand, readable_foreground, resolve_brand
from app.services.brand_assets import asset_url

logger = logging.getLogger(__name__)

# Hashed asset filenames (Vite) are safe to cache forever; index.html must not
# be, or a deploy leaves browsers pinned to a stale bundle referencing assets
# that no longer exist.
_IMMUTABLE_MAX_AGE = 31536000
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

# Today's manifest (frontend/public/site.webmanifest), for the default brand.
DEFAULT_MANIFEST_NAME = "Inspro Insurance Brokers"
_MANIFEST_BACKGROUND = "#faf7f7"
_DEFAULT_MANIFEST_ICONS = (
    {"src": "/icon-192.png", "type": "image/png", "sizes": "192x192"},
    {"src": "/icon-512.png", "type": "image/png", "sizes": "512x512"},
)
_MIN_MANIFEST_ICON = 192

_HTML_TAG = re.compile(r"<html\b([^>]*)>", re.IGNORECASE)
_TITLE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)
_THEME_COLOR = re.compile(r'<meta\s+name="theme-color"\s+content="[^"]*"', re.IGNORECASE)
_ICON_LINK = re.compile(r'<link\s+rel="icon"[^>]*>', re.IGNORECASE)
_TOUCH_ICON_LINK = re.compile(r'<link\s+rel="apple-touch-icon"[^>]*>', re.IGNORECASE)

_index_cache: dict[Path, tuple[int, str]] = {}


def spa_dir() -> Path | None:
    """The directory holding `index.html`, or None when no SPA was bundled."""
    raw = os.environ.get("INSPRO_SPA_DIR", "").strip()
    candidate = Path(raw) if raw else Path(__file__).resolve().parents[2] / "static"
    return candidate if (candidate / "index.html").is_file() else None


def _index_markup(index: Path) -> str:
    """The built index.html, cached until the file changes (a redeploy)."""
    mtime = index.stat().st_mtime_ns
    cached = _index_cache.get(index)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    markup = index.read_text(encoding="utf-8")
    _index_cache[index] = (mtime, markup)
    return markup


def _host_brand(request: Request) -> Brand:
    """The host firm's brand; the default when no firm resolves.

    Blocking (database). A failure serves the default shell rather than no page:
    the app itself then reports what is wrong.
    """
    ctx = request_firm(request)
    if ctx is None:
        return DEFAULT_BRAND
    from app.db.session import SessionLocal  # lazy: tests rebind the engine

    try:
        with SessionLocal() as db:
            return resolve_brand(db, ctx.firm_id)
    except Exception:
        logger.exception("Could not resolve the brand for the app shell; serving the default")
        return DEFAULT_BRAND


def _icon_link(rel: str, meta: dict[str, Any]) -> str:
    url = html.escape(asset_url(meta) or "", quote=True)
    kind = html.escape(str(meta.get("content_type", "")), quote=True)
    sizes = f'{int(meta.get("width", 0))}x{int(meta.get("height", 0))}'
    return f'<link rel="{rel}" href="{url}" type="{kind}" sizes="{sizes}" />'


def _replace_links(markup: str, pattern: re.Pattern[str], replacement: str) -> str:
    """Swap every link `pattern` matches for one `replacement`, in place of the first."""
    seen = False

    def swap(_match: re.Match[str]) -> str:
        nonlocal seen
        first, seen = not seen, True
        return replacement if first else ""

    return pattern.sub(swap, markup)


def _palette(brand: Brand) -> dict[str, str]:
    """The `--brand-*` variables the SPA reads (`styles/brand.css`), keyed as
    `BrandProvider` sets them."""
    return {
        "--brand-primary": brand.primary_color,
        "--brand-primary-foreground": readable_foreground(brand.primary_color),
        "--brand-accent": brand.accent_color,
        "--brand-accent-foreground": readable_foreground(brand.accent_color),
    }


def _paint_html_tag(markup: str, brand: Brand) -> str:
    """Put a custom palette on `<html>` so the first paint is already branded
    (the SPA would otherwise show the default colours until `/public/site`)."""
    palette = _palette(brand)
    if {k: v.lower() for k, v in palette.items()} == _palette(DEFAULT_BRAND):
        return markup
    style = html.escape(";".join(f"{k}:{v}" for k, v in palette.items()), quote=True)
    return _HTML_TAG.sub(
        lambda m: f'<html{m.group(1)} data-brand="custom" style="{style}">', markup, count=1
    )


def brand_index(markup: str, brand: Brand) -> str:
    """index.html with `brand`'s title, theme colour and icons.

    Values are HTML-escaped and inserted through functions (never as `re`
    replacement templates). A tag the build no longer contains is left alone;
    the default brand returns the markup unchanged.
    """
    if brand.is_default:
        return markup
    title = html.escape(brand.product_name, quote=True)
    markup = _TITLE.sub(lambda _m: f"<title>{title}</title>", markup, count=1)
    markup = _paint_html_tag(markup, brand)
    color = html.escape(brand.primary_color, quote=True)
    markup = _THEME_COLOR.sub(
        lambda _m: f'<meta name="theme-color" content="{color}"', markup, count=1
    )
    icon = brand.assets.get("favicon") or brand.assets.get("mark")
    if icon and asset_url(icon):
        markup = _replace_links(markup, _ICON_LINK, _icon_link("icon", icon))
    touch = brand.assets.get("mark")
    if touch and touch.get("content_type") == "image/png" and asset_url(touch):
        markup = _replace_links(markup, _TOUCH_ICON_LINK, _icon_link("apple-touch-icon", touch))
    return markup


def brand_manifest(brand: Brand) -> dict[str, Any]:
    """The web-app manifest for `brand`: its names, colour and square images of
    at least 192 px (mark first), else the built-in icons."""
    icons = []
    for slot in ("mark", "favicon"):
        meta = brand.assets.get(slot)
        url = asset_url(meta)
        if (
            meta and url and meta.get("content_type") != "image/x-icon"
            and int(meta.get("width", 0)) >= _MIN_MANIFEST_ICON
        ):
            size = f'{int(meta["width"])}x{int(meta["height"])}'
            icons.append({"src": url, "type": str(meta["content_type"]), "sizes": size})
    return {
        "name": DEFAULT_MANIFEST_NAME if brand.is_default else brand.product_name,
        "short_name": brand.short_name,
        "icons": icons or [dict(icon) for icon in _DEFAULT_MANIFEST_ICONS],
        "theme_color": brand.primary_color,
        "background_color": _MANIFEST_BACKGROUND,
        "display": "standalone",
        "start_url": "/",
    }


def mount_spa(app: FastAPI, api_prefix: str) -> bool:
    """Serve the SPA at `/`, leaving the API and probes untouched.

    Must be called AFTER every router is registered: the catch-all route below
    would otherwise shadow them. Returns whether a bundle was found.
    """
    root = spa_dir()
    if root is None:
        logger.info("No SPA bundle found — serving API only.")
        return False

    index = root / "index.html"
    # Vite emits every hashed artifact under assets/.
    assets = root / "assets"
    if assets.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=assets),
            name="spa-assets",
        )

    async def app_shell(request: Request) -> Response:
        brand = await run_in_threadpool(_host_brand, request)
        markup = brand_index(_index_markup(index), brand)
        return Response(markup, media_type="text/html; charset=utf-8", headers=_NO_CACHE)

    # Registered before the catch-all so it wins over the static file.
    @app.get("/site.webmanifest", include_in_schema=False)
    async def web_manifest(request: Request) -> Response:
        """The host firm's web-app manifest (home-screen name, colour, icons)."""
        brand = await run_in_threadpool(_host_brand, request)
        return Response(
            json.dumps(brand_manifest(brand), indent=2),
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, full_path: str) -> Response:
        """Return index.html for client-side routes.

        A deep link like /portal/claims/new is a SPA route, not a file — the
        browser must still receive the app shell so the router can resolve it.
        API paths are excluded so a typo'd endpoint 404s as JSON instead of
        silently returning HTML, which is far harder to debug from the client.
        """
        if full_path.startswith(api_prefix.strip("/")):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

        # Serve a real file when one matches (favicon, logos...), except the
        # shell itself, which is always branded. `resolve()` + containment
        # check prevents `../` escaping the bundle.
        if full_path and full_path != "index.html":
            target = (root / full_path).resolve()
            if target.is_file() and target.is_relative_to(root.resolve()):
                return FileResponse(target, headers=_cache_headers(full_path))

        return await app_shell(request)

    logger.info("Serving SPA from %s", root)
    return True


def _cache_headers(path: str) -> dict[str, str]:
    if path.startswith("assets/"):
        return {"Cache-Control": f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable"}
    return {"Cache-Control": "public, max-age=3600"}
