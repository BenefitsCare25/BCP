"""Employee-portal auth seam — a SEPARATE principal type, not a broker role.

Members (insured employees of a client company) authenticate with a username
and password (plus a TOTP step when their company enables portal 2FA) and
receive an HS256 JWT signed with `INSPRO_PORTAL_JWT_SECRET` carrying
`typ: "member"`. Broker Entra tokens are RS256 against Entra's JWKS, so
neither surface's tokens verify on the other — cryptographic separation.

Portal routers depend on `get_current_member` and are registered in `main.py`
OUTSIDE the broker `require_write_access` gate. A member is hard-pinned to
exactly one client (from the token) — the `X-Inspro-Client` header is ignored
on portal routes.

Every portal endpoint must scope data through `resolve_member_employee` — the
member's own Employee row in the active policy year — never by bare client_id
(which would expose co-workers' data).
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
from sqlalchemy.orm import Session

from app.core import credentials as CRED
from app.core.settings import Settings, get_settings
from app.core.tenancy_host import (
    SURFACE_PORTAL,
    TenantContext,
    resolve_host_info,
    resolve_tenant_context,
)
from app.core.tenant_resolution import refuse_unserved_surface, request_firm
from app.db.session import get_db
from app.db.tenancy import set_search_path
from app.models import Employee, MemberAccount, PolicyYear
from app.models.platform import DOMAIN_SURFACE_CLIENT
from app.services.member_access import (
    UNSET as _UNSET,  # "year not supplied", distinct from "no active year"
)
from app.services.member_access import (
    Capability,
    access_of,
    locate_employee,
    refusal,
)

logger = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"
_TOKEN_TYPE_MEMBER = "member"


@dataclass(frozen=True)
class CurrentMember:
    member_account_id: str
    client_id: str
    broker_firm_id: str | None
    email: str | None
    staff_id: str
    display_name: str | None = None


def issue_member_token(
    member_account_id: str, client_id: str, credential_version: int = 0,
    *, session_id: str | None = None,
) -> tuple[str, datetime]:
    """Mint a member session token.

    `credential_version` stamps the password generation this token belongs to
    (`credentials.credential_version`). Member tokens are stateless — there is no
    `auth_sessions` row to revoke — so this claim is the only way a password
    change can evict tokens issued before it. `get_current_member` compares it
    against the account's current value on every request.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=10)
    if session_id is None:
        # Trusted internal issuers also create a revocable session.
        from app.core import sessions as SESS
        from app.core.hr_auth import get_auth_policy
        from app.db.session import SessionLocal
        from app.models.auth import SUBJECT_MEMBER

        with SessionLocal() as db:
            issued = SESS.issue_session(
                db, subject_type=SUBJECT_MEMBER, subject_id=member_account_id,
                client_id=client_id, broker_firm_id=None,
                absolute_hours=get_auth_policy(db, client_id).session_absolute_hours,
            )
            session_id = issued.session_id
            db.commit()
    token = jwt.encode(
        {
            "sub": member_account_id,
            "client_id": client_id,
            "typ": _TOKEN_TYPE_MEMBER,
            "cv": credential_version,
            "sid": session_id,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        settings.portal_jwt_secret,
        algorithm=_JWT_ALGORITHM,
    )
    return token, expires_at


_TOKEN_TYPE_SET_PW = "member_set_pw"
_TOKEN_TYPE_MFA = "member_mfa"
_SET_PW_LABEL = b"inspro-member-set-password-v1"
_MFA_LABEL = b"inspro-member-mfa-challenge-v1"
SET_PW_TTL_HOURS = 72
MFA_CHALLENGE_TTL_MINUTES = 5
_ID_ALPHABET = string.ascii_uppercase + string.digits


def _derive_key(label: bytes, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return hmac.new(settings.portal_jwt_secret.encode(), label, "sha256").hexdigest()


def generate_member_login_id() -> str:
    """Opaque, non-guessable member login id (e.g. "EM-7Q2M8K"). NEVER an NRIC."""
    body = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"EM-{body}"


def issue_member_set_password_token(
    member_account_id: str, version: int, *, issued_at: datetime | None = None,
) -> str:
    """The bare token. Links a person is given come from
    `issue_member_set_password_link`, which also cancels earlier links."""
    now = issued_at or datetime.now(UTC)
    return jwt.encode(
        {
            "sub": member_account_id,
            "v": version,
            "typ": _TOKEN_TYPE_SET_PW,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=SET_PW_TTL_HOURS)).timestamp()),
        },
        _derive_key(_SET_PW_LABEL),
        algorithm=_JWT_ALGORITHM,
    )


def issue_member_set_password_link(account: MemberAccount) -> str:
    """Mint the token for a NEW set-password link and cancel every earlier one.

    Stamps `password_token_issued_at` with the token's issue time, so links
    issued before it are refused. Every path that hands a member a link uses
    this rather than the bare issuer. Caller commits.
    """
    return issue_member_set_password_token(
        account.id, CRED.credential_version(account),
        issued_at=CRED.stamp_password_token(account),
    )


def verify_member_set_password_token(token: str) -> CRED.SetPasswordClaim:
    claims = jwt.decode(
        token,
        _derive_key(_SET_PW_LABEL),
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    if claims.get("typ") != _TOKEN_TYPE_SET_PW:
        raise jwt.InvalidTokenError("wrong token type")
    return CRED.SetPasswordClaim(
        str(claims["sub"]), int(claims.get("v", 0)), int(claims.get("iat", 0)),
    )


def issue_member_mfa_challenge_token(member_account_id: str, client_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": member_account_id,
            "cid": client_id,
            "typ": _TOKEN_TYPE_MFA,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=MFA_CHALLENGE_TTL_MINUTES)).timestamp()),
        },
        _derive_key(_MFA_LABEL),
        algorithm=_JWT_ALGORITHM,
    )


def verify_member_mfa_challenge_token(token: str) -> tuple[str, str]:
    claims = jwt.decode(
        token,
        _derive_key(_MFA_LABEL),
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    if claims.get("typ") != _TOKEN_TYPE_MFA:
        raise jwt.InvalidTokenError("wrong token type")
    return str(claims["sub"]), str(claims.get("cid", ""))


def resolve_member_credential(
    db: Session, client_id: str, identifier: str
) -> MemberAccount | None:
    """Locate a member account by username WITHIN a client, accepting any of the
    three identifier forms (email / system id / staff id) — the broker's chosen
    source drives the UI label, but a member can present whichever they have."""
    from app.models import MemberAccount
    from app.models.member_account import MEMBER_STATUS_DISABLED

    identifier = identifier.strip()
    base = db.query(MemberAccount).filter(
        MemberAccount.client_id == client_id,
        MemberAccount.status != MEMBER_STATUS_DISABLED,
    )
    if "@" in identifier:
        return base.filter(MemberAccount.email == identifier.lower()).one_or_none()
    account = base.filter(MemberAccount.system_login_id == identifier.upper()).one_or_none()
    if account is not None:
        return account
    return base.filter(MemberAccount.staff_id == identifier).one_or_none()


def require_portal_tenant(
    request: Request,
    db: Session = Depends(get_db),
    x_inspro_tenant_slug: str | None = Header(default=None),
) -> TenantContext:
    """The portal surface's tenant, or 400.

    Normally the `{slug}.portal.<base>` subdomain; a single-host deployment
    (`INSPRO_TENANT_MODE=header`) or non-prod accepts an `X-Inspro-Tenant-Slug`
    header stand-in (same as the HR surface). The alias is looked up within
    the broker firm the host belongs to."""
    firm = request_firm(request)
    refuse_unserved_surface(firm, DOMAIN_SURFACE_CLIENT)
    host_info = resolve_host_info(request, SURFACE_PORTAL, x_inspro_tenant_slug)
    ctx = resolve_tenant_context(host_info, db, firm)
    if ctx is None or ctx.surface != SURFACE_PORTAL:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This request must be made on a company portal subdomain.",
        )
    # **Route the schema HERE, not only in `get_current_member`.** The sign-in
    # endpoints have no member token yet, so the authenticated dep has not run —
    # and until the leaver gate they touched only CONTROL tables, which live in
    # `public` on every deployment, so nothing noticed the omission.
    #
    # `_issue_member_login` now reads `policy_years`, `employees` and `claims`,
    # all TENANT tables. Unrouted, every one of those resolves against `public`,
    # which holds no tenant rows on Postgres: the access check would come back
    # `unknown` and silently sign in every member whose access had ended. The
    # SQLite suite cannot catch that (no schemas) — see
    # `tests/test_schema_isolation_pg.py`.
    set_search_path(db, ctx.broker_firm_id)
    return ctx


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


def get_current_member(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CurrentMember:
    from app.models import Client, MemberAccount  # lazy: avoid import cost at module load
    from app.models.member_account import MEMBER_STATUS_ACTIVE

    firm = request_firm(request)
    refuse_unserved_surface(firm, DOMAIN_SURFACE_CLIENT)
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.portal_jwt_secret,
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Invalid portal token") from exc
    if claims.get("typ") != _TOKEN_TYPE_MEMBER:
        raise _unauthorized("Invalid portal token")

    account = db.get(MemberAccount, str(claims["sub"]))
    if account is None or account.status != MEMBER_STATUS_ACTIVE:
        raise _unauthorized("Member account is not active")
    if account.client_id != claims.get("client_id"):
        # Token minted before the account moved clients — force re-auth.
        raise _unauthorized("Invalid portal token")
    # Password change evicts older tokens. Without this a member token stayed
    # valid for its full TTL after a reset, so resetting a phished password gave
    # the member no containment at all.
    if int(claims.get("cv") or 0) != CRED.credential_version(account):
        raise _unauthorized("Session ended — sign in again")
    client = db.get(Client, account.client_id)
    # The member's company belongs to one broker; another broker's host does not
    # accept the session. Checked before the session is touched.
    if firm is not None and (client is None or client.broker_firm_id != firm.firm_id):
        raise _unauthorized("Invalid portal token")
    from app.core import sessions as SESS
    from app.core.hr_auth import get_auth_policy
    from app.models.auth import SUBJECT_MEMBER

    policy = get_auth_policy(db, account.client_id)
    session = SESS.validate_access_session(
        db, str(claims.get("sid", "")), subject_type=SUBJECT_MEMBER,
        subject_id=account.id, client_id=account.client_id,
        idle_minutes=policy.session_idle_minutes,
        touch_activity=request.headers.get("X-Inspro-Session-Activity") != "passive"
        and request.url.path != "/api/v1/portal/enrollment/notices"
        and not request.url.path.startswith("/api/v1/portal/enrollment/state/"),
    )
    if client is not None and not client.portal_enabled:
        # Per request, not only at sign-in: switching the portal off has to stop
        # what live sessions can do, not just new sign-ins.
        raise HTTPException(status.HTTP_403_FORBIDDEN, {
            "code": "portal_disabled",
            "message": "Employee portal access for this company is switched off.",
        })
    if policy.mfa_portal_required and not session.mfa_verified:
        from app.core import mfa as MFA

        if MFA.has_confirmed(db, SUBJECT_MEMBER, account.id):
            SESS.revoke_family(db, session.family_id)
            db.commit()
            raise _unauthorized("Sign in again to verify two-factor authentication.")
        allowed = {
            "/api/v1/portal/auth/security-status", "/api/v1/portal/auth/mfa/enroll/start",
            "/api/v1/portal/auth/mfa/enroll/confirm",
        }
        if request.url.path not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, {
                "code": "mfa_enrollment_required",
                "message": "Complete two-factor setup before continuing.",
            })

    broker_firm_id = client.broker_firm_id if client else None
    set_search_path(db, broker_firm_id)
    return CurrentMember(
        member_account_id=account.id,
        client_id=account.client_id,
        broker_firm_id=broker_firm_id,
        email=account.email,
        staff_id=account.staff_id,
        display_name=account.display_name,
    )


REFRESH_COOKIE_NAME = "inspro_portal_refresh"
REFRESH_COOKIE_PATH = "/api/v1/portal/auth"


def refresh_cookie_name(client_id: str | None = None) -> str:
    if get_settings().tenant_mode == "header" and client_id:
        return f"{REFRESH_COOKIE_NAME}_{client_id}"
    return REFRESH_COOKIE_NAME


def set_refresh_cookie(
    response: Response, token: str, expires_at: datetime, client_id: str,
) -> None:
    response.set_cookie(
        refresh_cookie_name(client_id), token,
        max_age=max(0, int((expires_at - datetime.now(UTC)).total_seconds())),
        httponly=True, secure=get_settings().env != "dev", samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: Response, client_id: str | None = None) -> None:
    response.delete_cookie(
        refresh_cookie_name(client_id), path=REFRESH_COOKIE_PATH, samesite="strict",
    )


def active_policy_year(db: Session, client_id: str) -> PolicyYear | None:
    """The one explicitly live benefit year for a company.

    Coverage dates describe what the policy covers; they are not an activation
    mechanism. A copied or incomplete draft must never become member-visible
    merely because the calendar crossed its start date.
    """
    from app.models import PolicyYear
    from app.models.policy_year import PolicyYearStatus

    return (
        db.query(PolicyYear)
        .filter(
            PolicyYear.client_id == client_id,
            PolicyYear.status == PolicyYearStatus.active,
        )
        .order_by(PolicyYear.start_date.desc())
        .first()
    )


def resolve_member_employee(
    db: Session,
    member: CurrentMember,
    *,
    requires: Capability | None = Capability.RECORD,
    year: Any = _UNSET,
) -> Employee:
    """The member's own Employee row in the active policy year.

    Prefers the stamped `member_account_id` binding; falls back to a
    `(policy_year_id, staff_id)` match (new policy year rosters arrive without
    the stamp) and stamps it for next time. 404 when the client has no active
    year or the member has no row in it.

    **`requires` is the leaver gate, and this is deliberately where it lives.**
    Every portal handler already has to call this function, and that rule is
    already documented as mandatory — so bolting the check on here is the one
    seam that cannot be forgotten, unlike a dependency someone omits from a new
    router. It defaults to `Capability.RECORD`; `requires=None` opts out
    entirely and exists for `GET /portal/me`, which has to keep answering for a
    member whose access has ended so the shell can say why.

    A denied capability is a 403 carrying a `code` (`access_ended` when the
    member is finished with, `coverage_ended` while they are still in run-off) —
    never a 404, which would say the record does not exist when it does.

    `year` lets a caller that has ALREADY resolved the active year hand it over
    rather than have it looked up again. It takes a sentinel default, not
    `None`, because `None` is a real answer here (the client has no active year)
    and the two must stay distinguishable.
    """
    year = active_policy_year(db, member.client_id) if year is _UNSET else year
    if year is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active coverage")

    # The binding rule itself lives in `member_access.locate_employee` — ONE
    # spelling, shared with the access check, which must agree with this about
    # which person a request is about. What is local to the data path is what
    # happens either side of it: stamping the match, and refusing ambiguity.
    employee, ambiguous = locate_employee(
        db,
        policy_year_id=year.id,
        member_account_id=member.member_account_id,
        staff_id=member.staff_id,
    )
    if ambiguous:
        logger.warning(
            "Ambiguous staff_id %s for member %s in policy year %s",
            member.staff_id,
            member.member_account_id,
            year.id,
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Multiple roster rows match your staff ID — contact your broker.",
        )
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active coverage")
    if employee.member_account_id != member.member_account_id:
        employee.member_account_id = member.member_account_id
        db.commit()
    if requires is not None:
        assert_member_capability(db, employee, requires, year=year)
    return employee


def assert_member_capability(
    db: Session,
    employee: Employee,
    capability: Capability,
    *,
    year: Any = None,
) -> None:
    """403 unless this member still holds `capability`. The ONE raise site.

    Almost every caller reaches this through `resolve_member_employee`'s
    `requires=`. It is exposed for the one place a route's capability depends on
    the ROW it is acting on rather than on the route: submitting a claim is
    answering a `needs_info` (`RESPOND`) or finishing a new one (`CLAIM`), and
    which it is can only be known once the claim is loaded.
    """
    if year is None:
        year = active_policy_year(db, employee.client_id)
    denied = refusal(access_of(db, employee, year), capability)
    if denied is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, denied)
