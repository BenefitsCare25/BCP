"""Audit log writer — call from mutating endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser
from app.models.audit_log import AuditLog

if TYPE_CHECKING:
    from app.core.portal_auth import CurrentMember

# Explicit set of secret-bearing key names. Audit rows aren't intended to
# carry credentials, but if an upstream payload echoes one we drop it.
# Note: `input_tokens` / `output_tokens` are LEGITIMATE spend counts, not
# secrets — keep them. Only `_token` suffixed credentials are redacted.
_REDACT_KEYS_EXACT = {
    "api_key",
    "apikey",
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "authorization",
    "bearer",
    "auth_token",
    "access_token",
    "refresh_token",
    "id_token",
    # Defense-in-depth: a future handler that dumps `row.__dict__` shouldn't
    # leak the Fernet ciphertext into audit rows even though it's encrypted.
    # `client_ai_configs` and the `platform_ai_settings` singleton name theirs
    # differently, so both belong here.
    "encrypted_api_key",
    "encrypted_service_account",
    # The CLEARTEXT service-account private key arrives under this name on the
    # platform-key upsert payload (BYOK's cleartext field is `api_key`, above)
    # — the one that actually matters if a handler ever audits a request body.
    "service_account_json",
}
_REDACT_PLACEHOLDER = "[redacted]"


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k in _REDACT_KEYS_EXACT


def _scrub(value: Any) -> Any:
    """Recursively redact secret-bearing keys in audit payloads."""
    if isinstance(value, dict):
        return {
            k: (_REDACT_PLACEHOLDER if _is_secret_key(k) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def write_member_audit(
    db: Session,
    member: CurrentMember,
    action: str,
    entity_type: str,
    entity_id: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    employee_id: str | None = None,
) -> None:
    """Append an audit row for a portal-member action. Caller must commit.

    Mirrors `write_audit` but records the member account as the actor
    (`actor_type="member"`) so portal activity is queryable alongside broker
    events in the same trail.
    """
    db.add(
        AuditLog(
            client_id=member.client_id,
            user_id=None,
            actor_type="member",
            member_account_id=member.member_account_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            employee_id=employee_id,
            before=_scrub(before) if before is not None else None,
            after=_scrub(after) if after is not None else None,
            cross_tenant_access=False,
        )
    )


def write_audit(
    db: Session,
    user: CurrentUser,
    action: str,
    entity_type: str,
    entity_id: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    employee_id: str | None = None,
) -> None:
    """Append an audit row. Caller must commit.

    Secret-looking keys in `before`/`after` are redacted before persistence
    so an audit row never echoes credentials lifted from upstream payloads.

    Pass ``employee_id`` for member-scoped events (coverage changes, enrollment
    actions) so the per-employee coverage-history view can filter on an indexed
    column instead of scanning JSON payloads.
    """
    db.add(
        AuditLog(
            client_id=user.client_id,
            user_id=user.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            employee_id=employee_id,
            before=_scrub(before) if before is not None else None,
            after=_scrub(after) if after is not None else None,
            cross_tenant_access=user.role == ROLE_SYSTEM_ADMIN,
        )
    )
