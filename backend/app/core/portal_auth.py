"""Employee-portal auth seam — a SEPARATE principal type, not a broker role.

Members (insured employees of a client company) authenticate with an email
OTP and receive an HS256 JWT signed with `INSPRO_PORTAL_JWT_SECRET` carrying
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

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.session import get_db
from app.db.tenancy import set_search_path

logger = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"
_TOKEN_TYPE_MEMBER = "member"

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class CurrentMember:
    member_account_id: str
    client_id: str
    broker_firm_id: str | None
    email: str
    staff_id: str
    display_name: str | None = None


def hash_otp_code(code: str) -> str:
    """Keyed hash of an OTP code — a leaked `member_otp_codes` table alone
    can't be brute-forced offline without the app secret."""
    secret = get_settings().portal_jwt_secret.encode()
    return hmac.new(secret, code.encode(), hashlib.sha256).hexdigest()


def issue_member_token(member_account_id: str, client_id: str) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=settings.portal_token_ttl_hours)
    token = jwt.encode(
        {
            "sub": member_account_id,
            "client_id": client_id,
            "typ": _TOKEN_TYPE_MEMBER,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        settings.portal_jwt_secret,
        algorithm=_JWT_ALGORITHM,
    )
    return token, expires_at


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


def get_current_member(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CurrentMember:
    from app.models import Client, MemberAccount  # lazy: avoid import cost at module load
    from app.models.member_account import MEMBER_STATUS_ACTIVE

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

    client = db.get(Client, account.client_id)
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


def active_policy_year(db: Session, client_id: str):
    """The client's current active policy year (latest start when several)."""
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


def resolve_member_employee(db: Session, member: CurrentMember):
    """The member's own Employee row in the active policy year.

    Prefers the stamped `member_account_id` binding; falls back to a
    `(policy_year_id, staff_id)` match (new policy year rosters arrive without
    the stamp) and stamps it for next time. 404 when the client has no active
    year or the member has no row in it.
    """
    from app.models import Employee

    year = active_policy_year(db, member.client_id)
    if year is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active coverage")

    employee = (
        db.query(Employee)
        .filter(
            Employee.policy_year_id == year.id,
            Employee.member_account_id == member.member_account_id,
        )
        .one_or_none()
    )
    if employee is None:
        candidates = (
            db.query(Employee)
            .filter(
                Employee.policy_year_id == year.id,
                Employee.staff_id == member.staff_id,
                Employee.member_account_id.is_(None),
            )
            .all()
        )
        if len(candidates) > 1:
            logger.warning(
                "Ambiguous staff_id %s for member %s in policy year %s",
                member.staff_id, member.member_account_id, year.id,
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Multiple roster rows match your staff ID — contact your broker.",
            )
        if candidates:
            employee = candidates[0]
            employee.member_account_id = member.member_account_id
            db.commit()
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active coverage")
    return employee
