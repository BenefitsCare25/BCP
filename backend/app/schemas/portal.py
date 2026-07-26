"""Pydantic schemas for the employee portal + broker-side member provisioning."""
from __future__ import annotations

from datetime import datetime

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


class PortalMe(BaseModel):
    member: PortalMemberOut
    # None when the client has no active policy year / the member has no
    # roster row in it (statement and claims endpoints 404 in that state).
    employee: PortalEmployeeOut | None = None
    policy_year: PortalPolicyYearOut | None = None
    flex_eligible: bool = False
    # True while an enrollment window is open and in-period for the member's
    # policy year — drives the "Enrollment open" call-to-action in the shell.
    enrollment_open: bool = False


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
    # True once the member has set a password (credential login enabled).
    has_password: bool = False
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


class MemberAccountCreateIn(BaseModel):
    # Overrides the roster email when provided (e.g. roster has none/stale).
    email: str | None = Field(default=None, max_length=320)


class MemberAccountPatch(BaseModel):
    status: str  # active | disabled


class BulkInviteIn(BaseModel):
    policy_year_id: str


class BulkInviteResult(BaseModel):
    invited: int
    skipped_existing: int
    skipped_no_email: int
    # Accounts created whose invite email failed to send (mail outage ≠ rollout
    # success — the broker must resend these once mail is fixed).
    mail_failed: int = 0


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
