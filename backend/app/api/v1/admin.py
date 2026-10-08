"""Provisioning / admin console.

- `system_admin` lists broker firms (the firm picker); firms are created and
  managed in the platform console (`api/v1/platform.py`).
- company administrators (`broker_admin`, `firm_admin`, or `system_admin`
  naming a firm it may access) manage clients.
- firm owners view and manage users and invitations: a `firm_admin` within its
  own firm, the platform master admin (`system_admin`) in any firm, which is
  how a new firm's first `firm_admin` is invited.

Identity is DB-backed: inviting a user provisions a `User` row (status
`invited`) plus an `Invitation` record holding only the SHA-256 of a
single-use link token, returned once and emailed to the invitee. Opening the
link binds the invitee's sign-in, with Microsoft (`app.core.auth._entra_principal`)
or a password (`api/v1/broker_auth.accept_invite`), and the status flips to
`active`; email never binds an identity. A `system_admin` never belongs to a
broker firm (Postgres enforces it with a CHECK). Email addresses are unique per
firm (and among firm-less platform admins), so every lookup by email is scoped
to the firm and no message reveals another firm's accounts.
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit, write_platform_audit
from app.core.auth import ROLE_SYSTEM_ADMIN, VALID_ROLES, CurrentUser
from app.core.broker_auth import hash_invite_token
from app.core.deps import (
    assert_platform_firm_access,
    require_firm_admin,
    require_firm_owner,
    require_system_admin,
)
from app.core.identity_providers import staff_methods
from app.core.mailer import get_mailer
from app.core.settings import get_settings
from app.core.tenancy_host import SlugError
from app.core.tenant_resolution import (
    FirmOriginUnavailable,
    platform_owner_firm,
    public_origin,
)
from app.db.session import get_db
from app.db.tenancy import set_search_path
from app.models import (
    AuthSession,
    BrokerFirm,
    Client,
    ClientAuthPolicy,
    PolicyYear,
    User,
    UserClientAccess,
    WicaIncident,
    WicaPeriod,
    WicaSettings,
)
from app.models.auth import SUBJECT_MEMBER, SUBJECT_USER
from app.models.invitation import (
    INVITE_STATUS_PENDING,
    INVITE_STATUS_REVOKED,
    Invitation,
)
from app.models.platform import DOMAIN_SURFACE_STAFF
from app.models.user import USER_STATUS_ACTIVE, USER_STATUS_DISABLED, USER_STATUS_INVITED
from app.services.brand import DEFAULT_BRAND, firm_card_prefix, resolve_brand
from app.services.client_slug import assign_slug
from app.services.member_invite import mail_deliverable

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

# Roles a firm admin may grant. system_admin can additionally grant system_admin.
_FIRM_GRANTABLE_ROLES = frozenset(
    {"firm_admin", "broker_admin", "broker_viewer", "client_admin", "client_hr"}
)
_CLIENT_ROLES = frozenset({"client_admin", "client_hr"})
# Roles whose broker sign-in can be made to require an authenticator.
_BROKER_SIGN_IN_ROLES = frozenset({"system_admin", "firm_admin", "broker_admin", "broker_viewer"})
_INVITE_TTL_DAYS = 14
_FIRM_EMAIL_TAKEN = "This email already has an account in this firm."
_PLATFORM_EMAIL_TAKEN = "This email already has a platform administrator account."


def _resolve_target_firm(
    user: CurrentUser, requested_firm_id: str | None, db: Session | None = None
) -> str:
    """The firm an admin action targets. Firm roles → own firm; system_admin
    names one explicitly, or falls back to the sole firm when only one exists.

    A firm role naming another firm gets the same 404 as a firm that does not
    exist, so the console never confirms another firm's id.
    """
    if user.role == ROLE_SYSTEM_ADMIN:
        if requested_firm_id:
            return requested_firm_id
        # A single-firm platform has exactly one answer, so demanding the caller
        # name it is pure friction — and it made the admin page unusable for a
        # system_admin, because the UI sends no broker_firm_id: every "Create
        # company" and "Invite" returned 400. Resolve it here rather than
        # guessing in the UI. With two or more firms there IS no unambiguous
        # answer, so still refuse (mirrors _column_id_for_plan in sob_columns).
        if db is not None:
            firm_ids = db.execute(select(BrokerFirm.id).limit(2)).scalars().all()
            if len(firm_ids) == 1:
                return str(firm_ids[0])
            if not firm_ids:
                # Distinct message: "specify a firm" is unactionable advice when
                # there is no firm to name, and this is the very first thing a
                # freshly bootstrapped system_admin hits.
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "No broker firm exists yet — create one first.",
                )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "system_admin must specify broker_firm_id.",
        )
    if requested_firm_id and requested_firm_id != user.broker_firm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    if not user.broker_firm_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no broker firm.")
    return user.broker_firm_id


def _assert_grantable_role(user: CurrentUser, role: str) -> None:
    """system_admin grants any role; a firm_admin any role but system_admin."""
    if role == ROLE_SYSTEM_ADMIN:
        if user.role != ROLE_SYSTEM_ADMIN:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Only system_admin can grant system_admin."
            )
        return
    if role not in _FIRM_GRANTABLE_ROLES or role not in VALID_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid role: {role}")


def _clients_in_firm(db: Session, firm_id: str, client_ids: list[str]) -> list[Client]:
    if not client_ids:
        return []
    rows = list(
        db.execute(
            select(Client).where(
                Client.id.in_(client_ids), Client.broker_firm_id == firm_id
            )
        ).scalars().all()
    )
    found = {c.id for c in rows}
    missing = set(client_ids) - found
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Clients not in firm: {', '.join(sorted(missing))}",
        )
    return rows


def _set_client_access(db: Session, user_id: str, firm_id: str, client_ids: list[str]) -> None:
    """Replace a user's per-client grants with the given set (firm-validated)."""
    _clients_in_firm(db, firm_id, client_ids)
    db.query(UserClientAccess).filter(UserClientAccess.user_id == user_id).delete()
    for cid in client_ids:
        db.add(UserClientAccess(user_id=user_id, client_id=cid))


# ── Broker firms (system_admin firm picker) ───────────────────────────────────
class BrokerFirmOut(BaseModel):
    id: str
    name: str
    client_count: int


@router.get("/broker-firms", response_model=list[BrokerFirmOut])
def list_broker_firms(
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[BrokerFirmOut]:
    counts: dict[str, int] = {
        firm_id: int(count)
        for firm_id, count in db.execute(
            select(Client.broker_firm_id, func.count(Client.id)).group_by(
                Client.broker_firm_id
            )
        ).all()
        if firm_id is not None
    }
    firms = db.execute(select(BrokerFirm).order_by(BrokerFirm.name)).scalars().all()
    return [
        BrokerFirmOut(id=f.id, name=f.name, client_count=int(counts.get(f.id, 0)))
        for f in firms
    ]


# ── Clients ───────────────────────────────────────────────────────────────────
class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    broker_firm_id: str | None = None  # system_admin only
    # Optional override; derived from the name when omitted.
    slug: str | None = Field(default=None, max_length=63)
    legal_name: str | None = Field(default=None, max_length=255)


class ClientPatch(BaseModel):
    """A PARTIAL update — every field is optional and only what was SENT is
    applied (`model_fields_set`, as on `PATCH /policy-years/{id}`).

    Not a convenience. `name` used to be required and was the only field the UI
    sent, so the moment a second nullable field exists here, a plain rename
    posting `{name}` would read `legal_name=None` as "clear it" and silently
    drop the registered name. The alias is already protected from exactly this
    (a rename never moves `slug`); partial semantics extend that to every field
    instead of re-deciding it per field."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=63)
    legal_name: str | None = Field(default=None, max_length=255)
    # Kill switches for the employee portal and the HR portal. Turning one off
    # also ends every live session on that surface for this company.
    portal_enabled: bool | None = None
    hr_enabled: bool | None = None


class ClientOut(BaseModel):
    id: str
    name: str
    broker_firm_id: str
    # The tenant label. Surfaced so the broker UI can build absolute links to
    # the member/HR surfaces — `{slug}.portal.<base>` in subdomain mode, or the
    # `/portal/{slug}` path on a single-host deployment. Set-password and invite
    # links are sent to people who are NOT on the broker host.
    slug: str | None = None
    # The registered company name; None until a broker fills it in. Never
    # derived from `name` — a short handle is not a legal name.
    legal_name: str | None = None
    portal_enabled: bool = True
    hr_enabled: bool = True


def _client_out(client: Client) -> ClientOut:
    """One builder for all three client endpoints — three hand-rolled copies is
    how a newly added field ends up missing from `list` but present on `patch`,
    which reads to the UI as the value not saving."""
    return ClientOut(
        id=client.id,
        name=client.name,
        broker_firm_id=client.broker_firm_id,
        slug=client.slug,
        legal_name=client.legal_name,
        portal_enabled=client.portal_enabled,
        hr_enabled=client.hr_enabled,
    )


# Surface switch on `clients` → the `auth_sessions.subject_type` it governs.
_SURFACE_SUBJECTS = {"portal_enabled": SUBJECT_MEMBER, "hr_enabled": SUBJECT_USER}


def _revoke_surface_sessions(db: Session, client_id: str, subject_type: str) -> int:
    """End every live session one company holds on one surface. No commit."""
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.subject_type == subject_type,
            AuthSession.client_id == client_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _optional_text(raw: str | None) -> str | None:
    """Trim, and treat a blank as an explicit CLEAR rather than as the string
    `""` — an empty legal name must read as "not filled in" everywhere, not as
    a name that happens to render as nothing."""
    text = (raw or "").strip()
    return text or None


def _load_firm_client(db: Session, user: CurrentUser, client_id: str) -> Client:
    """A company the caller may change: one in its own firm, or for the platform
    admin one in a firm it holds standing access or a write grant on."""
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    if user.role != ROLE_SYSTEM_ADMIN and client.broker_firm_id != user.broker_firm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    assert_platform_firm_access(
        db, user, client.broker_firm_id, write=True, not_found="Client not found"
    )
    return client


@router.post("/clients", response_model=ClientOut, status_code=201)
def create_client(
    body: ClientCreate,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> ClientOut:
    firm_id = _resolve_target_firm(user, body.broker_firm_id, db)
    if db.get(BrokerFirm, firm_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    assert_platform_firm_access(db, user, firm_id, write=True, not_found="Broker firm not found")
    client = Client(
        name=body.name.strip(),
        legal_name=_optional_text(body.legal_name),
        broker_firm_id=firm_id,
        card_id_prefix=firm_card_prefix(db, firm_id),
    )
    db.add(client)
    db.flush()
    # Always give the tenant a subdomain label: `resolve_tenant_context` looks
    # tenants up by it, so a NULL slug makes the HR surface and portal
    # credential login 404 for this company.
    try:
        assign_slug(db, client, body.slug)
    except SlugError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    # New companies start with HR two-factor required; every other sign-in
    # setting keeps its default. Existing companies are not changed.
    db.add(ClientAuthPolicy(client_id=client.id, mfa_hr_enabled=True, mfa_hr_required=True))
    # A system admin may create a company outside the firm it is routed to.
    # Record the creation in the new company's own firm schema.
    set_search_path(db, firm_id)
    write_audit(db, user, action="create", entity_type="client", entity_id=client.id,
                after={"name": client.name, "broker_firm_id": firm_id,
                       "slug": client.slug, "legal_name": client.legal_name,
                       "mfa_hr_required": True},
                client_id=client.id)
    db.commit()
    return _client_out(client)


@router.get("/clients", response_model=list[ClientOut])
def list_clients(
    broker_firm_id: str | None = Query(None),
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> list[ClientOut]:
    firm_id = _resolve_target_firm(user, broker_firm_id, db)
    assert_platform_firm_access(db, user, firm_id, write=False, not_found="Broker firm not found")
    clients = db.execute(
        select(Client).where(Client.broker_firm_id == firm_id).order_by(Client.name)
    ).scalars().all()
    return [_client_out(c) for c in clients]


@router.patch("/clients/{client_id}", response_model=ClientOut)
def patch_client(
    client_id: str,
    body: ClientPatch,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> ClientOut:
    client = _load_firm_client(db, user, client_id)
    sent = body.model_fields_set
    audited = ("name", "slug", "legal_name", *_SURFACE_SUBJECTS)
    before = {field: getattr(client, field) for field in audited}
    if "name" in sent and body.name:
        client.name = body.name.strip()
    if "legal_name" in sent:
        client.legal_name = _optional_text(body.legal_name)
    # Renaming does NOT move the alias — live links and bookmarks would break,
    # and on a single-host deployment the alias is the `/portal/{slug}` path
    # that every emailed invite points at. Only an explicit slug changes it; a
    # client that somehow has none (pre-slug row) gets one derived now.
    if body.slug or not client.slug:
        try:
            assign_slug(db, client, body.slug)
        except SlugError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    revoked: dict[str, int] = {}
    for field, subject_type in _SURFACE_SUBJECTS.items():
        enabled = getattr(body, field)
        if field not in sent or enabled is None:
            continue
        setattr(client, field, enabled)
        if not enabled:
            # Turning a surface off ends access now, not when tokens expire.
            # Repeating it also ends a session that raced the first switch.
            revoked[field] = _revoke_surface_sessions(db, client.id, subject_type)
    after = {field: getattr(client, field) for field in audited}
    # A system admin may edit a company outside the firm it is routed to.
    # Record the change in the company's own firm schema.
    set_search_path(db, client.broker_firm_id)
    write_audit(db, user, action="update", entity_type="client", entity_id=client.id,
                before=before, after=after, client_id=client.id)
    for field, count in revoked.items():
        write_audit(db, user, action="revoke_sessions", entity_type="client",
                    entity_id=client.id, after={"switch": field, "sessions_revoked": count},
                    client_id=client.id)
    db.commit()
    return _client_out(client)


@router.delete("/clients/{client_id}", status_code=204)
def delete_client(
    client_id: str,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Delete an empty client company. Refused while it still holds benefit
    years — those (and everything under them: employees, claims, enrollment)
    would be orphaned, so require them to be removed first. Per-client user
    grants (``user_client_access``) cascade via the FK on delete."""
    client = _load_firm_client(db, user, client_id)
    # A system admin may target a company outside the currently selected firm.
    # Check its operational dependencies in the target firm's schema.
    set_search_path(db, client.broker_firm_id)
    # Incident intake takes this same lock. Keep configuration cleanup and
    # retained-incident checks atomic with respect to a concurrent new incident.
    db.scalar(select(WicaSettings).where(WicaSettings.client_id == client_id).with_for_update())
    if db.scalar(select(WicaIncident.id).where(WicaIncident.client_id == client_id).limit(1)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This company has retained WICA incidents and cannot be deleted. "
            "Disable WICA in Company settings to stop new intake; existing records are retained.",
        )
    year_count = db.execute(
        select(func.count())
        .select_from(PolicyYear)
        .where(PolicyYear.client_id == client_id)
    ).scalar_one()
    if year_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Delete this company's {year_count} benefit year"
            f"{'s' if year_count != 1 else ''} first before removing the company.",
        )
    before = {"name": client.name, "broker_firm_id": client.broker_firm_id}
    write_audit(db, user, action="delete", entity_type="client",
                entity_id=client_id, before=before, client_id=client_id)
    try:
        # Explicitly remove unused configuration, never cascade incident data.
        # Restrictive model/migration FKs remain the final retention safeguard.
        db.execute(delete(WicaPeriod).where(WicaPeriod.client_id == client_id))
        db.execute(delete(WicaSettings).where(WicaSettings.client_id == client_id))
        db.delete(client)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This company still has linked records or was updated concurrently. "
            "Refresh and review its records before deleting it.",
        ) from None
    return Response(status_code=204)


# ── Users ─────────────────────────────────────────────────────────────────────
class UserOut(BaseModel):
    id: str
    email: str
    display_name: str | None
    role: str
    status: str
    broker_firm_id: str | None
    client_ids: list[str]
    external_id: str | None = None
    broker_mfa_required: bool = False


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    status: str | None = None
    client_ids: list[str] | None = None
    external_id: str | None = None
    broker_mfa_required: bool | None = None
    # The firm a system_admin joins when changing to a firm role. Required for
    # that change, since a platform admin belongs to no firm; not accepted
    # otherwise (moving an account between firms is not supported).
    broker_firm_id: str | None = None


def _user_out(db: Session, u: User) -> UserOut:
    cids = list(
        db.execute(
            select(UserClientAccess.client_id).where(UserClientAccess.user_id == u.id)
        ).scalars().all()
    )
    return UserOut(
        id=u.id, email=u.email, display_name=u.display_name, role=u.role,
        status=u.status, broker_firm_id=u.broker_firm_id, client_ids=cids,
        external_id=u.external_id,
        broker_mfa_required=u.broker_mfa_required,
    )


def _load_firm_user(db: Session, user: CurrentUser, target_id: str) -> User:
    """The account to change, locked. A firm_admin reaches its own firm's
    accounts only; any other id is a 404, never a 403, so the console does not
    confirm that another firm's (or a platform admin's) account exists."""
    target = db.execute(
        select(User).where(User.id == target_id).with_for_update()
    ).scalar_one_or_none()
    if target is None or (
        user.role != ROLE_SYSTEM_ADMIN and target.broker_firm_id != user.broker_firm_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return target


def _account_scope(firm_id: str | None) -> Any:
    """Where an email must be unique: within a firm, or among platform admins."""
    return User.broker_firm_id == firm_id if firm_id else User.broker_firm_id.is_(None)


def _assert_email_free(
    db: Session, email: str, firm_id: str | None, *, exclude_id: str | None = None
) -> None:
    """409 when ``email`` already has an account where it would be placed.

    The message names only the firm being acted on: another firm's accounts
    are never confirmed or denied.
    """
    stmt = select(User.id).where(User.email == email, _account_scope(firm_id))
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    if db.execute(stmt.limit(1)).scalar_one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, _FIRM_EMAIL_TAKEN if firm_id else _PLATFORM_EMAIL_TAKEN
        )


def _invitation_account(db: Session, inv: Invitation) -> User | None:
    """The account an invitation provisioned (same email, same firm or firm-less)."""
    firm_id = None if inv.role == ROLE_SYSTEM_ADMIN else inv.broker_firm_id
    return db.execute(
        select(User).where(User.email == inv.email, _account_scope(firm_id))
    ).scalar_one_or_none()


@router.get("/users", response_model=list[UserOut])
def list_users(
    broker_firm_id: str | None = Query(None),
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> list[UserOut]:
    firm_id = _resolve_target_firm(user, broker_firm_id, db)
    # Platform system_admins have NO broker firm (they operate across firms), so
    # a purely firm-scoped list rendered "No users yet" while those accounts held
    # full access to everything — an admin console that hides the most privileged
    # rows on the platform. Show them to a system_admin, who is the only caller
    # entitled to see accounts outside their own firm.
    conditions = [User.broker_firm_id == firm_id]
    if user.role == ROLE_SYSTEM_ADMIN:
        conditions.append(
            (User.broker_firm_id.is_(None)) & (User.role == "system_admin")
        )
    users = db.execute(
        select(User).where(or_(*conditions)).order_by(User.email)
    ).scalars().all()
    return [_user_out(db, u) for u in users]


def _assert_admin_change_is_recoverable(
    db: Session, actor: CurrentUser, target: User, body: UserPatch
) -> None:
    """Refuse edits to a platform admin that no one could undo from the UI.

    Disabling or demoting the last active system_admin is immediately fatal:
    `auth.py` rejects disabled users, and only a system_admin may grant
    system_admin, so recovery needs shell access to
    `scripts/create_system_admin.py`. (Demotion itself is safe otherwise: it
    must name the firm the account joins — see `_apply_role_change` — so the
    row never ends up firm-less, outside every admin list.)

    409 rather than 403 — the caller has the right to do this in principle, the
    platform state is what makes it unsafe. No administrator may disable their
    own account either.
    """
    disabling = body.status is not None and body.status == USER_STATUS_DISABLED
    if target.id == actor.user_id and disabling:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot disable your own account."
        )
    if target.role != "system_admin":
        return

    demoting = body.role is not None and body.role != "system_admin"
    if not (demoting or disabling):
        return

    remaining = db.execute(
        select(func.count(User.id)).where(
            User.role == "system_admin",
            User.status == USER_STATUS_ACTIVE,
            User.id != target.id,
        )
    ).scalar_one()
    if not remaining:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is the last active system_admin — removing it would lock "
            "everyone out of platform administration.",
        )


def _apply_role_change(db: Session, actor: CurrentUser, target: User, body: UserPatch) -> None:
    """Change a user's role while keeping the rule that a system_admin has no firm.

    Promotion detaches the account from its firm and drops its company grants:
    a platform admin reaches companies through its role, never through grants.
    Demotion must name the firm the account joins, validated to exist —
    otherwise the row would be firm-less and outside every admin list.
    """
    demoting = (
        target.role == "system_admin"
        and body.role is not None
        and body.role != "system_admin"
    )
    if demoting:
        if not body.broker_firm_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Choose the broker firm this account will belong to.",
            )
        if db.get(BrokerFirm, body.broker_firm_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
        _assert_email_free(db, target.email, body.broker_firm_id, exclude_id=target.id)
    elif body.broker_firm_id is not None and body.broker_firm_id != target.broker_firm_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A broker firm can be chosen only when changing a platform admin to a firm role.",
        )
    if body.role is None:
        return
    _assert_grantable_role(actor, body.role)
    if demoting:
        target.broker_firm_id = body.broker_firm_id
    elif body.role == "system_admin":
        _assert_email_free(db, target.email, None, exclude_id=target.id)
        target.broker_firm_id = None
        db.execute(delete(UserClientAccess).where(UserClientAccess.user_id == target.id))
    target.role = body.role


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: str,
    body: UserPatch,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> UserOut:
    target = _load_firm_user(db, user, user_id)
    _assert_admin_change_is_recoverable(db, user, target, body)
    before = {"role": target.role, "status": target.status, "display_name": target.display_name,
              "external_id": target.external_id, "broker_mfa_required": target.broker_mfa_required,
              "broker_firm_id": target.broker_firm_id}
    mfa_changed = (body.broker_mfa_required is not None
                   and body.broker_mfa_required != target.broker_mfa_required)
    if body.broker_mfa_required is not None:
        next_role = body.role if body.role is not None else target.role
        if next_role not in _BROKER_SIGN_IN_ROLES:
            raise HTTPException(422, "This setting applies only to broker accounts.")
        target.broker_mfa_required = body.broker_mfa_required
    if body.external_id is not None:
        try:
            oid = str(UUID(body.external_id))
        except ValueError:
            raise HTTPException(422, "Microsoft object ID must be a valid UUID.") from None
        if target.external_id and target.external_id != oid:
            raise HTTPException(409, "This account is already bound to a Microsoft identity.")
        next_role = body.role if body.role is not None else target.role
        directory = staff_methods(db, target.broker_firm_id, next_role).entra
        if directory is None:
            raise HTTPException(
                422, "Microsoft sign-in is not enabled for this firm, so no object ID can be bound."
            )
        if db.query(User).filter(User.external_id == oid, User.id != target.id).first():
            raise HTTPException(409, "That Microsoft identity is already registered.")
        target.external_id = oid
        target.external_tid = directory.tenant_id
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "That Microsoft identity is already registered.") from None
    if body.display_name is not None:
        target.display_name = body.display_name.strip() or None
    _apply_role_change(db, user, target, body)
    if body.status is not None:
        if body.status not in (USER_STATUS_ACTIVE, USER_STATUS_DISABLED, USER_STATUS_INVITED):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid status")
        target.status = body.status
    if body.client_ids is not None:
        # Grants are validated against the TARGET's firm. A firm-less user
        # (e.g. system_admin) can't hold per-client grants.
        if not target.broker_firm_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot grant client access to a user without a broker firm.",
            )
        _set_client_access(db, target.id, target.broker_firm_id, body.client_ids)
    from app.core.sessions import revoke_all_for_subject

    if body.role is not None or body.status is not None or body.client_ids is not None:
        revoke_all_for_subject(db, "broker", target.id)
        revoke_all_for_subject(db, "user", target.id)
    elif mfa_changed:
        revoke_all_for_subject(db, "broker", target.id)
    # File the change in the account's own firm, whichever company a platform
    # admin had selected (a platform admin's own row files under `public`).
    set_search_path(db, target.broker_firm_id)
    write_audit(db, user, action="update", entity_type="user", entity_id=target.id,
                before=before,
                after={"role": target.role, "status": target.status,
                       "display_name": target.display_name, "external_id": target.external_id,
                       "broker_mfa_required": target.broker_mfa_required,
                       "broker_firm_id": target.broker_firm_id},
                client_id=None)
    db.commit()
    db.refresh(target)
    return _user_out(db, target)


# ── Invitations ───────────────────────────────────────────────────────────────
class InvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    # Optional, and deliberately not required: an invite is often sent from an
    # email address alone, and blocking on a name would only get a guess typed
    # in. Absent, the list falls back to the email until someone fills it in.
    display_name: str | None = Field(default=None, max_length=255)
    role: str
    client_ids: list[str] = Field(default_factory=list)
    broker_firm_id: str | None = None  # system_admin only
    external_id: str | None = None


class InvitationOut(BaseModel):
    """An invitation as lists show it. Its link token is never listed: only a
    hash is stored, and the raw token is returned once, at creation."""

    id: str
    email: str
    role: str
    status: str
    broker_firm_id: str
    user_id: str
    expires_at: datetime | None


class InvitationCreatedOut(InvitationOut):
    # The single-use link token, shown this once.
    invite_token: str
    # `<staff origin>/sign-in#invite=<token>`; None when the firm has no web
    # address yet, or for a company (HR) role, which does not sign in there.
    invite_url: str | None


def _bound_identity(db: Session, raw: str | None, firm_id: str | None, role: str) -> str | None:
    """An administrator-supplied Microsoft object ID, validated and unused.

    It is recorded with the directory the account will sign in through, so
    the firm must offer Microsoft sign-in.
    """
    if not raw:
        return None
    try:
        oid = str(UUID(raw))
    except ValueError:
        raise HTTPException(422, "Microsoft object ID must be a valid UUID.") from None
    if staff_methods(db, firm_id, role).entra is None:
        raise HTTPException(
            422, "Microsoft sign-in is not enabled for this firm, so no object ID can be bound."
        )
    if db.query(User).filter(User.external_id == oid).first():
        raise HTTPException(409, "That Microsoft identity is already registered.")
    return oid


def _invite_url(db: Session, firm_id: str, role: str, raw_token: str) -> str | None:
    """Where the invitee opens their invitation: the firm's staff sign-in page
    (a platform host for a platform admin). The token travels in the fragment,
    so it never reaches a server log."""
    if role not in _BROKER_SIGN_IN_ROLES:
        return None
    try:
        origin = (
            get_settings().frontend_origin.rstrip("/") if role == ROLE_SYSTEM_ADMIN
            else public_origin(db, firm_id, DOMAIN_SURFACE_STAFF)
        )
    except FirmOriginUnavailable:
        return None
    return f"{origin}/sign-in#invite={raw_token}"


def _mail_invitation(db: Session, email: str, firm_id: str, role: str, url: str) -> None:
    """Email the invitee their link when mail is deliverable. Never raises."""
    if not mail_deliverable():
        return
    firm = platform_owner_firm(db) if role == ROLE_SYSTEM_ADMIN else None
    firm = firm or db.get(BrokerFirm, firm_id)
    try:
        brand = resolve_brand(db, firm.id) if firm else DEFAULT_BRAND
        get_mailer(brand).send_staff_invite(
            email, firm.name if firm else "your organisation", url
        )
    except Exception:
        # The link itself is never logged: it carries the single-use token.
        logger.warning("Failed to email a staff invitation")


@router.post("/invitations", response_model=InvitationCreatedOut, status_code=201)
def create_invitation(
    body: InvitationCreate,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> InvitationCreatedOut:
    firm_id = _resolve_target_firm(user, body.broker_firm_id, db)
    if db.get(BrokerFirm, firm_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    _assert_grantable_role(user, body.role)
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid email address.")

    # Email is unique per firm (a platform admin's among platform admins).
    # Broker Microsoft sign-in uses only a bound (directory, object ID).
    account_firm = None if body.role == ROLE_SYSTEM_ADMIN else firm_id
    _assert_email_free(db, email, account_firm)
    email_taken = _FIRM_EMAIL_TAKEN if account_firm else _PLATFORM_EMAIL_TAKEN

    client_ids = body.client_ids if body.role in _CLIENT_ROLES else []
    _clients_in_firm(db, firm_id, client_ids)
    oid = _bound_identity(db, body.external_id, account_firm, body.role)
    directory = staff_methods(db, account_firm, body.role).entra if oid else None

    # Provision the user up front. The invitee binds their sign-in by opening
    # the link (Microsoft or a password, as the firm offers); an object ID an
    # administrator already supplied is bound now. A platform admin belongs to
    # no firm; its invitation row still records the issuing firm because that
    # column is required.
    display_name = (body.display_name or "").strip() or None
    new_user = User(
        external_id=oid, external_tid=directory.tenant_id if directory else None,
        email=email, display_name=display_name,
        broker_firm_id=account_firm,
        role=body.role, status=USER_STATUS_INVITED,
    )
    db.add(new_user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "That email or Microsoft identity is already registered."
        ) from None
    for cid in client_ids:
        db.add(UserClientAccess(user_id=new_user.id, client_id=cid))

    raw_token = secrets.token_urlsafe(32)
    invite = Invitation(
        email=email, broker_firm_id=firm_id, role=body.role,
        token=hash_invite_token(raw_token), status=INVITE_STATUS_PENDING,
        invited_by=user.user_id,
        client_ids={"ids": client_ids} if client_ids else None,
        expires_at=datetime.now(UTC) + timedelta(days=_INVITE_TTL_DAYS),
    )
    db.add(invite)
    db.flush()
    after = {"email": email, "display_name": display_name,
             "role": body.role, "broker_firm_id": firm_id}
    if user.role == ROLE_SYSTEM_ADMIN:
        # The platform's onboarding path (a new firm's first firm_admin) needs
        # no access grant, so it is on the platform trail as well.
        write_platform_audit(db, user, action="invitation.create", entity_type="invitation",
                             entity_id=invite.id, broker_firm_id=firm_id, detail=after)
    set_search_path(db, firm_id)
    write_audit(db, user, action="create", entity_type="invitation", entity_id=invite.id,
                after=after, client_id=None)
    try:
        db.commit()
    except IntegrityError as exc:
        # A concurrent invite for the same email won the unique-email race.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, email_taken) from exc
    invite_url = _invite_url(db, firm_id, body.role, raw_token)
    if invite_url is not None:
        _mail_invitation(db, email, firm_id, body.role, invite_url)
    return InvitationCreatedOut(
        id=invite.id, email=email, role=body.role, status=invite.status,
        broker_firm_id=firm_id, user_id=new_user.id, expires_at=invite.expires_at,
        invite_token=raw_token, invite_url=invite_url,
    )


@router.get("/invitations", response_model=list[InvitationOut])
def list_invitations(
    broker_firm_id: str | None = Query(None),
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> list[InvitationOut]:
    firm_id = _resolve_target_firm(user, broker_firm_id, db)
    conditions = [
        Invitation.broker_firm_id == firm_id,
        Invitation.status == INVITE_STATUS_PENDING,
    ]
    if user.role != ROLE_SYSTEM_ADMIN:
        # A platform-admin invitation records the firm it was issued from, but
        # it is not that firm's account to see or revoke.
        conditions.append(Invitation.role != ROLE_SYSTEM_ADMIN)
    invites = db.execute(
        select(Invitation).where(*conditions).order_by(Invitation.created_at.desc())
    ).scalars().all()
    out: list[InvitationOut] = []
    for inv in invites:
        u = _invitation_account(db, inv)
        out.append(InvitationOut(
            id=inv.id, email=inv.email, role=inv.role, status=inv.status,
            broker_firm_id=inv.broker_firm_id,
            user_id=u.id if u else "", expires_at=inv.expires_at,
        ))
    return out


@router.post("/invitations/{invitation_id}/revoke", status_code=200)
def revoke_invitation(
    invitation_id: str,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    inv = db.get(Invitation, invitation_id)
    if inv is None or (
        user.role != ROLE_SYSTEM_ADMIN
        and (inv.broker_firm_id != user.broker_firm_id or inv.role == ROLE_SYSTEM_ADMIN)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    inv.status = INVITE_STATUS_REVOKED
    # If the invited user never signed in, disable the provisioned row.
    invited = _invitation_account(db, inv)
    if invited is not None and invited.status == USER_STATUS_INVITED:
        invited.status = USER_STATUS_DISABLED
    if user.role == ROLE_SYSTEM_ADMIN:
        write_platform_audit(db, user, action="invitation.revoke", entity_type="invitation",
                             entity_id=inv.id, broker_firm_id=inv.broker_firm_id,
                             detail={"email": inv.email, "role": inv.role})
    set_search_path(db, inv.broker_firm_id)
    write_audit(db, user, action="revoke", entity_type="invitation", entity_id=inv.id,
                after={"email": inv.email}, client_id=None)
    db.commit()
    return {"revoked": True}
