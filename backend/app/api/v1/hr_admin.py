"""Broker-side provisioning of HR credential accounts.

Registered INSIDE the broker router loop (`require_write_access`); every action
is `require_firm_admin`-gated. Creating an HR account provisions a `User`
(role `client_hr`/`client_admin`, status `invited`) + a `UserClientAccess`
grant to the client + an `AuthCredential` with a system-generated HR login id
and an unusable placeholder password. The HR admin sets their own password via
a single-use set-password token (returned here; emailed in production).
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import hr_auth as HR
from app.core import passwords as PW
from app.core import sessions as SESS
from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.deps import require_firm_admin
from app.db.session import get_db
from app.models import AuthCredential, Client, User, UserClientAccess
from app.models.auth import SUBJECT_USER
from app.models.user import (
    USER_STATUS_ACTIVE,
    USER_STATUS_DISABLED,
    USER_STATUS_INVITED,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hr-admin", tags=["hr-admin"])

_MAX_LOGIN_ID_TRIES = 6


def _load_firm_client(db: Session, user: CurrentUser, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    if user.role != "system_admin" and client.broker_firm_id != user.broker_firm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    return client


def _unique_login_id(db: Session, firm_id: str) -> str:
    for _ in range(_MAX_LOGIN_ID_TRIES):
        candidate = HR.generate_hr_login_id()
        exists = (
            db.query(AuthCredential.id)
            .filter(
                AuthCredential.broker_firm_id == firm_id,
                AuthCredential.hr_login_id == candidate,
            )
            .first()
        )
        if not exists:
            return candidate
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not allocate an HR id.")


def _load_hr_user(db: Session, actor: CurrentUser, user_id: str) -> tuple[User, AuthCredential]:
    target = db.get(User, user_id)
    cred = (
        db.query(AuthCredential).filter(AuthCredential.user_id == user_id).one_or_none()
        if target
        else None
    )
    if target is None or cred is None or target.role not in HR.HR_ROLES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "HR account not found")
    if actor.role != "system_admin" and target.broker_firm_id != actor.broker_firm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "HR account not found")
    return target, cred


# ── Schemas ────────────────────────────────────────────────────────────────────
class HrAccountCreate(BaseModel):
    client_id: str
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = None
    role: str = "client_hr"  # client_hr | client_admin


class HrAccountOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: str
    status: str
    client_id: str
    hr_login_id: str | None
    mfa_enrolled: bool
    last_login_at: datetime | None
    # The tenant's subdomain label. The set-password link lives on
    # `{tenant_slug}.hr.<base>`, NOT on the broker host the admin is using, so
    # the UI needs it to build an absolute (clickable, emailable) URL.
    tenant_slug: str | None = None


class HrAccountCreated(HrAccountOut):
    # Returned once, on create / reset — deliver to the HR admin out-of-band
    # (emailed set-password link) in production.
    set_password_token: str


class AuthPolicyOut(BaseModel):
    client_id: str
    mfa_hr_enabled: bool
    mfa_portal_enabled: bool
    hr_login_source: str
    portal_login_source: str
    password_min_entropy: int
    password_rotation_days: int | None
    session_idle_minutes: int
    session_absolute_hours: int
    breach_check_enabled: bool


_LOGIN_SOURCES = frozenset({"email", "system_id", "staff_id"})


class AuthPolicyPatch(BaseModel):
    mfa_hr_enabled: bool | None = None
    mfa_portal_enabled: bool | None = None
    hr_login_source: str | None = None
    portal_login_source: str | None = None
    password_min_entropy: int | None = Field(default=None, ge=20, le=256)
    password_rotation_days: int | None = None
    session_idle_minutes: int | None = Field(default=None, ge=5, le=1440)
    session_absolute_hours: int | None = Field(default=None, ge=1, le=168)
    breach_check_enabled: bool | None = None

    @field_validator("hr_login_source", "portal_login_source")
    @classmethod
    def _valid_source(cls, v: str | None) -> str | None:
        if v is not None and v not in _LOGIN_SOURCES:
            raise ValueError(f"Invalid login source: {v}")
        return v


def _account_out(db: Session, user: User, cred: AuthCredential, client_id: str) -> HrAccountOut:
    client = db.get(Client, client_id) if client_id else None
    return HrAccountOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        client_id=client_id,
        hr_login_id=cred.hr_login_id,
        mfa_enrolled=HR.user_has_confirmed_mfa(db, user.id),
        last_login_at=cred.last_login_at,
        tenant_slug=client.slug if client else None,
    )


def _client_id_for(db: Session, user_id: str) -> str | None:
    return (
        db.query(UserClientAccess.client_id)
        .filter(UserClientAccess.user_id == user_id)
        .scalar()
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────
@router.post("/accounts", response_model=HrAccountCreated, status_code=201)
def create_account(
    body: HrAccountCreate,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> HrAccountCreated:
    if body.role not in HR.HR_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid HR role.")
    client = _load_firm_client(db, user, body.client_id)
    firm_id = client.broker_firm_id
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid email address.")
    if db.query(User).filter(User.email == email).one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email is already registered on the platform."
        )

    new_user = User(
        external_id=None,
        email=email,
        display_name=(body.display_name or "").strip() or None,
        broker_firm_id=firm_id,
        role=body.role,
        status=USER_STATUS_INVITED,
    )
    db.add(new_user)
    db.flush()
    db.add(UserClientAccess(user_id=new_user.id, client_id=client.id))
    cred = AuthCredential(
        user_id=new_user.id,
        broker_firm_id=firm_id,
        hr_login_id=_unique_login_id(db, firm_id),
        # Unusable placeholder — a valid Argon2id hash of a random secret nobody
        # holds, so login fails until the HR admin sets their own password.
        password_hash=PW.hash_password(secrets.token_urlsafe(32)),
        password_updated_at=datetime.now(UTC),
    )
    db.add(cred)
    db.flush()
    token = HR.issue_set_password_token(new_user.id, HR.credential_version(cred))
    write_audit(
        db, user, action="create", entity_type="hr_account", entity_id=new_user.id,
        after={"email": email, "role": body.role, "client_id": client.id},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email is already registered on the platform."
        ) from exc
    out = _account_out(db, new_user, cred, client.id)
    return HrAccountCreated(**out.model_dump(), set_password_token=token)


@router.get("/accounts", response_model=list[HrAccountOut])
def list_accounts(
    client_id: str = Query(...),
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> list[HrAccountOut]:
    client = _load_firm_client(db, user, client_id)
    rows = (
        db.query(User, AuthCredential)
        .join(AuthCredential, AuthCredential.user_id == User.id)
        .join(UserClientAccess, UserClientAccess.user_id == User.id)
        .filter(
            UserClientAccess.client_id == client.id,
            User.role.in_(HR.HR_ROLES),
        )
        .order_by(User.email)
        .all()
    )
    return [_account_out(db, u, c, client.id) for u, c in rows]


@router.post("/accounts/{user_id}/regenerate-login-id", response_model=HrAccountOut)
def regenerate_login_id(
    user_id: str,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> HrAccountOut:
    target, cred = _load_hr_user(db, user, user_id)
    cred.hr_login_id = _unique_login_id(db, cred.broker_firm_id)
    write_audit(db, user, action="update", entity_type="hr_account", entity_id=target.id,
                after={"hr_login_id": cred.hr_login_id})
    db.commit()
    return _account_out(db, target, cred, _client_id_for(db, target.id) or "")


@router.post("/accounts/{user_id}/reset-password", response_model=HrAccountCreated)
def reset_password(
    user_id: str,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> HrAccountCreated:
    target, cred = _load_hr_user(db, user, user_id)
    token = HR.issue_set_password_token(target.id, HR.credential_version(cred))
    # An admin reset is a containment action — evict live sessions NOW rather
    # than whenever the user happens to redeem the link, otherwise an attacker
    # already signed in keeps their session for its full absolute lifetime.
    revoked = SESS.revoke_all_for_subject(db, SUBJECT_USER, target.id)
    write_audit(db, user, action="reset_password", entity_type="hr_account",
                entity_id=target.id, after={"sessions_revoked": revoked})
    db.commit()
    out = _account_out(db, target, cred, _client_id_for(db, target.id) or "")
    return HrAccountCreated(**out.model_dump(), set_password_token=token)


@router.post("/accounts/{user_id}/disable", response_model=HrAccountOut)
def disable_account(
    user_id: str,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> HrAccountOut:
    target, cred = _load_hr_user(db, user, user_id)
    target.status = USER_STATUS_DISABLED
    # Kill all live sessions so a disable takes effect immediately.
    SESS.revoke_all_for_subject(db, SUBJECT_USER, target.id)
    write_audit(db, user, action="disable", entity_type="hr_account", entity_id=target.id)
    db.commit()
    return _account_out(db, target, cred, _client_id_for(db, target.id) or "")


@router.post("/accounts/{user_id}/enable", response_model=HrAccountOut)
def enable_account(
    user_id: str,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> HrAccountOut:
    target, cred = _load_hr_user(db, user, user_id)
    # An invited account that never set a password returns to invited, not active.
    target.status = (
        USER_STATUS_ACTIVE
        if cred.last_login_at is not None
        else USER_STATUS_INVITED
    )
    write_audit(db, user, action="enable", entity_type="hr_account", entity_id=target.id)
    db.commit()
    return _account_out(db, target, cred, _client_id_for(db, target.id) or "")


# ── Per-tenant auth policy ─────────────────────────────────────────────────────
@router.get("/clients/{client_id}/auth-policy", response_model=AuthPolicyOut)
def get_policy(
    client_id: str,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> AuthPolicyOut:
    _load_firm_client(db, user, client_id)
    p = HR.get_auth_policy(db, client_id)
    return AuthPolicyOut(client_id=client_id, **p.__dict__)


@router.put("/clients/{client_id}/auth-policy", response_model=AuthPolicyOut)
def put_policy(
    client_id: str,
    body: AuthPolicyPatch,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> AuthPolicyOut:
    from app.models import ClientAuthPolicy

    _load_firm_client(db, user, client_id)
    row = db.get(ClientAuthPolicy, client_id)
    if row is None:
        row = ClientAuthPolicy(client_id=client_id)
        db.add(row)
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(row, field, value)
    write_audit(db, user, action="update", entity_type="client_auth_policy",
                entity_id=client_id, after=changes)
    db.commit()
    p = HR.get_auth_policy(db, client_id)
    return AuthPolicyOut(client_id=client_id, **p.__dict__)
