"""Platform-wide AI credentials + spend/concurrency limits (single global row).

These span EVERY client because all tenants share one Vertex key/quota, so
they're system-admin-scoped and live in ``public`` (a CONTROL table), not a
firm schema. Nullable limit columns are tri-state: NULL = inherit the env
fallback (``INSPRO_AI_*``), a value = explicit (0 = disabled). Resolved via
``services/platform_ai_settings.py``; edited via ``api/v1/platform_ai_settings.py``.

The row also carries the PLATFORM Vertex credentials — the default key every
company runs on. Per-company BYOK (``client_ai_configs``) stays an optional
override on top; resolution order lives in ``core/ai_config.py``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# There is exactly one row; its id is fixed so upserts never race on lookup.
SINGLETON_ID = "platform"


class PlatformAISetting(Base, TimestampMixin):
    __tablename__ = "platform_ai_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=SINGLETON_ID)
    # NULL = inherit env fallback; a value (0 allowed = disabled) = explicit.
    # Token counts are BigInteger: a fleet cap can exceed int32 (2.1B) and the
    # validator allows up to 1e12 — Integer would overflow on Postgres (int32),
    # invisibly, since SQLite has no such limit. max_concurrent_calls stays a
    # small Integer.
    platform_monthly_token_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    default_monthly_token_budget: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_concurrent_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Platform Vertex credentials (the default key, NULL = none set) ------
    # Mirrors `client_ai_configs` field-for-field so one drawer/one test path
    # serves both surfaces. `location` is the GCP region (BYOK calls it
    # `endpoint`); the encrypted blob is `pack_vertex_secret(project, sa_json)`.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    encrypted_service_account: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    # First 16 hex chars of sha256(plaintext) — non-reversible change marker.
    key_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validation_error: Mapped[str | None] = mapped_column(String(512), nullable=True)


class PlatformAIUsage(Base, TimestampMixin):
    """Running monthly token total across ALL firms/clients (one row per month).

    A CONTROL table (public) on purpose: ``AISpendLog`` is a tenant table that,
    on Postgres, lives in per-firm schemas — a ``search_path``-scoped SUM over it
    only sees the active firm, so it can't back a cap meant to guard the SHARED
    key/quota. This counter is incremented on every non-cache spend from whatever
    firm session made the call (control tables resolve to ``public`` regardless
    of ``search_path``), so ``platform_month_to_date_tokens`` reads a true
    cross-firm total. Forward-accruing — it starts counting from deploy.
    """

    __tablename__ = "platform_ai_usage"

    # "YYYY-MM" (UTC). MTD = the current month's row.
    year_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
