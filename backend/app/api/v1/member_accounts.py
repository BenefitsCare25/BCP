"""Broker-side provisioning of employee-portal member accounts.

Runs inside the normal gated router loop (broker auth + tenant scoping).
Provisioning creates a control-plane `MemberAccount` from an Employee row
(email pulled from the roster via `EMAIL_KEYS`, overridable), stamps the
employee's `member_account_id`, and sends the OTP invite.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import auth_events as EV
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
    SET_PW_TTL_HOURS,
    generate_member_login_id,
    issue_member_set_password_token,
)
from app.core.rate_limit import limiter
from app.core.request_context import client_ip, user_agent
from app.core.settings import get_settings
from app.db.session import SessionLocal, get_db
from app.models import Client, Employee, MemberAccount
from app.models.auth import SUBJECT_MEMBER
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
    PortalRolloutMember,
    PortalRolloutOut,
)
from app.services.member_invite import (
    clear_invite_expiry,
    issue_invite_credential,
    login_username,
    mail_deliverable,
    restore_credential,
    send_member_invite,
    snapshot_credential,
)
from app.services.roster_attributes import EMAIL_KEYS, first_value

logger = logging.getLogger(__name__)

router = APIRouter(tags=["member-accounts"])


def _tenant_slug(db: Session, client_id: str | None) -> str | None:
    """The client's subdomain label, for building absolute portal links."""
    from app.models import Client

    client = db.get(Client, client_id) if client_id else None
    return client.slug if client else None


def _broker_firm_for(db: Session, client_id: str | None) -> str | None:
    """The firm owning a company. `MemberAccount` has no firm of its own."""
    client = db.get(Client, client_id) if client_id else None
    return client.broker_firm_id if client else None


def _login_source(db: Session, client_id: str | None) -> str | None:
    """The company's `portal_login_source` — what employees sign in with."""
    if not client_id:
        return None
    return get_auth_policy(db, client_id).portal_login_source


def _account_out(db: Session, account: MemberAccount) -> MemberAccountOut:
    """Serialize an account WITH its resolved sign-in username.

    Every single-account response goes through here, so a freshly created
    account states the same username as the list refetch behind it.
    """
    out = MemberAccountOut.model_validate(account)
    out.login_username = login_username(account, _login_source(db, account.client_id))
    return out


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
    # One lookup for the whole list: the UI prints the member's own sign-in URL,
    # which MUST carry the tenant. Without it the portal can't resolve the
    # company and the member is told their details weren't recognised —
    # indistinguishable from a wrong password.
    slug = _tenant_slug(db, client_id)
    source = _login_source(db, client_id)
    items = []
    for row in rows:
        out = MemberAccountOut.model_validate(row)
        out.tenant_slug = slug
        out.login_username = login_username(row, source)
        items.append(out)
    return MemberAccountList(
        total=total,
        items=items,
        password_min_length=PW.MIN_LENGTH,
        set_password_ttl_hours=SET_PW_TTL_HOURS,
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
        out = _account_out(db, account)
        out.set_password_token = token
        out.tenant_slug = _tenant_slug(db, account.client_id)
        return out
    email = _validated_email(raw_email)

    try:
        account = _create_account(db, employee, email, user.user_id)
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
    mail_sent = _issue_and_send_invite(db, account)
    out = _account_out(db, account)
    out.mail_sent = mail_sent
    return out


def _issue_and_send_invite(db: Session, account: MemberAccount) -> bool:
    """Mint a one-time password, mail it, and record delivery. Returns whether
    the mailer accepted it.

    The single-account path (per-employee invite / resend). The credential is
    committed before the send so it is live when the mail lands, and rolled back
    if the send fails — an account is never left holding a password that was
    never delivered, and `invite_sent_at` is stamped only on real delivery, so a
    failure keeps the member in the bulk send's target set.
    """
    policy = get_auth_policy(db, account.client_id)
    prior = snapshot_credential(account)
    password = issue_invite_credential(account, policy.password_min_entropy)
    db.commit()
    sent = send_member_invite(
        account,
        password,
        _tenant_slug(db, account.client_id),
        _login_source(db, account.client_id),
    )
    if sent:
        account.invite_sent_at = datetime.now(UTC)
    else:
        restore_credential(account, prior)
    db.commit()
    return sent


@router.post("/member-accounts/{account_id}/resend-invite", response_model=MemberAccountOut)
@limiter.limit("10/minute")
def resend_invite(
    request: Request,
    account_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberAccountOut:
    """Re-issue this member's invite: a NEW one-time password, mailed to them.

    Deliberately a per-employee action. Any existing password stops working, so
    on an already-onboarded member this is a password reset — the UI confirms it
    as one. The bulk send can never do this: it only targets members with no
    delivered invite, which is what keeps a rollout from mailing anyone twice.
    """
    account = _load_account(account_id, user, db)
    if account.status == MEMBER_STATUS_DISABLED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Account is disabled — re-enable it first."
        )
    if not account.email:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No email address on file — use a set-password link instead.",
        )
    write_audit(
        db, user, "member_account.invite_resent", "member_account", account.id,
        after={"email": account.email},
    )
    mail_sent = _issue_and_send_invite(db, account)
    out = _account_out(db, account)
    out.mail_sent = mail_sent
    return out


class MemberSetPasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post(
    "/member-accounts/{account_id}/password-setup", response_model=MemberAccountOut
)
def member_password_setup(
    request: Request,
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
    # Broker-initiated: the security trail records the MEMBER as subject and the
    # staff member who asked for it in `detail`, so a reset can be traced to a
    # person. `write_audit` below is the broker-facing log, a separate record.
    EV.write_auth_event(
        db, event_type=EV.EVENT_PASSWORD_RESET_REQUEST, outcome=EV.OUTCOME_SUCCESS,
        surface="portal", subject_type=SUBJECT_MEMBER, subject_id=account.id,
        client_id=account.client_id,
        # The company's OWN firm, not the actor's — `_load_account` lets a
        # `system_admin` act across firms, and an event filed under the actor's
        # firm is invisible to the firm whose member was actually reset.
        broker_firm_id=_broker_firm_for(db, account.client_id),
        ip=client_ip(request), user_agent=user_agent(request),
        subdomain=request.headers.get("host"),
        detail={"reason": "broker_link", "actor_user_id": user.user_id},
    )
    write_audit(db, user, "member_account.password_setup", "member_account", account.id)
    db.commit()
    out = _account_out(db, account)
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
    # A real password supersedes any mailed one-time value, so its deadline no
    # longer applies — leaving it set would expire the password just chosen.
    clear_invite_expiry(account)
    if account.status == MEMBER_STATUS_INVITED:
        account.status = MEMBER_STATUS_ACTIVE
    write_audit(db, user, "member_account.password_set", "member_account", account.id)
    db.commit()
    return _account_out(db, account)


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
    return _account_out(db, account)


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
    return _account_out(db, account)


# ── Portal rollout: one classification, shared by the count and the send ─────
#
# The number on the button and the set the endpoint acts on MUST come from the
# same function. Two implementations drift, and the failure is silent: the card
# offers "Send invites to 412 employees" and 380 go out, with nothing to say
# which 32 were dropped or why.

_BUCKET_PENDING = "pending"      # has an email, no invite delivered yet → target
_BUCKET_INVITED = "invited"      # invite delivered, not signed in
_BUCKET_SIGNED_IN = "signed_in"  # onboarded
_BUCKET_NO_EMAIL = "no_email"    # nowhere to send
_BUCKET_DUPLICATE = "duplicate"  # its email/staff id belongs to another employee
_BUCKET_DISABLED = "disabled"

# The follow-up list is rendered in full, so it is capped rather than unbounded.
ATTENTION_LIST_LIMIT = 500

# Clients with a delivery run in flight.
#
# Delivery is slow by design (Argon2id per member), so for a minute or two after
# pressing send the counts have not moved yet — which reads exactly like nothing
# happened and invites a second press. Without this, that second press re-queues
# members whose first invite is mid-flight but not yet stamped, and mails them
# twice: the one outcome this feature must never produce.
#
# In-process, so it bounds a double-press on ONE instance rather than across a
# scaled-out deployment. That is the realistic failure (one operator, one page,
# two clicks); the per-account `invite_sent_at` check inside the run still
# closes the window for anything already delivered.
_SENDING: set[str] = set()
_SENDING_LOCK = threading.Lock()


@dataclass
class _RosterEntry:
    employee: Employee
    account: MemberAccount | None
    email: str | None
    # True when provisioning this employee would collide with another employee
    # on the same roster (a shared email address, or a repeated staff id).
    duplicate: bool = False


def _roster_email(employee: Employee) -> str | None:
    """The employee's roster email, or None when it is absent or unusable."""
    raw = first_value(employee.attribute_values or {}, EMAIL_KEYS)
    if not raw:
        return None
    email = raw.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        return None
    return email


def _roster_entries(
    db: Session, client_id: str, policy_year_id: str
) -> list[_RosterEntry]:
    """Every active employee of the year, joined to its portal account (if any).

    Accounts are matched the same three ways provisioning writes them: the
    stamped `member_account_id`, then staff id, then email — both of the latter
    being unique per client.
    """
    accounts = db.execute(
        select(MemberAccount).where(MemberAccount.client_id == client_id)
    ).scalars().all()
    by_id = {a.id: a for a in accounts}
    by_staff = {a.staff_id: a for a in accounts}
    by_email = {a.email: a for a in accounts if a.email}

    employees = db.execute(
        select(Employee)
        .where(
            Employee.client_id == client_id,
            Employee.policy_year_id == policy_year_id,
            Employee.status == "active",
        )
        .order_by(Employee.staff_id)
    ).scalars().all()

    # Accounts are unique per client on BOTH email and staff id, so two roster
    # rows sharing either cannot both be provisioned. Detected here, in the one
    # shared classifier, so the count and the send agree — and so the second
    # employee is REPORTED rather than silently dropped or, worse, handed an
    # account on a mailbox that belongs to someone else. (A shared address is
    # real: spouses at the same employer, a department inbox, a copy-paste
    # error. Mailing both people's credentials there would put one member's
    # benefits in another's inbox.) Employees are ordered by staff id, so which
    # row wins is stable between the preview and the run.
    seen_emails: set[str] = set()
    seen_staff: set[str] = set()

    entries: list[_RosterEntry] = []
    for employee in employees:
        account = by_id.get(employee.member_account_id or "") or by_staff.get(
            employee.staff_id
        )
        email = _roster_email(employee)
        duplicate = False
        if account is None:
            # An account already on this address belongs to a DIFFERENT staff id
            # — it is a colleague's, not this employee's. Adopting it here would
            # be worse than the constraint violation it avoids: this employee
            # would be reported as covered, and the colleague's mailbox would be
            # treated as theirs. (The `by_staff` lookup above already covers the
            # legitimate case of an account whose employee link was never
            # stamped.)
            owner = by_email.get(email) if email else None
            duplicate = (
                owner is not None
                or employee.staff_id in seen_staff
                or (email is not None and email in seen_emails)
            )
            if not duplicate:
                seen_staff.add(employee.staff_id)
                if email:
                    seen_emails.add(email)
        entries.append(
            _RosterEntry(
                employee=employee, account=account, email=email, duplicate=duplicate
            )
        )
    return entries


def _bucket(entry: _RosterEntry) -> str:
    account = entry.account
    if account is not None:
        if account.status == MEMBER_STATUS_DISABLED:
            return _BUCKET_DISABLED
        # Status flips to active on the first successful sign-in or set-password
        # (`_issue_member_login`), so it — not `has_password` — is what marks a
        # member as onboarded. An outstanding invite ALSO leaves a password
        # hash on the row (the mailed one-time value), so testing that would
        # class everyone mid-rollout as already done.
        if account.status == MEMBER_STATUS_ACTIVE or account.last_sign_in_at:
            return _BUCKET_SIGNED_IN
        if account.invite_sent_at:
            return _BUCKET_INVITED
    elif entry.duplicate:
        return _BUCKET_DUPLICATE
    if not entry.email:
        return _BUCKET_NO_EMAIL
    return _BUCKET_PENDING


def _deliver_invites(account_ids: list[str], client_id: str) -> None:
    """Issue + mail one-time passwords, one member at a time.

    Runs in the background: Argon2id is ~100ms by design and SMTP is slower
    still, so a full roster would hold a request open for minutes.

    Each account is committed INDIVIDUALLY and `invite_sent_at` is stamped only
    after the mailer accepts the message. Batching the commit would mean a fault
    at member 400 discarded the record of 399 deliveries that really happened —
    and the next run would email all of them a second time, which is the one
    outcome this feature must never produce. A failed send rolls that account's
    credential back to what it was, so nobody is left holding a password that
    was never delivered.

    Touches only control tables (`member_accounts`, `clients`), which live in
    `public` on every dialect — so unlike the claim-review pipeline this needs
    no firm `search_path`.
    """
    db = SessionLocal()
    try:
        client = db.get(Client, client_id)

        slug = client.slug if client else None
        policy = get_auth_policy(db, client_id)
        source = policy.portal_login_source
        sent = failed = 0
        for account_id in account_ids:
            account = db.get(MemberAccount, account_id)
            if account is None or account.status == MEMBER_STATUS_DISABLED:
                continue
            if account.invite_sent_at is not None:
                continue  # delivered by a concurrent run — never send twice
            prior = snapshot_credential(account)
            password = issue_invite_credential(account, policy.password_min_entropy)
            db.commit()
            if send_member_invite(account, password, slug, source):
                account.invite_sent_at = datetime.now(UTC)
                db.commit()
                sent += 1
            else:
                restore_credential(account, prior)
                db.commit()
                failed += 1
        if failed:
            logger.error(
                "Portal invites: %d of %d failed to send for client %s — they stay "
                "unstamped and will be retried by the next run",
                failed, sent + failed, client_id,
            )
    except Exception:
        logger.exception("Portal invite delivery failed for client %s", client_id)
    finally:
        db.close()
        with _SENDING_LOCK:
            _SENDING.discard(client_id)


@router.get("/member-accounts/rollout", response_model=PortalRolloutOut)
def portal_rollout(
    policy_year_id: str = Query(...),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PortalRolloutOut:
    """Portal-access state of the whole roster — the rollout card's data."""
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)

    entries = _roster_entries(db, client_id, policy_year_id)
    counts = dict.fromkeys(
        (
            _BUCKET_PENDING,
            _BUCKET_INVITED,
            _BUCKET_SIGNED_IN,
            _BUCKET_NO_EMAIL,
            _BUCKET_DUPLICATE,
            _BUCKET_DISABLED,
        ),
        0,
    )
    attention: list[PortalRolloutMember] = []
    for entry in entries:
        bucket = _bucket(entry)
        counts[bucket] += 1
        if (
            bucket in (_BUCKET_NO_EMAIL, _BUCKET_DUPLICATE)
            and len(attention) < ATTENTION_LIST_LIMIT
        ):
            attention.append(
                PortalRolloutMember(
                    employee_id=entry.employee.id,
                    staff_id=entry.employee.staff_id,
                    employee_name=entry.employee.employee_name,
                    reason="duplicate" if bucket == _BUCKET_DUPLICATE else "no_email",
                    email=entry.email,
                )
            )
    unreachable = counts[_BUCKET_NO_EMAIL] + counts[_BUCKET_DUPLICATE]
    return PortalRolloutOut(
        employees_total=len(entries),
        invite_pending=counts[_BUCKET_PENDING],
        invited=counts[_BUCKET_INVITED],
        signed_in=counts[_BUCKET_SIGNED_IN],
        no_email=counts[_BUCKET_NO_EMAIL],
        duplicate=counts[_BUCKET_DUPLICATE],
        disabled=counts[_BUCKET_DISABLED],
        mail_deliverable=mail_deliverable(),
        mail_mode=get_settings().mail_mode,
        sending=client_id in _SENDING,
        needs_attention=attention,
        needs_attention_truncated=unreachable > len(attention),
    )


@router.post("/member-accounts/bulk-invite", response_model=BulkInviteResult)
@limiter.limit("10/minute")
def bulk_invite(
    request: Request,
    body: BulkInviteIn,
    background: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkInviteResult:
    """Send a portal invite to every employee who has not had one delivered.

    Provisions an account for EVERY active employee — including those with no
    email address, who get no send but do get an account, so they appear on the
    follow-up list and can be handed a set-password link individually.

    Members who already received an invite, or who are already onboarded, are
    left untouched: the target set is the `pending` bucket alone. That is what
    makes this safe to press repeatedly — it never becomes a second email to
    someone who already has one, only a first email to whoever is left.
    """
    client_id = require_client_id(user)
    assert_policy_year_for_user(body.policy_year_id, user, db)

    # A run already in flight has targets it has not stamped yet; re-queueing
    # them here is how a member ends up with two emails.
    with _SENDING_LOCK:
        if client_id in _SENDING:
            return BulkInviteResult(already_sending=True)

    entries = _roster_entries(db, client_id, body.policy_year_id)
    created = no_email = already = disabled = duplicate = 0
    targets: list[str] = []

    for entry in entries:
        bucket = _bucket(entry)
        if bucket == _BUCKET_DISABLED:
            disabled += 1
            continue
        if bucket in (_BUCKET_INVITED, _BUCKET_SIGNED_IN):
            already += 1
            continue
        if bucket == _BUCKET_DUPLICATE:
            # Deliberately NOT provisioned: an account is unique per client on
            # email and staff id, so this row cannot have one of its own, and
            # attaching it to the colleague's address would deliver a member's
            # credentials — and then their benefits — to someone else's inbox.
            # Reported on the follow-up list for the roster to be corrected.
            duplicate += 1
            continue
        account = entry.account
        if account is None:
            account = _create_account(db, entry.employee, entry.email, user.user_id)
            created += 1
        if bucket == _BUCKET_NO_EMAIL:
            no_email += 1
            continue
        targets.append(account.id)

    write_audit(
        db, user, "member_account.bulk_invited", "member_account", None,
        after={
            "policy_year_id": body.policy_year_id,
            "queued": len(targets),
            "accounts_created": created,
            "no_email": no_email,
            "duplicate": duplicate,
        },
    )
    # Accounts must exist and be committed before delivery starts — the
    # background task opens its own session and looks them up by id.
    db.commit()

    if targets:
        # Claimed BEFORE dispatch — a second request arriving while this one is
        # still queueing must be refused too, not just one arriving mid-send.
        with _SENDING_LOCK:
            _SENDING.add(client_id)
        background.add_task(_deliver_invites, targets, client_id)

    return BulkInviteResult(
        queued=len(targets),
        accounts_created=created,
        no_email=no_email,
        duplicate=duplicate,
        already_invited=already,
        skipped_disabled=disabled,
    )
