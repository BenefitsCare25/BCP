"""Audit log writer — call from mutating endpoints."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser
from app.core.request_context import (
    client_ip,
    get_client_ip,
    get_request_id,
    get_user_agent,
    user_agent,
)
from app.models.audit_log import AuditLog
from app.models.platform import PlatformAuditLog
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    DOB_KEYS,
    EMAIL_KEYS,
    EMPLOYEE_ID_KEYS,
    mask_nric,
)

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

# Platform records: firm users, their invitations and the firm itself. They are
# not company data, whichever company the actor had selected when the row was
# written, so only system admins read them in the activity feed.
PLATFORM_ENTITY_TYPES = frozenset({"user", "invitation", "broker_firm"})

_MASKED_PLACEHOLDER = "[masked]"
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_NRIC_IN_TEXT = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)


def _field_words(key: str) -> tuple[str, ...]:
    """A payload key as lowercase words: "dateOfBirth", "Date of Birth" and
    "date_of_birth" are all ("date", "of", "birth")."""
    spaced = _CAMEL_BOUNDARY.sub("_", key).casefold()
    return tuple(word for word in _NON_WORD.split(spaced) if word)


def _field_names(keys: tuple[str, ...]) -> frozenset[str]:
    return frozenset("_".join(_field_words(key)) for key in keys)


# Personal-data fields in an audit payload, matched on the roster's own
# spellings plus any key containing one of the marker words.
_IDENTIFIER_FIELDS = _field_names((*EMPLOYEE_ID_KEYS, *DEPENDANT_ID_KEYS, "employee_id_no"))
_IDENTIFIER_WORDS = frozenset({"nric", "fin", "passport"})
_PERSONAL_FIELDS = _field_names(
    (*DOB_KEYS, *EMAIL_KEYS, "contact_no", "contact_number", "tel", "hp")
)
_PERSONAL_WORDS = frozenset({
    "dob", "birth", "birthdate", "dateofbirth",
    "email", "phone", "mobile", "handphone", "telephone",
    "bank", "salary", "income", "wage", "wages",
})


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
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def mask_personal_data(value: Any, field: str = "") -> Any:
    """Copy of an audit payload with personal data masked, for read-only roles.

    Identification numbers keep the shape every report uses (``mask_nric``:
    ``S******7D``), so a change still reads as a change; dates of birth,
    contact details, bank details and salary become ``[masked]``. NRIC/FIN-
    shaped text is masked wherever it appears, whatever its key. Empty values
    stay empty — that a field was blank is not personal data.
    """
    if value is None or value == "":
        return value
    words = _field_words(field)
    name = "_".join(words)
    identifier = name in _IDENTIFIER_FIELDS or not _IDENTIFIER_WORDS.isdisjoint(words)
    if identifier and isinstance(value, (str, int)) and not isinstance(value, bool):
        return mask_nric(value)
    if identifier or name in _PERSONAL_FIELDS or not _PERSONAL_WORDS.isdisjoint(words):
        return _MASKED_PLACEHOLDER
    if isinstance(value, dict):
        return {k: mask_personal_data(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_personal_data(v) for v in value]
    if isinstance(value, str):
        return _NRIC_IN_TEXT.sub(lambda match: mask_nric(match.group()), value)
    return value


def _request_ip(request: Request | None) -> str | None:
    """The caller's IP: from ``request`` when given, else the one
    `RequestIDMiddleware` captured for the current request. Bounded to the
    column width so a malformed forwarded value cannot fail the write."""
    value = client_ip(request) if request is not None else get_client_ip()
    return value[:64] if value else None


def _request_user_agent(request: Request | None) -> str | None:
    value = user_agent(request) if request is not None else get_user_agent()
    return value[:512] if value else None


class _Company(Enum):
    # `write_audit`'s default: stamp the row with the actor's active company.
    # A sentinel rather than None, because None is a real stamp (no company).
    ACTOR = "actor"


_ACTOR_COMPANY: Literal[_Company.ACTOR] = _Company.ACTOR


def write_member_audit(
    db: Session,
    member: CurrentMember,
    action: str,
    entity_type: str,
    entity_id: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    employee_id: str | None = None,
    request: Request | None = None,
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
            request_id=get_request_id(),
            ip_address=_request_ip(request),
            user_agent=_request_user_agent(request),
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
    request: Request | None = None,
    *,
    client_id: str | None | Literal[_Company.ACTOR] = _ACTOR_COMPANY,
) -> None:
    """Append an audit row. Caller must commit.

    Secret-looking keys in `before`/`after` are redacted before persistence
    so an audit row never echoes credentials lifted from upstream payloads.

    Pass ``employee_id`` for member-scoped events (coverage changes, enrollment
    actions) so the per-employee coverage-history view can filter on an indexed
    column instead of scanning JSON payloads.

    ``client_id`` is the company the RESOURCE belongs to. It defaults to the
    actor's active company, which is the same thing for every tenant-scoped
    role; pass it when a system admin can reach another company's record, and
    pass None for a platform record that belongs to no company. A system
    admin's row about a company is flagged ``cross_tenant_access``.

    The caller's IP and user agent come from ``request`` when given, else from
    the request `RequestIDMiddleware` is handling.
    """
    company = user.client_id if client_id is _ACTOR_COMPANY else client_id
    db.add(
        AuditLog(
            client_id=company,
            user_id=user.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            employee_id=employee_id,
            request_id=get_request_id(),
            ip_address=_request_ip(request),
            user_agent=_request_user_agent(request),
            before=_scrub(before) if before is not None else None,
            after=_scrub(after) if after is not None else None,
            cross_tenant_access=user.role == ROLE_SYSTEM_ADMIN and company is not None,
        )
    )


def write_access_audit(
    db: Session,
    user: CurrentUser,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: str,
    *,
    employee_id: str | None = None,
    client_id: str | None | Literal[_Company.ACTOR] = _ACTOR_COMPANY,
) -> None:
    """Record a successful read/download of sensitive claims data.

    Deliberately stores identifiers and request metadata only. Claim values and
    document contents must never be copied into the access trail.

    ``client_id`` has `write_audit`'s meaning: pass the company the record
    belongs to, so a system admin's read lands in that company's trail.
    """
    write_audit(
        db,
        user,
        action,
        entity_type,
        entity_id,
        employee_id=employee_id,
        request=request,
        client_id=client_id,
    )


def write_platform_audit(
    db: Session,
    user: CurrentUser,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    broker_firm_id: str | None = None,
    client_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a `platform_audit_log` row for a platform action. Caller must commit.

    The platform trail (firms, domains, master-admin access grants) lives in
    ``public`` and spans firms; it is append-only on Postgres. IP, user agent
    and request id come from the request `RequestIDMiddleware` is handling.
    ``occurred_at`` is stamped here rather than by the server default so rows
    written in the same second still page in order.
    """
    db.add(
        PlatformAuditLog(
            occurred_at=datetime.now(UTC),
            actor_user_id=user.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            broker_firm_id=broker_firm_id,
            client_id=client_id,
            detail=_scrub(detail) if detail is not None else None,
            ip_address=_request_ip(None),
            user_agent=_request_user_agent(None),
            request_id=get_request_id(),
        )
    )
