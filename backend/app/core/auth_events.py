"""The single sink for structured, tenant-tagged auth audit events.

Writes an `AuthEvent` row. The caller owns the commit (auth flows batch the
event with their own state change). Raw identifiers are never stored — only a
SHA-256 hash — so a failed-login log can't be mined for valid emails/IDs.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models import AuthEvent

# Canonical event types (keep in sync with docs/AUTH_DESIGN.md §4).
EVENT_LOGIN_SUCCESS = "login_success"
EVENT_LOGIN_FAIL = "login_fail"
EVENT_MFA_CHALLENGE = "mfa_challenge"
EVENT_MFA_FAIL = "mfa_fail"
EVENT_MFA_SUCCESS = "mfa_success"
EVENT_PASSWORD_RESET_REQUEST = "password_reset_request"
EVENT_PASSWORD_RESET_COMPLETE = "password_reset_complete"
EVENT_LOCKOUT = "lockout"
EVENT_TOKEN_REFRESH = "token_refresh"
EVENT_TOKEN_REUSE = "token_reuse_detected"
EVENT_LOGOUT = "logout"

OUTCOME_SUCCESS = "success"
OUTCOME_FAIL = "fail"
OUTCOME_BLOCKED = "blocked"


def hash_identifier(identifier: str) -> str:
    return hashlib.sha256(identifier.strip().lower().encode()).hexdigest()


def write_auth_event(
    db: Session,
    *,
    event_type: str,
    outcome: str,
    surface: str,
    subject_type: str | None = None,
    subject_id: str | None = None,
    client_id: str | None = None,
    broker_firm_id: str | None = None,
    identifier: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    subdomain: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuthEvent:
    """Append an auth event. Does NOT commit — the caller does."""
    from app.models import AuthEvent  # lazy: avoid import cost at module load

    row = AuthEvent(
        event_type=event_type,
        outcome=outcome,
        surface=surface,
        subject_type=subject_type,
        subject_id=subject_id,
        client_id=client_id,
        broker_firm_id=broker_firm_id,
        identifier_hash=hash_identifier(identifier) if identifier else None,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        subdomain=subdomain,
        detail=detail,
    )
    db.add(row)
    return row
