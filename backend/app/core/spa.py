"""Serve the built SPA from the API process.

Only used by single-host deployments. When the platform has no per-tenant
subdomains, the SPA and the API MUST share an origin — the HR refresh cookie is
host-only and `SameSite=Strict` (`core/hr_auth.set_refresh_cookie`), so it is
only ever returned to the exact host that set it. A separately-hosted frontend
calling a different API host could never refresh a session.

Mounting is conditional on the build output existing, so local dev (Vite on
:5173 proxying to :8000) is completely unaffected.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Hashed asset filenames (Vite) are safe to cache forever; index.html must not
# be, or a deploy leaves browsers pinned to a stale bundle referencing assets
# that no longer exist.
_IMMUTABLE_MAX_AGE = 31536000


def spa_dir() -> Path | None:
    """The directory holding `index.html`, or None when no SPA was bundled."""
    raw = os.environ.get("INSPRO_SPA_DIR", "").strip()
    candidate = Path(raw) if raw else Path(__file__).resolve().parents[2] / "static"
    return candidate if (candidate / "index.html").is_file() else None


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

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, full_path: str) -> FileResponse:
        """Return index.html for client-side routes.

        A deep link like /portal/claims/new is a SPA route, not a file — the
        browser must still receive the app shell so the router can resolve it.
        API paths are excluded so a typo'd endpoint 404s as JSON instead of
        silently returning HTML, which is far harder to debug from the client.
        """
        if full_path.startswith(api_prefix.strip("/")):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

        # Serve a real file when one matches (favicon, logos, manifest...).
        # `resolve()` + containment check prevents `../` escaping the bundle.
        if full_path:
            target = (root / full_path).resolve()
            if target.is_file() and target.is_relative_to(root.resolve()):
                return FileResponse(target, headers=_cache_headers(full_path))

        return FileResponse(
            index, headers={"Cache-Control": "no-cache, must-revalidate"}
        )

    logger.info("Serving SPA from %s", root)
    return True


def _cache_headers(path: str) -> dict[str, str]:
    if path.startswith("assets/"):
        return {"Cache-Control": f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable"}
    return {"Cache-Control": "public, max-age=3600"}
