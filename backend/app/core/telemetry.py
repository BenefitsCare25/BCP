"""Application Insights + OpenTelemetry initialisation.

When `APPLICATIONINSIGHTS_CONNECTION_STRING` is set, the Azure-Monitor
OpenTelemetry distro auto-instruments FastAPI, SQLAlchemy, and httpx; traces
and logs are sent to the configured App Insights component.

With `INSPRO_APPINSIGHTS_AUTH=managed_identity` (set on Azure) telemetry is
sent with the app's managed identity, so the connection string only locates
the resource: once local auth is disabled on App Insights, a leaked copy of it
can send nothing.

The init is wrapped in a try/except so a missing optional dep (running tests
without the distro installed) doesn't break the app — telemetry is best-effort.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
_configured = False


def configure_telemetry() -> None:
    global _configured
    if _configured:
        return
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not conn:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set; skipping telemetry init")
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry not installed — telemetry disabled. "
            "Add `azure-monitor-opentelemetry` to backend dependencies."
        )
        return

    options: dict[str, Any] = {"connection_string": conn, "logger_name": "app"}
    if os.environ.get("INSPRO_APPINSIGHTS_AUTH", "").strip().lower() == "managed_identity":
        from azure.identity import ManagedIdentityCredential

        options["credential"] = ManagedIdentityCredential()
    try:
        configure_azure_monitor(**options)
        _configured = True
        logger.info("Azure Monitor telemetry configured")
    except Exception:
        # Non-fatal: an unreachable App Insights endpoint must not crash the
        # app at boot.
        logger.exception("configure_azure_monitor failed; telemetry disabled")
