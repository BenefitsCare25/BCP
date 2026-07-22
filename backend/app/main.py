"""FastAPI entry point for the Inspro backend."""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # loads backend/.env before settings / crypto initialise

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1 import (
    adc,
    admin,
    ai_config,
    ai_spend,
    audit_log,
    bulk_plan_updates,
    categories,
    claim_doc_types,
    claims,
    dashboard,
    dependants,
    employees,
    enrollment_windows,
    enrollments,
    entity_aliases,
    flex_pricing,
    flex_schemes,
    insurers,
    leave_policies,
    matches,
    member_accounts,
    panel_cards,
    panel_listings,
    panel_setup,
    placement_slips,
    plan_overrides,
    plans,
    policy_years,
    portal,
    portal_auth,
    portal_claims,
    portal_dependants,
    portal_enrollment,
    portal_preview,
    product_setups,
    product_terms,
    recommendations,
    reports,
    schemas_api,
    session,
    system,
    underwriting,
    voluntary_rates,
)
from app.core import drift_checks
from app.core.deps import require_write_access
from app.core.rate_limit import RateLimitExceeded, limiter
from app.core.request_context import RequestIDMiddleware, install_log_filter
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.telemetry import configure_telemetry

logger = logging.getLogger(__name__)


def _allowed_origins() -> list[str]:
    raw = os.environ.get(
        "INSPRO_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Recover claims left in `ai_review_pending` by an interrupted background
    # review (a deploy IS a restart, so this fires exactly when strandings
    # happen). Never raises. Runs on real server startup only — TestClient(app)
    # without a `with` block doesn't enter the lifespan, so tests are unaffected.
    from app.services.claims_review.recovery import recover_stranded_reviews

    try:
        recovered = recover_stranded_reviews()
        if recovered:
            logger.warning(
                "Startup: reverted %s stranded claim review(s) to manual review",
                recovered,
            )
    except Exception:  # pragma: no cover - belt-and-braces; the sweep is self-guarding
        logger.exception("Startup stranded-review recovery raised")
    yield


def create_app() -> FastAPI:
    install_log_filter()
    configure_telemetry()
    drift_checks.run_all()
    from app.core.settings import get_settings

    # Interactive docs + the OpenAPI schema are dev-only: in staging/prod they
    # hand an unauthenticated caller the full endpoint/parameter map.
    docs_enabled = get_settings().env == "dev"
    app = FastAPI(
        title="Inspro Backend",
        description="Inspro Group Benefits Configuration Platform — spike + v0 UI.",
        version="0.2.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=_lifespan,
    )

    # Starlette runs middleware in reverse-add order; RequestIDMiddleware
    # (added last) runs first on the inbound path so downstream logging /
    # auditing can read the correlation ID.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Inspro-Client",
            "Accept",
        ],
        expose_headers=["X-Request-ID", "Content-Disposition", "X-FactFind-Notes"],
        max_age=600,
    )
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(RequestIDMiddleware)

    api_prefix = "/api/v1"
    api_routers = (
        adc.router,
        dashboard.router,
        policy_years.router,
        recommendations.router,
        schemas_api.router,
        insurers.router,
        entity_aliases.router,
        categories.router,
        placement_slips.router,
        plans.router,
        plan_overrides.router,
        enrollment_windows.router,
        enrollments.router,
        leave_policies.router,
        bulk_plan_updates.router,
        product_setups.router,
        product_terms.router,
        flex_pricing.router,
        flex_schemes.router,
        voluntary_rates.router,
        employees.router,
        dependants.router,
        member_accounts.router,
        panel_listings.router,
        panel_listings.year_router,
        panel_cards.router,
        panel_cards.year_router,
        panel_setup.router,
        portal_preview.router,
        reports.router,
        underwriting.router,
        claim_doc_types.router,
        claims.router,
        matches.router,
        audit_log.router,
        system.router,
        ai_spend.router,
        ai_config.router,
        session.router,
        admin.router,
    )
    for api_router in api_routers:
        app.include_router(
            api_router,
            prefix=api_prefix,
            dependencies=[Depends(require_write_access)],
        )

    # Employee portal — a SEPARATE auth surface. Deliberately registered
    # OUTSIDE the broker loop: `require_write_access` (broker identity) must
    # never run for members. `portal_auth` is public (OTP request/verify, its
    # own abuse guards); `portal` authenticates via its router-level
    # `get_current_member` dependency.
    app.include_router(portal_auth.router, prefix=api_prefix)
    app.include_router(portal.router, prefix=api_prefix)
    app.include_router(portal_claims.router, prefix=api_prefix)
    app.include_router(portal_claims.options_router, prefix=api_prefix)
    app.include_router(portal_dependants.router, prefix=api_prefix)
    app.include_router(portal_enrollment.router, prefix=api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe. Process is up — does NOT check DB connectivity."""
        return {"status": "ok"}

    @app.get("/readiness")
    async def readiness() -> dict[str, str]:
        """Readiness probe. Verifies the DB is reachable (cheap SELECT 1).

        Wired to App Service's health-check path is `/health` (faster, no
        DB hit); load balancers / kube readiness probes can use this one
        when DB-bound traffic should be steered away from a degraded node.
        """
        from sqlalchemy import text

        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
