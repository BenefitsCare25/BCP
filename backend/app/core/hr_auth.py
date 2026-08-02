"""HR credential-login auth seam — a SEPARATE principal path, not Entra.

Client-company HR admins (`client_admin` / `client_hr`) sign in with an email
OR a system-generated HR user id + Argon2id password (+ optional TOTP) on their
tenant's subdomain (`{slug}.hr.<base>`). They receive a short-lived HS256 access
token (`typ:"hr"`) plus a rotating refresh token in a host-only cookie.

Key separation: the HR access token is signed with a key DERIVED from the portal
secret (HKDF-style HMAC with a fixed label), so it is cryptographically distinct
from the member token (which uses the portal secret directly) and from broker
Entra RS256 tokens — no surface can verify another's token.

The resolved principal is the SAME `CurrentUser` the broker surface uses, so HR
requests inherit every existing tenant/RBAC dep (`load_employee`, `user_owns`, …)
for free. The tenant (`client_id`) is baked into the token AND must match the
subdomain the request arrived on (defence against replaying a token across
tenants).
"""
from __future__ import annotations

import hmac
import logging
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import credentials as _credentials
from app.core import mfa
from app.core.auth import CurrentUser
from app.core.settings import Settings, get_settings
from app.core.tenancy_host import (
    SURFACE_HR,
    TenantContext,
    resolve_host_info,
    resolve_tenant_context,
)
from app.db.session import get_db
from app.db.tenancy import set_search_path
from app.models.auth import SUBJECT_USER

logger = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"
_TOKEN_TYPE_HR = "hr"
_TOKEN_TYPE_SET_PW = "hr_set_pw"
_TOKEN_TYPE_MFA = "hr_mfa"
_HR_KEY_LABEL = b"inspro-hr-access-token-v1"
_SET_PW_KEY_LABEL = b"inspro-hr-set-password-v1"
_MFA_KEY_LABEL = b"inspro-hr-mfa-challenge-v1"
MFA_CHALLENGE_TTL_MINUTES = 5

ACCESS_TTL_MINUTES = 10
SET_PW_TTL_HOURS = 48
REFRESH_COOKIE_NAME = "inspro_hr_refresh"
REFRESH_COOKIE_PATH = "/api/v1/hr/auth"

HR_ROLES = frozenset({"client_admin", "client_hr"})

# Re-exported so existing `HR.*` call sites keep working (shared with members).
is_locked = _credentials.is_locked
register_failure = _credentials.register_failure
reset_failures = _credentials.reset_failures
credential_version = _credentials.credential_version
next_rotation_deadline = _credentials.next_rotation_deadline
rotation_due = _credentials.rotation_due


def _derive_key(settings: Settings, label: bytes) -> str:
    return hmac.new(
        settings.portal_jwt_secret.encode(), label, "sha256"
    ).hexdigest()


# ── Access token ──────────────────────────────────────────────────────────────
def issue_hr_access_token(
    *, user_id: str, client_id: str, broker_firm_id: str | None, role: str
) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=ACCESS_TTL_MINUTES)
    token = jwt.encode(
        {
            "sub": user_id,
            "cid": client_id,
            "fid": broker_firm_id,
            "role": role,
            "typ": _TOKEN_TYPE_HR,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        _derive_key(settings, _HR_KEY_LABEL),
        algorithm=_JWT_ALGORITHM,
    )
    return token, expires_at


def _decode_hr_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    claims = jwt.decode(
        token,
        _derive_key(settings, _HR_KEY_LABEL),
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    if claims.get("typ") != _TOKEN_TYPE_HR:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


# ── Set-password / reset token (stateless, single-use via version stamp) ───────
def issue_set_password_token(user_id: str, version: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "v": version,
            "typ": _TOKEN_TYPE_SET_PW,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=SET_PW_TTL_HOURS)).timestamp()),
        },
        _derive_key(settings, _SET_PW_KEY_LABEL),
        algorithm=_JWT_ALGORITHM,
    )


def verify_set_password_token(token: str) -> tuple[str, int]:
    """Return (user_id, version) or raise. `version` must equal the credential's
    current stamp at redeem time — that makes the token single-use."""
    settings = get_settings()
    claims = jwt.decode(
        token,
        _derive_key(settings, _SET_PW_KEY_LABEL),
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    if claims.get("typ") != _TOKEN_TYPE_SET_PW:
        raise jwt.InvalidTokenError("wrong token type")
    return str(claims["sub"]), int(claims.get("v", 0))


# ── MFA challenge token (bridges login step 1 → TOTP step 2) ───────────────────
def issue_mfa_challenge_token(user_id: str, client_id: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "cid": client_id,
            "typ": _TOKEN_TYPE_MFA,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=MFA_CHALLENGE_TTL_MINUTES)).timestamp()),
        },
        _derive_key(settings, _MFA_KEY_LABEL),
        algorithm=_JWT_ALGORITHM,
    )


def verify_mfa_challenge_token(token: str) -> tuple[str, str]:
    settings = get_settings()
    claims = jwt.decode(
        token,
        _derive_key(settings, _MFA_KEY_LABEL),
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    if claims.get("typ") != _TOKEN_TYPE_MFA:
        raise jwt.InvalidTokenError("wrong token type")
    return str(claims["sub"]), str(claims.get("cid", ""))


# ── Per-tenant auth policy (row → defaults) ────────────────────────────────────
@dataclass(frozen=True)
class ResolvedAuthPolicy:
    mfa_hr_enabled: bool
    mfa_portal_enabled: bool
    hr_login_source: str
    portal_login_source: str
    password_min_entropy: int
    password_rotation_days: int | None
    session_idle_minutes: int
    session_absolute_hours: int
    breach_check_enabled: bool


def get_auth_policy(db: Session, client_id: str) -> ResolvedAuthPolicy:
    from app.models import ClientAuthPolicy

    row = db.get(ClientAuthPolicy, client_id)
    if row is None:
        return ResolvedAuthPolicy(
            mfa_hr_enabled=False,
            mfa_portal_enabled=False,
            hr_login_source="email",
            portal_login_source="email",
            password_min_entropy=60,
            password_rotation_days=None,
            session_idle_minutes=30,
            session_absolute_hours=12,
            breach_check_enabled=True,
        )
    return ResolvedAuthPolicy(
        mfa_hr_enabled=row.mfa_hr_enabled,
        mfa_portal_enabled=row.mfa_portal_enabled,
        hr_login_source=row.hr_login_source,
        portal_login_source=row.portal_login_source,
        password_min_entropy=row.password_min_entropy,
        password_rotation_days=row.password_rotation_days,
        session_idle_minutes=row.session_idle_minutes,
        session_absolute_hours=row.session_absolute_hours,
        breach_check_enabled=row.breach_check_enabled,
    )


# ── TOTP MFA (thin wrappers over the shared, subject-agnostic core.mfa) ────────
def user_has_confirmed_mfa(db: Session, user_id: str) -> bool:
    return mfa.has_confirmed(db, SUBJECT_USER, user_id)


def user_mfa_status(db: Session, user_id: str) -> str:
    return mfa.status_for(db, SUBJECT_USER, user_id)


def verify_user_totp(db: Session, user_id: str, code: str) -> bool:
    return mfa.verify_totp(db, SUBJECT_USER, user_id, code)


def consume_recovery_code(db: Session, user_id: str, code: str) -> bool:
    return mfa.consume_recovery_code(db, SUBJECT_USER, user_id, code)


def start_user_mfa_enrollment(db: Session, user_id: str, account: str) -> tuple[str, str]:
    return mfa.start_enrollment(db, SUBJECT_USER, user_id, account)


def confirm_user_mfa_enrollment(db: Session, user_id: str, code: str) -> list[str] | None:
    return mfa.confirm_enrollment(db, SUBJECT_USER, user_id, code)


def disable_user_mfa(db: Session, user_id: str) -> None:
    mfa.disable(db, SUBJECT_USER, user_id)


# ── HR login id ────────────────────────────────────────────────────────────────
_ID_ALPHABET = string.ascii_uppercase + string.digits


def generate_hr_login_id() -> str:
    """Opaque, non-guessable HR user id (e.g. "HR-7Q2M8K"). NEVER an NRIC."""
    body = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"HR-{body}"


# ── Login resolution ───────────────────────────────────────────────────────────
def resolve_hr_credential(db: Session, tenant: TenantContext, identifier: str):
    """Locate the (User, AuthCredential) for an identifier WITHIN a tenant.

    `identifier` is an email or an HR login id. The user must hold an HR role
    and be granted to the subdomain's client. Returns None if nothing matches
    (the caller still runs `dummy_verify` to keep timing constant).
    """
    from app.models import AuthCredential, User, UserClientAccess

    identifier = identifier.strip()
    user: User | None = None
    if "@" in identifier:
        email = identifier.lower()
        user = (
            db.execute(
                select(User)
                .join(UserClientAccess, UserClientAccess.user_id == User.id)
                .where(
                    User.email == email,
                    User.broker_firm_id == tenant.broker_firm_id,
                    User.role.in_(HR_ROLES),
                    UserClientAccess.client_id == tenant.client_id,
                )
            )
            .scalars()
            .first()
        )
    else:
        cred = db.execute(
            select(AuthCredential).where(
                AuthCredential.broker_firm_id == tenant.broker_firm_id,
                AuthCredential.hr_login_id == identifier.upper(),
            )
        ).scalar_one_or_none()
        if cred is not None:
            candidate = db.get(User, cred.user_id)
            if (
                candidate is not None
                and candidate.role in HR_ROLES
                and db.execute(
                    select(UserClientAccess.id).where(
                        UserClientAccess.user_id == candidate.id,
                        UserClientAccess.client_id == tenant.client_id,
                    )
                ).scalar_one_or_none()
                is not None
            ):
                user = candidate

    if user is None:
        return None
    cred = db.execute(
        select(AuthCredential).where(AuthCredential.user_id == user.id)
    ).scalar_one_or_none()
    if cred is None:
        return None
    return user, cred


# ── Tenant dependency (HR surface) ─────────────────────────────────────────────
def require_hr_tenant(
    request: Request,
    db: Session = Depends(get_db),
    x_inspro_tenant_slug: str | None = Header(default=None),
) -> TenantContext:
    """The HR surface's tenant, or 400.

    Normally the `{slug}.hr.<base>` subdomain. On a single-host deployment
    (`INSPRO_TENANT_MODE=header`) or in non-prod, an `X-Inspro-Tenant-Slug`
    header names the tenant instead — see `resolve_host_info`."""
    host_info = resolve_host_info(request, SURFACE_HR, x_inspro_tenant_slug)
    ctx = resolve_tenant_context(host_info, db)
    if ctx is None or ctx.surface != SURFACE_HR:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This request must be made on an HR subdomain.",
        )
    return ctx


def optional_hr_tenant(
    request: Request,
    db: Session = Depends(get_db),
    x_inspro_tenant_slug: str | None = Header(default=None),
) -> TenantContext | None:
    """The HR subdomain's tenant if present, else None (token cid governs).

    Like `require_hr_tenant` but non-fatal — used by `get_current_hr_user`,
    where a request without a subdomain (dev/localhost direct) is legitimate.
    Crucially it pins the header surface to HR, so resolution checks
    `hr_enabled` rather than `portal_enabled`.
    """
    return resolve_tenant_context(
        resolve_host_info(request, SURFACE_HR, x_inspro_tenant_slug), db
    )


# ── Refresh cookie ─────────────────────────────────────────────────────────────
def set_refresh_cookie(
    response: Response, token: str, expires_at: datetime, settings: Settings | None = None
) -> None:
    settings = settings or get_settings()
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.env != "dev",  # allow http on localhost only
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
        # No `domain` → host-only: an `acme.hr` cookie is never sent to `beta.hr`.
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, samesite="strict"
    )


# ── Authenticated dependency ───────────────────────────────────────────────────
def get_current_hr_user(
    request: Request,
    authorization: str | None = Header(default=None),
    tenant: TenantContext | None = Depends(optional_hr_tenant),
    db: Session = Depends(get_db),
) -> CurrentUser:
    from app.models import User
    from app.models.user import USER_STATUS_ACTIVE

    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = _decode_hr_access_token(token)
    except jwt.InvalidTokenError as exc:
        raise unauthorized from exc

    user = db.get(User, str(claims["sub"]))
    if user is None or user.role not in HR_ROLES or user.status != USER_STATUS_ACTIVE:
        raise unauthorized
    client_id = claims.get("cid")
    if not client_id:
        raise unauthorized
    # Token must belong to the tenant the request arrived on. When there's no
    # subdomain context (dev/localhost direct), the token's own cid governs.
    if tenant is not None and tenant.client_id != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown tenant.")

    set_search_path(db, user.broker_firm_id)
    return CurrentUser(
        user_id=user.id,
        broker_firm_id=user.broker_firm_id,
        client_id=client_id,
        role=user.role,  # type: ignore[arg-type]
        email=user.email,
    )
