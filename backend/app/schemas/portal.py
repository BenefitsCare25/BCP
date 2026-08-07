"""Pydantic schemas for the employee portal + broker-side member provisioning."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Portal auth (public OTP flow) ─────────────────────────────────────────────


class OtpRequestIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class OtpRequestOut(BaseModel):
    status: str = "sent"
    # Populated ONLY in dev + mock auth mode so local sign-in works without a
    # mail server (mirrors the deferred invitation-email posture).
    debug_code: str | None = None


class OtpVerifyIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=4, max_length=12)


class PortalMemberOut(_Base):
    id: str
    email: str | None = None
    staff_id: str
    display_name: str | None = None


class OtpVerifyOut(BaseModel):
    token: str
    expires_at: datetime
    member: PortalMemberOut


# ── Portal profile ────────────────────────────────────────────────────────────


class PortalPolicyYearOut(BaseModel):
    id: str
    year: int
    start_date: str
    end_date: str


class PortalEmployeeOut(BaseModel):
    id: str
    staff_id: str
    employee_name: str | None = None


class PortalCompanyOut(BaseModel):
    """The member's employer, as the member should see it.

    Two things need this and neither could get it before: the shell has no way
    to name the company a member is signed in to, and on a single-host
    deployment the URL's `/portal/{slug}` segment is unverified — a member
    holding a valid token could sit on another company's path and nothing would
    notice, because tenancy is resolved from the TOKEN, not the URL. Serving the
    slug lets the client correct the address to the one it actually resolved.
    """

    slug: str | None = None
    # The broker's internal short handle ("CDL"). Present as a FALLBACK only —
    # `legal_name` is what a member recognises.
    name: str
    legal_name: str | None = None


class PortalMe(BaseModel):
    member: PortalMemberOut
    # Never None: a member token always resolves to a client row.
    company: PortalCompanyOut
    # None when the client has no active policy year / the member has no
    # roster row in it (statement and claims endpoints 404 in that state).
    employee: PortalEmployeeOut | None = None
    policy_year: PortalPolicyYearOut | None = None
    flex_eligible: bool = False
    # True while an enrollment window is open and in-period for the member's
    # policy year — drives the "Enrollment open" call-to-action in the shell.
    enrollment_open: bool = False
    # NOTE: there is deliberately no `unread_messages` here. It existed briefly
    # to badge Home in the shell, but a dot on Home names nothing — unread is
    # stated in words on the home Messages tile, which reads the count off
    # `GET /portal/messages`. Adding it back means a COUNT query on this hot
    # endpoint for a mark that has to explain itself some other way.


# ── Broker-side member-account provisioning ───────────────────────────────────


class MemberAccountOut(_Base):
    id: str
    client_id: str
    email: str | None = None
    staff_id: str
    display_name: str | None = None
    status: str
    invited_by: str | None = None
    last_sign_in_at: datetime | None = None
    created_at: datetime
    # Broker-generated alternate username; None until allocated.
    system_login_id: str | None = None
    # True once a password exists (invite issued, or one set directly).
    has_password: bool = False
    # What THIS member should be told to type, resolved from the company's
    # `portal_login_source` setting. SERVED, never re-derived in the UI: the
    # setting lives behind a firm-admin-only endpoint that Member Coverage
    # cannot call, so a client-side guess there silently ignored the setting and
    # printed the email for a company signing in by system ID.
    login_username: str | None = None
    # When an invite email was confirmed delivered. None = never sent, which is
    # what the bulk send targets — so the panel can say "not invited yet"
    # instead of leaving the broker to infer it from the status badge.
    invite_sent_at: datetime | None = None
    # Deadline on a mailed one-time password that hasn't been used yet.
    invite_expires_at: datetime | None = None
    # Set by invite/resend responses only (None on plain reads): False means
    # the account exists but the OTP email could not be delivered.
    mail_sent: bool | None = None
    # Set once by set-password-link responses — deliver to the member.
    set_password_token: str | None = None
    # The tenant's subdomain label, so the UI can build an ABSOLUTE
    # `{tenant_slug}.portal.<base>` link. The broker generating it is on a
    # different host, so a bare path would be unclickable when pasted into an
    # email — and the token is shown only once.
    tenant_slug: str | None = None


class MemberAccountList(BaseModel):
    total: int
    items: list[MemberAccountOut] = Field(default_factory=list)
    # Rules the UI states and enforces client-side. Served rather than mirrored
    # as TypeScript constants: a copy drifts the moment the real value moves,
    # and the failure is silent — a form that accepts a password the server
    # rejects, or a link described as lasting longer than it does.
    password_min_length: int = 0
    set_password_ttl_hours: int = 0


class MemberAccountCreateIn(BaseModel):
    # Overrides the roster email when provided (e.g. roster has none/stale).
    email: str | None = Field(default=None, max_length=320)


class MemberAccountPatch(BaseModel):
    status: str  # active | disabled


class BulkInviteIn(BaseModel):
    policy_year_id: str


class BulkInviteResult(BaseModel):
    """Outcome of one bulk send. Every employee falls in exactly one bucket.

    Delivery runs in the background (Argon2id + SMTP per member is far too slow
    to hold a request open for a full roster), so this reports what was QUEUED.
    Progress is read back from `PortalRolloutOut`, whose `invited` count rises
    as invites land — and because targeting is `invite_sent_at IS NULL`, a run
    cut short by a restart is resumed by pressing the button again, with no
    possibility of a second email to anyone already served.
    """

    # Invites dispatched for delivery.
    queued: int = 0
    # Accounts created by this run (covers queued members AND the no-email ones,
    # who are provisioned so they appear on the follow-up list).
    accounts_created: int = 0
    # Provisioned, but no email address on the roster to send to.
    no_email: int = 0
    # Not provisioned: shares an email address (or staff id) with another
    # employee, so it cannot have an account of its own.
    duplicate: int = 0
    # Already had an invite delivered, or already onboarded. Untouched — this is
    # what makes the button safe to press twice.
    already_invited: int = 0
    skipped_disabled: int = 0
    # True when a run was already in flight and this request did nothing. The
    # counts do not move for a minute or two while delivery works through the
    # roster, which reads like nothing happened — so a second press is refused
    # rather than allowed to re-queue members mid-send and mail them twice.
    already_sending: bool = False


class PortalRolloutMember(BaseModel):
    """One employee the rollout could not reach, and why.

    The reason is carried rather than implied: "no address on file" and "this
    address belongs to another employee" need different fixes, and a single
    undifferentiated list would send HR looking for a missing email that is
    sitting right there.
    """

    employee_id: str
    staff_id: str
    employee_name: str | None = None
    reason: Literal["no_email", "duplicate"] = "no_email"
    email: str | None = None


class PortalRolloutOut(BaseModel):
    """Portal-access state of the whole roster, for the rollout card.

    `invite_pending` is what the send button targets and what its label counts,
    so the number on the button is the number the endpoint will act on.
    """

    employees_total: int
    invite_pending: int
    invited: int          # invite delivered, not signed in yet
    signed_in: int        # has used the portal (or has a password set)
    no_email: int         # provisioned, but nowhere to send
    # Roster rows whose email (or staff id) already belongs to another employee.
    # Not provisioned at all: an account is unique per client on both, and
    # sharing one would put a member's benefits in a colleague's inbox.
    duplicate: int = 0
    disabled: int
    # False when the configured mailer cannot even be CONSTRUCTED — which is the
    # real production failure: `INSPRO_MAIL_MODE=smtp` with no `INSPRO_SMTP_HOST`
    # raises, every send fails, and a rollout reports hundreds queued and
    # delivers none. Checked BEFORE the button is offered rather than discovered
    # after pressing it.
    mail_deliverable: bool
    # The delivery mode, so `log` (dev/staging default — invites are written to
    # the application log, not emailed) can be WARNED about without disabling
    # the button: it is how the flow is rehearsed before a real rollout, and
    # prod is fail-closed against it at boot anyway.
    mail_mode: str
    # True while a delivery run is working through the roster. Delivery is slow
    # (Argon2id per member), so without this the card looks idle mid-send and
    # the button invites a second press.
    sending: bool = False
    # Everyone the send could not reach, with their reason. Capped for render.
    needs_attention: list[PortalRolloutMember] = Field(default_factory=list)
    needs_attention_truncated: bool = False


# ── Broker-side portal preview ("employee view") ─────────────────────────────


class PortalPreviewOut(BaseModel):
    """Context for the broker's read-only portal preview of one employee.
    Mirrors `PortalMe` but keyed off the selected Employee row (any year),
    plus the portal-account state so the preview can show access status."""

    employee: PortalEmployeeOut
    policy_year: PortalPolicyYearOut | None = None
    flex_eligible: bool = False
    # False when previewing a draft/closed year — the live portal only ever
    # shows the client's active policy year.
    is_active_policy_year: bool = False
    member_account: MemberAccountOut | None = None
    # Mirrors PortalMe.enrollment_open for the previewed employee.
    enrollment_open: bool = False
