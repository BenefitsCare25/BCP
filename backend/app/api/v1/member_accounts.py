"""Broker-side provisioning of employee-portal member accounts.

Runs inside the normal gated router loop (broker auth + tenant scoping).
Provisioning creates a control-plane `MemberAccount` from an Employee row
(email pulled from the roster via `EMAIL_KEYS`, overridable), stamps the
employee's `member_account_id`, and sends the OTP invite.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import passwords as PW
from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.breach_check import is_breached
from app.core.credentials import credential_version, next_rotation_deadline
from app.core.deps import (
    assert_policy_year_for_user,
    load_employee,
    require_client_id,
    user_owns,
)
from app.core.hr_auth import get_auth_policy
from app.core.portal_auth import (
    generate_member_login_id,
    issue_member_set_password_token,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import Employee, MemberAccount
from app.models.member_account import (
    MEMBER_STATUS_ACTIVE,
    MEMBER_STATUS_DISABLED,
    MEMBER_STATUS_INVITED,
)
from app.schemas.portal import (
    BulkInviteIn,
    BulkInviteResult,
    MemberAccountCreateIn,
    MemberAccountList,
    MemberAccountOut,
    MemberAccountPatch,
)
from app.services.member_otp import issue_otp, send_otp
from app.services.roster_attributes import EMAIL_KEYS, first_value

logger = logging.getLogger(__name__)

router = APIRouter(tags=["member-accounts"])


def _tenant_slug(db: Session, client_id: str | None) -> str | None:
    """The client's subdomain label, for building absolute portal links."""
    from app.models import Client

    client = db.get(Client, client_id) if client_id else None
    return client.slug if client else None


def _load_account(
    account_id: str, user: CurrentUser, db: Session
) -> MemberAccount:
    account = db.get(MemberAccount, account_id)
    if account is None or not user_owns(user, account.client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member account not found")
    return account


def _validated_email(raw: str) -> str:
    email = raw.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid email address."
        )
    return email


def _unique_member_login_id(db: Session, client_id: str) -> str:
    for _ in range(6):
        candidate = generate_member_login_id()
        exists = (
            db.query(MemberAccount.id)
            .filter(
                MemberAccount.client_id == client_id,
                MemberAccount.system_login_id == candidate,
            )
            .first()
        )
        if not exists:
            return candidate
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not allocate a member id."
    )


def _create_account(
    db: Session, employee: Employee, email: str | None, invited_by: str
) -> MemberAccount:
    account = MemberAccount(
        client_id=employee.client_id,
        email=email,
        staff_id=employee.staff_id,
        display_name=employee.employee_name,
        status=MEMBER_STATUS_INVITED,
        invited_by=invited_by,
        system_login_id=_unique_member_login_id(db, employee.client_id),
    )
    db.add(account)
    db.flush()
    employee.member_account_id = account.id
    return account


@router.get("/member-accounts", response_model=MemberAccountList)
def list_member_accounts(
    status_filter: str | None = Query(default=None, alias="status"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountList:
    client_id = require_client_id(user)
    conditions = [MemberAccount.client_id == client_id]
    if status_filter:
        conditions.append(MemberAccount.status == status_filter)
    total = db.scalar(select(func.count(MemberAccount.id)).where(*conditions)) or 0
    rows = db.execute(
        select(MemberAccount).where(*conditions).order_by(MemberAccount.staff_id)
    ).scalars().all()
    return MemberAccountList(
        total=total,
        items=[MemberAccountOut.model_validate(r) for r in rows],
    )


@router.post(
    "/employees/{employee_id}/member-account",
    response_model=MemberAccountOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
def create_member_account(
    request: Request,
    body: MemberAccountCreateIn,
    employee: Employee = Depends(load_employee),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountOut:
    raw_email = body.email or first_value(employee.attribute_values or {}, EMAIL_KEYS)
    if not raw_email:
        # Email-less employee: create the account with a system login id + a
        # set-password token (no OTP — they sign in with username + password).
        try:
            account = _create_account(db, employee, None, user.user_id)
            token = issue_member_set_password_token(account.id, credential_version(account))
            write_audit(
                db, user, "member_account.invited", "member_account", account.id,
                after={"staff_id": employee.staff_id, "emailless": True},
                employee_id=employee.id,
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A portal account already exists for this staff ID.",
            ) from None
        out = MemberAccountOut.model_validate(account)
        out.set_password_token = token
        out.tenant_slug = _tenant_slug(db, account.client_id)
        return out
    email = _validated_email(raw_email)

    try:
        account = _create_account(db, employee, email, user.user_id)
        issued = issue_otp(db, account)
        write_audit(
            db, user, "member_account.invited", "member_account", account.id,
            after={"email": email, "staff_id": employee.staff_id},
            employee_id=employee.id,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A portal account already exists for this email or staff ID.",
        ) from None
    mail_sent = send_otp(account, issued)
    out = MemberAccountOut.model_validate(account)
    out.mail_sent = mail_sent
    return out


@router.post("/member-accounts/{account_id}/resend-invite", response_model=MemberAccountOut)
@limiter.limit("10/minute")
def resend_invite(
    request: Request,
    account_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountOut:
    account = _load_account(account_id, user, db)
    if account.status == MEMBER_STATUS_DISABLED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Account is disabled — re-enable it first."
        )
    issued = issue_otp(db, account)
    write_audit(
        db, user, "member_account.invite_resent", "member_account", account.id,
        after={"email": account.email},
    )
    db.commit()
    mail_sent = send_otp(account, issued)
    out = MemberAccountOut.model_validate(account)
    out.mail_sent = mail_sent
    return out


class MemberSetPasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post(
    "/member-accounts/{account_id}/password-setup", response_model=MemberAccountOut
)
def member_password_setup(
    account_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountOut:
    """Allocate a system login id (if missing) and mint a single-use
    set-password token the member redeems on the portal. For members WITH an
    email you can send this link; for email-less members, hand it over."""
    account = _load_account(account_id, user, db)
    if account.status == MEMBER_STATUS_DISABLED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Account is disabled.")
    if not account.system_login_id:
        account.system_login_id = _unique_member_login_id(db, account.client_id)
    token = issue_member_set_password_token(account.id, credential_version(account))
    write_audit(db, user, "member_account.password_setup", "member_account", account.id)
    db.commit()
    out = MemberAccountOut.model_validate(account)
    out.set_password_token = token
    out.tenant_slug = _tenant_slug(db, account.client_id)
    return out


@router.post(
    "/member-accounts/{account_id}/set-password", response_model=MemberAccountOut
)
def member_set_password_direct(
    account_id: str,
    body: MemberSetPasswordIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountOut:
    """Broker sets a member's password directly (email-less members). The
    member changes it later from the portal if they wish."""
    account = _load_account(account_id, user, db)
    if account.status == MEMBER_STATUS_DISABLED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Account is disabled.")
    policy = get_auth_policy(db, account.client_id)
    ok, reason = PW.password_meets_policy(body.password, policy.password_min_entropy)
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
    # Same breach gate the member-facing portal set-password enforces, so the
    # broker can't seed a known-breached credential the member never could.
    if policy.breach_check_enabled and is_breached(body.password):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This password has appeared in a known data breach — choose another.",
        )
    from datetime import UTC, datetime

    if not account.system_login_id:
        account.system_login_id = _unique_member_login_id(db, account.client_id)
    account.password_hash = PW.hash_password(body.password)
    account.password_updated_at = datetime.now(UTC)
    account.must_rotate_after = next_rotation_deadline(
        policy.password_rotation_days, account.password_updated_at
    )
    account.failed_attempts = 0
    account.locked_until = None
    if account.status == MEMBER_STATUS_INVITED:
        account.status = MEMBER_STATUS_ACTIVE
    write_audit(db, user, "member_account.password_set", "member_account", account.id)
    db.commit()
    return MemberAccountOut.model_validate(account)


@router.post(
    "/member-accounts/{account_id}/regenerate-login-id", response_model=MemberAccountOut
)
def member_regenerate_login_id(
    account_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountOut:
    account = _load_account(account_id, user, db)
    account.system_login_id = _unique_member_login_id(db, account.client_id)
    write_audit(db, user, "member_account.login_id_regenerated", "member_account", account.id)
    db.commit()
    return MemberAccountOut.model_validate(account)


@router.patch("/member-accounts/{account_id}", response_model=MemberAccountOut)
def update_member_account(
    account_id: str,
    body: MemberAccountPatch,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountOut:
    if body.status not in (MEMBER_STATUS_ACTIVE, MEMBER_STATUS_DISABLED):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "status must be 'active' or 'disabled'.",
        )
    account = _load_account(account_id, user, db)
    before = account.status
    account.status = body.status
    write_audit(
        db, user, "member_account.status_changed", "member_account", account.id,
        before={"status": before}, after={"status": body.status},
    )
    db.commit()
    return MemberAccountOut.model_validate(account)


@router.post("/member-accounts/bulk-invite", response_model=BulkInviteResult)
@limiter.limit("10/minute")
def bulk_invite(
    request: Request,
    body: BulkInviteIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkInviteResult:
    """Provision every employee in the policy year that has a roster email and
    no portal account yet. Existing accounts (matched by email or staff_id)
    are skipped, not re-invited."""
    client_id = require_client_id(user)
    assert_policy_year_for_user(body.policy_year_id, user, db)

    accounts = db.execute(
        select(MemberAccount).where(MemberAccount.client_id == client_id)
    ).scalars().all()
    known_emails = {a.email for a in accounts}
    known_staff = {a.staff_id for a in accounts}

    employees = db.execute(
        select(Employee).where(
            Employee.client_id == client_id,
            Employee.policy_year_id == body.policy_year_id,
            Employee.status == "active",
        )
    ).scalars().all()

    invited = skipped_existing = skipped_no_email = 0
    to_send: list[tuple[MemberAccount, object]] = []
    for employee in employees:
        raw_email = first_value(employee.attribute_values or {}, EMAIL_KEYS)
        if not raw_email:
            skipped_no_email += 1
            continue
        email = raw_email.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            skipped_no_email += 1
            continue
        if email in known_emails or employee.staff_id in known_staff:
            skipped_existing += 1
            continue
        account = _create_account(db, employee, email, user.user_id)
        to_send.append((account, issue_otp(db, account)))
        known_emails.add(email)
        known_staff.add(employee.staff_id)
        invited += 1

    if invited:
        write_audit(
            db, user, "member_account.bulk_invited", "member_account", None,
            after={"policy_year_id": body.policy_year_id, "invited": invited},
        )
    db.commit()
    mail_failed = 0
    for account, issued in to_send:
        if not send_otp(account, issued):
            mail_failed += 1
    if mail_failed:
        logger.error(
            "Bulk invite: %d of %d invite emails failed to send for policy year %s",
            mail_failed, invited, body.policy_year_id,
        )
    return BulkInviteResult(
        invited=invited,
        skipped_existing=skipped_existing,
        skipped_no_email=skipped_no_email,
        mail_failed=mail_failed,
    )
