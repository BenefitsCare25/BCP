"""Local-credential auth: password, MFA, sessions, events, per-tenant policy.

All control-plane tables (they live in ``public``): authentication resolves
*before* a firm schema is known. The broker surface (Entra) and member surface
(email OTP) do NOT use ``auth_credentials`` — that table backs the HR credential
login. ``auth_mfa`` / ``auth_sessions`` / ``auth_events`` are surface-agnostic
(``subject_type`` discriminates user vs member).

See ``docs/AUTH_DESIGN.md`` §4 for the full rationale.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

# auth_mfa / auth_sessions subject discriminator
SUBJECT_USER = "user"
SUBJECT_MEMBER = "member"

# auth_events surfaces
SURFACE_BROKER = "broker"
SURFACE_HR = "hr"
SURFACE_PORTAL = "portal"


class AuthCredential(Base, TimestampMixin):
    """Local password for a credential-login principal (HR admins today).

    One row per ``users`` row that has a local password. Entra users have NO
    row here → they cannot local-login (fail-closed). Kept out of ``users`` so
    the Entra surface stays password-free and this generalizes to any future
    local surface.
    """

    __tablename__ = "auth_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_auth_credentials_user_id"),
        UniqueConstraint(
            "broker_firm_id", "hr_login_id", name="uq_auth_credentials_firm_login_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized from the user so the system-generated login id is unique
    # within the firm without a join.
    broker_firm_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # System-generated opaque HR user id (e.g. "HR-7Q2M8K"); alternative to email.
    hr_login_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Argon2id PHC string (algorithm + params embedded — upgradable on next login).
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Per-tenant forced-rotation deadline; NULL = no forced rotation.
    must_rotate_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Per-identifier lockout (exponential backoff); NULL = not locked.
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthMfa(Base, TimestampMixin):
    """TOTP enrolment for a user or member. Secret is Fernet-encrypted."""

    __tablename__ = "auth_mfa"
    __table_args__ = (
        UniqueConstraint(
            "subject_type", "subject_id", name="uq_auth_mfa_subject"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Fernet ciphertext (ascii) of the base32 TOTP secret — never plaintext.
    totp_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # Enrolment isn't trusted until the first valid code confirms it.
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Single-use recovery codes, stored as hashes (list[str]).
    recovery_codes: Mapped[list | None] = mapped_column(JSON(), nullable=True)
    # Replay guard: reject reuse of the same 30s step.
    last_used_step: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AuthSession(Base, TimestampMixin):
    """Rotating refresh-token family. Only the token HASH is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_family_id", "family_id"),
        Index("ix_auth_sessions_refresh_hash", "refresh_hash"),
        Index("ix_auth_sessions_subject", "subject_type", "subject_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    broker_firm_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Rotation lineage: reuse of a rotated token revokes the whole family.
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subdomain: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AuthEvent(Base):
    """Structured, tenant-tagged auth audit — append-only, PDPA-retained.

    Deliberately separate from ``audit_log`` (mutation/entity oriented): auth
    events are high-volume, may have no resolved subject (failed login), and
    carry a distinct retention policy. Not a ``TimestampMixin`` — an append-only
    log needs an indexed ``occurred_at``, not a mutable ``updated_at``.
    """

    __tablename__ = "auth_events"
    __table_args__ = (
        Index("ix_auth_events_occurred_at", "occurred_at"),
        Index("ix_auth_events_event_type", "event_type"),
        Index("ix_auth_events_client_id", "client_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    surface: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    broker_firm_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Hash of the attempted identifier — never store a raw email/id on failure.
    identifier_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subdomain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON(), nullable=True)


# Which identifier a surface's users type as their username. Broker-configured
# per company, per surface (HR + employee portal).
LOGIN_SOURCE_EMAIL = "email"
LOGIN_SOURCE_SYSTEM_ID = "system_id"  # broker-generated (HR-xxxx / member id)
LOGIN_SOURCE_STAFF_ID = "staff_id"  # employee id from the member listing
LOGIN_SOURCES = frozenset({LOGIN_SOURCE_EMAIL, LOGIN_SOURCE_SYSTEM_ID, LOGIN_SOURCE_STAFF_ID})


class ClientAuthPolicy(Base, TimestampMixin):
    """Per-tenant sign-in settings (config, not identity). One row per client.

    The broker controls, per surface: which identifier is the login username
    (`*_login_source`) and whether two-factor is available (`mfa_*_enabled`,
    self-enrol / optional).
    """

    __tablename__ = "client_auth_policy"

    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True
    )
    # ── Two-factor availability (broker on/off; enrolment is self-service) ──
    mfa_hr_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    mfa_portal_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    # ── Login username source per surface ──
    hr_login_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=LOGIN_SOURCE_EMAIL,
        default=LOGIN_SOURCE_EMAIL,
    )
    portal_login_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=LOGIN_SOURCE_EMAIL,
        default=LOGIN_SOURCE_EMAIL,
    )
    # ── Password policy (applies to any password-based surface) ──
    password_min_entropy: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="60", default=60
    )
    password_rotation_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_idle_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="30", default=30
    )
    session_absolute_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="12", default=12
    )
    breach_check_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
