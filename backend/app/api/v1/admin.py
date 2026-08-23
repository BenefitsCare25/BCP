"""Provisioning / admin console.

- `system_admin` creates broker firms.
- firm admins (`broker_admin`, or `system_admin` naming a firm) manage their
  firm's clients, users, and invitations.

Identity is DB-backed: inviting a user provisions a `User` row (status
`invited`) plus an `Invitation` token record. On first Entra sign-in the oid is
linked to that row by email and the status flips to `active`
(see `app.core.auth._entra_principal`).
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import VALID_ROLES, CurrentUser
from app.core.deps import require_firm_admin, require_system_admin
from app.core.tenancy_host import SlugError
from app.db.session import engine, get_db
from app.db.tenancy import provision_firm_schema
from app.models import BrokerFirm, Client, PolicyYear, User, UserClientAccess
from app.models.invitation import (
    INVITE_STATUS_PENDING,
    INVITE_STATUS_REVOKED,
    Invitation,
)
from app.models.user import USER_STATUS_ACTIVE, USER_STATUS_DISABLED, USER_STATUS_INVITED
from app.services.client_slug import assign_slug

router = APIRouter(prefix="/admin", tags=["admin"])

# Roles a firm admin may grant. system_admin can additionally grant system_admin.
_FIRM_GRANTABLE_ROLES = frozenset(
    {"broker_admin", "broker_viewer", "client_admin", "client_hr"}
)
_CLIENT_ROLES = frozenset({"client_admin", "client_hr"})
_INVITE_TTL_DAYS = 14


def _resolve_target_firm(
    user: CurrentUser, requested_firm_id: str | None, db: Session | None = None
) -> str:
    """The firm an admin action targets. broker_admin → own firm; system_admin
    names one explicitly, or falls back to the sole firm when only one exists."""
    if user.role == "system_admin":
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
    # broker_admin
    if requested_firm_id and requested_firm_id != user.broker_firm_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot manage another firm.")
    if not user.broker_firm_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no broker firm.")
    return user.broker_firm_id


def _assert_grantable_role(user: CurrentUser, role: str) -> None:
    if role == "system_admin":
        if user.role != "system_admin":
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


# ── Broker firms (system_admin) ───────────────────────────────────────────────
class BrokerFirmCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class BrokerFirmOut(BaseModel):
    id: str
    name: str
    client_count: int


@router.post("/broker-firms", response_model=BrokerFirmOut, status_code=201)
def create_broker_firm(
    body: BrokerFirmCreate,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> BrokerFirmOut:
    firm = BrokerFirm(name=body.name.strip())
    db.add(firm)
    db.flush()  # assigns firm.id; not yet committed
    # Provision the firm's schema BEFORE committing the row. If provisioning
    # fails, the firm row rolls back rather than being left orphaned — an
    # orphaned firm (row, no schema) would 500 every future login for it.
    # No-op on SQLite.
    provision_firm_schema(engine, firm.id)
    write_audit(db, user, action="create", entity_type="broker_firm", entity_id=firm.id,
                after={"name": firm.name})
    db.commit()
    return BrokerFirmOut(id=firm.id, name=firm.name, client_count=0)


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
    )


def _optional_text(raw: str | None) -> str | None:
    """Trim, and treat a blank as an explicit CLEAR rather than as the string
    `""` — an empty legal name must read as "not filled in" everywhere, not as
    a name that happens to render as nothing."""
    text = (raw or "").strip()
    return text or None


def _load_firm_client(db: Session, user: CurrentUser, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    if user.role != "system_admin" and client.broker_firm_id != user.broker_firm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
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
    client = Client(
        name=body.name.strip(),
        legal_name=_optional_text(body.legal_name),
        broker_firm_id=firm_id,
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
    write_audit(db, user, action="create", entity_type="client", entity_id=client.id,
                after={"name": client.name, "broker_firm_id": firm_id,
                       "slug": client.slug, "legal_name": client.legal_name})
    db.commit()
    return _client_out(client)


@router.get("/clients", response_model=list[ClientOut])
def list_clients(
    broker_firm_id: str | None = Query(None),
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> list[ClientOut]:
    firm_id = _resolve_target_firm(user, broker_firm_id, db)
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
    before = {
        "name": client.name,
        "slug": client.slug,
        "legal_name": client.legal_name,
    }
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
    after = {
        "name": client.name,
        "slug": client.slug,
        "legal_name": client.legal_name,
    }
    write_audit(db, user, action="update", entity_type="client", entity_id=client.id,
                before=before, after=after)
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
                entity_id=client_id, before=before)
    db.delete(client)
    db.commit()
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


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    status: str | None = None
    client_ids: list[str] | None = None


def _user_out(db: Session, u: User) -> UserOut:
    cids = list(
        db.execute(
            select(UserClientAccess.client_id).where(UserClientAccess.user_id == u.id)
        ).scalars().all()
    )
    return UserOut(
        id=u.id, email=u.email, display_name=u.display_name, role=u.role,
        status=u.status, broker_firm_id=u.broker_firm_id, client_ids=cids,
    )


def _load_firm_user(db: Session, user: CurrentUser, target_id: str) -> User:
    target = db.get(User, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.role != "system_admin" and target.broker_firm_id != user.broker_firm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return target


@router.get("/users", response_model=list[UserOut])
def list_users(
    broker_firm_id: str | None = Query(None),
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> list[UserOut]:
    firm_id = _resolve_target_firm(user, broker_firm_id, db)
    # Platform system_admins have NO broker firm (they operate across firms), so
    # a purely firm-scoped list rendered "No users yet" while those accounts held
    # full access to everything — an admin console that hides the most privileged
    # rows on the platform. Show them to a system_admin, who is the only caller
    # entitled to see accounts outside their own firm.
    conditions = [User.broker_firm_id == firm_id]
    if user.role == "system_admin":
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

    Firm-less `system_admin` rows became editable when the user list started
    showing them, which opened two one-way doors:

    - Demoting one leaves `broker_firm_id` NULL while the role is no longer
      system_admin, so the row matches neither branch of the list query and
      DISAPPEARS from every admin surface — unreachable, with no way back.
    - Disabling the last system_admin is immediately fatal: `auth.py` rejects
      disabled users, and only a system_admin may grant system_admin, so
      recovery needs shell access to `scripts/create_system_admin.py`.

    409 rather than 403 — the caller has the right to do this in principle, the
    platform state is what makes it unsafe.
    """
    if target.role != "system_admin":
        return

    demoting = body.role is not None and body.role != "system_admin"
    disabling = body.status is not None and body.status == USER_STATUS_DISABLED

    if demoting and not target.broker_firm_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This platform admin belongs to no broker firm, so changing their "
            "role would strand the account with no way to reach it. Assign them "
            "to a firm first.",
        )

    if not (demoting or disabling):
        return

    if target.id == actor.user_id and disabling:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot disable your own account."
        )

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


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: str,
    body: UserPatch,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    target = _load_firm_user(db, user, user_id)
    _assert_admin_change_is_recoverable(db, user, target, body)
    before = {"role": target.role, "status": target.status, "display_name": target.display_name}
    if body.display_name is not None:
        target.display_name = body.display_name.strip() or None
    if body.role is not None:
        _assert_grantable_role(user, body.role)
        target.role = body.role
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
    write_audit(db, user, action="update", entity_type="user", entity_id=target.id,
                before=before,
                after={"role": target.role, "status": target.status,
                       "display_name": target.display_name})
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


class InvitationOut(BaseModel):
    id: str
    email: str
    role: str
    status: str
    broker_firm_id: str
    token: str
    user_id: str
    expires_at: datetime | None


@router.post("/invitations", response_model=InvitationOut, status_code=201)
def create_invitation(
    body: InvitationCreate,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> InvitationOut:
    firm_id = _resolve_target_firm(user, body.broker_firm_id, db)
    if db.get(BrokerFirm, firm_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    _assert_grantable_role(user, body.role)
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid email address.")

    # A user's email is a global platform identity (one person → one firm),
    # required for DB-backed Entra matching by email. So an email already in
    # use anywhere can't be re-invited.
    if db.query(User).filter(User.email == email).one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That email is already registered on the platform.",
        )

    client_ids = body.client_ids if body.role in _CLIENT_ROLES else []
    _clients_in_firm(db, firm_id, client_ids)

    # Provision the user up front (status invited); first Entra sign-in links
    # the oid by email and flips status to active.
    display_name = (body.display_name or "").strip() or None
    new_user = User(
        external_id=None, email=email, display_name=display_name,
        broker_firm_id=firm_id, role=body.role, status=USER_STATUS_INVITED,
    )
    db.add(new_user)
    db.flush()
    for cid in client_ids:
        db.add(UserClientAccess(user_id=new_user.id, client_id=cid))

    invite = Invitation(
        email=email, broker_firm_id=firm_id, role=body.role,
        token=secrets.token_urlsafe(32), status=INVITE_STATUS_PENDING,
        invited_by=user.user_id,
        client_ids={"ids": client_ids} if client_ids else None,
        expires_at=datetime.now(UTC) + timedelta(days=_INVITE_TTL_DAYS),
    )
    db.add(invite)
    db.flush()
    write_audit(db, user, action="create", entity_type="invitation", entity_id=invite.id,
                after={"email": email, "display_name": display_name,
                       "role": body.role, "broker_firm_id": firm_id})
    try:
        db.commit()
    except IntegrityError as exc:
        # A concurrent invite for the same email won the unique-email race.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That email is already registered on the platform.",
        ) from exc
    return InvitationOut(
        id=invite.id, email=email, role=body.role, status=invite.status,
        broker_firm_id=firm_id, token=invite.token, user_id=new_user.id,
        expires_at=invite.expires_at,
    )


@router.get("/invitations", response_model=list[InvitationOut])
def list_invitations(
    broker_firm_id: str | None = Query(None),
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> list[InvitationOut]:
    firm_id = _resolve_target_firm(user, broker_firm_id, db)
    invites = db.execute(
        select(Invitation).where(
            Invitation.broker_firm_id == firm_id,
            Invitation.status == INVITE_STATUS_PENDING,
        ).order_by(Invitation.created_at.desc())
    ).scalars().all()
    out: list[InvitationOut] = []
    for inv in invites:
        u = db.query(User).filter(User.email == inv.email).one_or_none()
        out.append(InvitationOut(
            id=inv.id, email=inv.email, role=inv.role, status=inv.status,
            broker_firm_id=inv.broker_firm_id, token=inv.token,
            user_id=u.id if u else "", expires_at=inv.expires_at,
        ))
    return out


@router.post("/invitations/{invitation_id}/revoke", status_code=200)
def revoke_invitation(
    invitation_id: str,
    user: CurrentUser = Depends(require_firm_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    inv = db.get(Invitation, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if user.role != "system_admin" and inv.broker_firm_id != user.broker_firm_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    inv.status = INVITE_STATUS_REVOKED
    # If the invited user never signed in, disable the provisioned row.
    invited = (
        db.query(User)
        .filter(User.email == inv.email, User.status == USER_STATUS_INVITED)
        .one_or_none()
    )
    if invited is not None:
        invited.status = USER_STATUS_DISABLED
    write_audit(db, user, action="revoke", entity_type="invitation", entity_id=inv.id,
                after={"email": inv.email})
    db.commit()
    return {"revoked": True}
