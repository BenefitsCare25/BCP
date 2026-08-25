"""FastAPI entry point for the Inspro backend."""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()  # loads backend/.env before settings / crypto initialise

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.v1 import (
    adc,
    admin,
    ai_config,
    ai_spend,
    audit_log,
    bulk_plan_updates,
    categories,
    claim_doc_types,
    claim_document_setups,
    claim_review_configs,
    claims,
    conversations,
    dashboard,
    dependant_query,
    dependants,
    dual_coverage,
    eligibility_mappings,
    employees,
    enquiries,
    enrollment_windows,
    enrollments,
    entity_aliases,
    flex_pricing,
    flex_schemes,
    hr_admin,
    hr_auth,
    insurers,
    leave_policies,
    matches,
    member_accounts,
    member_query,
    panel_cards,
    panel_listings,
    panel_setup,
    placement_slips,
    plan_overrides,
    plans,
    platform_ai_settings,
    policy_years,
    portal,
    portal_auth,
    portal_claims,
    portal_dependants,
    portal_enquiries,
    portal_enrollment,
    portal_messages,
    portal_preview,
    product_setups,
    product_terms,
    recommendations,
    report_versions,
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
from app.core.spa import mount_spa
from app.core.telemetry import configure_telemetry
from app.core.tenancy_host import TenantMiddleware

logger = logging.getLogger(__name__)

READINESS_TOTAL_TIMEOUT_SECONDS = 10.0


def _check_dependencies(redis_url: str | None) -> dict[str, str]:
    """Run blocking dependency probes outside the ASGI event loop."""
    from sqlalchemy import text

    from app.db.session import engine

    try:
        with engine.connect() as conn:
            # Bound database-side execution as well as connection and pool
            # acquisition (configured on the engine in app.db.session).
            if engine.dialect.name == "postgresql":
                conn.execute(text("SET LOCAL statement_timeout = 3000"))
            conn.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Readiness database probe failed", exc_info=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        ) from None

    redis_state = "not-required"
    if redis_url:
        from app.services.ai_cache import get_cache

        if not get_cache().ready():
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Redis is unavailable; shared AI cache and rate limits are degraded.",
            )
        redis_state = "ok"
    return {"status": "ready", "database": "ok", "redis": redis_state}


def _allowed_origins() -> list[str]:
    raw = os.environ.get(
        "INSPRO_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


def _handle_rate_limit(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)


def create_app() -> FastAPI:
    install_log_filter()
    configure_telemetry()
    drift_checks.run_all()
    from app.core.settings import get_settings

    # Interactive docs + the OpenAPI schema are dev-only: in staging/prod they
    # hand an unauthenticated caller the full endpoint/parameter map.
    settings = get_settings()
    from app.services.claims_ai_confidence import load_confidence_profile

    load_confidence_profile()
    if settings.env == "prod":
        from app.services.ai_cache import get_cache

        get_cache()
    docs_enabled = settings.env == "dev"
    app = FastAPI(
        title="Inspro Backend",
        description="Inspro Group Benefits Configuration Platform — spike + v0 UI.",
        version="0.2.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Starlette runs middleware in reverse-add order; RequestIDMiddleware
    # (added last) runs first on the inbound path so downstream logging /
    # auditing can read the correlation ID.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit)

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
    app.add_middleware(TenantMiddleware, base_domain=settings.base_domain)
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
        dual_coverage.router,
        eligibility_mappings.router,
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
        report_versions.router,
        report_versions.item_router,
        report_versions.registry_router,
        underwriting.router,
        claim_doc_types.router,
        claim_document_setups.router,
        claim_review_configs.router,
        claims.router,
        claims.employee_router,
        conversations.router,
        enquiries.router,
        matches.router,
        audit_log.router,
        system.router,
        ai_spend.router,
        ai_config.router,
        platform_ai_settings.router,
        session.router,
        admin.router,
        hr_admin.router,
    )
    for api_router in api_routers:
        app.include_router(
            api_router,
            prefix=api_prefix,
            dependencies=[Depends(require_write_access)],
        )

    # Roster QUERIES are reads that happen to be POST — `attributes[]` and the
    # nested employee filter do not survive a query string. `require_write_access`
    # gates on the HTTP METHOD, so leaving them in the loop above 403s every
    # `broker_viewer` out of the Member Listing and Dependants tables (and out
    # of the bulk picker's headcount, which has always been POST). Registered
    # here instead; every endpoint on both routers is read-only, and each still
    # authenticates + tenant-checks through `load_policy_year`.
    for read_router in (member_query.router, dependant_query.router):
        app.include_router(read_router, prefix=api_prefix)

    # Employee portal — a SEPARATE auth surface. Deliberately registered
    # OUTSIDE the broker loop: `require_write_access` (broker identity) must
    # never run for members. `portal_auth` is public (OTP request/verify, its
    # own abuse guards); `portal` authenticates via its router-level
    # `get_current_member` dependency.
    app.include_router(portal_auth.router, prefix=api_prefix)
    # HR credential-login surface — public auth, its own tenant + lockout guards
    # (mirrors portal_auth: registered OUTSIDE the broker require_write_access gate).
    app.include_router(hr_auth.router, prefix=api_prefix)
    app.include_router(portal.router, prefix=api_prefix)
    app.include_router(portal_claims.router, prefix=api_prefix)
    app.include_router(portal_claims.options_router, prefix=api_prefix)
    app.include_router(portal_dependants.router, prefix=api_prefix)
    app.include_router(portal_enrollment.router, prefix=api_prefix)
    app.include_router(portal_messages.router, prefix=api_prefix)
    app.include_router(portal_enquiries.router, prefix=api_prefix)

    @app.get("/health")
    async def health(response: Response) -> dict[str, str]:
        """Liveness probe. Process is up — does NOT check DB connectivity."""
        response.headers["X-Inspro-Version"] = os.environ.get("INSPRO_GIT_SHA", "unknown")
        return {"status": "ok"}

    @app.get("/readiness")
    async def readiness() -> dict[str, str]:
        """Deep readiness probe for traffic and synthetic monitoring.

        Blocking dependency calls run in a worker thread and the whole probe is
        time-bounded, so a database outage cannot stall every ASGI request.
        App Service itself uses `/health`, which is dependency-free liveness.
        """
        try:
            return await asyncio.wait_for(
                run_in_threadpool(_check_dependencies, settings.redis_url),
                timeout=READINESS_TOTAL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Readiness probe exceeded %.1f seconds",
                READINESS_TOTAL_TIMEOUT_SECONDS,
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Dependency readiness check timed out.",
            ) from None

    # LAST: the SPA catch-all would shadow any route registered after it.
    # No-op unless a bundle was baked into the image (single-host deploys).
    mount_spa(app, api_prefix)

    return app


app = create_app()
