"""Control-plane member identity for the employee self-service portal.

A `MemberAccount` is the stable, cross-policy-year login identity of one
insured employee of one client. It lives in `public` (control plane) because
authentication must resolve *before* a firm schema is known — the account's
`client_id` is what routes the request to the right firm schema.

Per-year `Employee` rows bind to an account via `employees.member_account_id`
(stamped at provisioning; lazily re-stamped by `(policy_year_id, staff_id)`
match when a new policy year's roster is uploaded). Members authenticate with
an email OTP — no passwords, no Entra dependency.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

MEMBER_STATUS_INVITED = "invited"
MEMBER_STATUS_ACTIVE = "active"
MEMBER_STATUS_DISABLED = "disabled"


class MemberAccount(Base, TimestampMixin):
    __tablename__ = "member_accounts"
    __table_args__ = (
        UniqueConstraint("client_id", "email", name="uq_member_accounts_client_email"),
        UniqueConstraint("client_id", "staff_id", name="uq_member_accounts_client_staff"),
        UniqueConstraint(
            "client_id", "system_login_id", name="uq_member_accounts_client_system_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Email is nullable: not every employee has one (they sign in with a
    # system-generated or staff id instead).
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    staff_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MEMBER_STATUS_INVITED, index=True
    )
    # Broker user who provisioned the account (plain string, like audit rows).
    invited_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # When the invite email was CONFIRMED delivered to the mailer. NULL means
    # "never received one", and that is the whole targeting rule for the bulk
    # send: it only ever touches NULL rows, so a member can never be emailed an
    # invite twice, and a send that failed (mail outage) stays NULL and is
    # picked up by the next run. Stamped only after a successful send.
    invite_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Deadline on the MAILED one-time password. Set when an invite goes out and
    # cleared by every path that sets a real password, so it can only ever gate
    # a credential still sitting unused in a mailbox. NULL = no invite pending.
    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sign_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ── Credential login (username + Argon2id password) ──
    # Broker-generated alternate username (e.g. "EM-7Q2M8K"); unique per client.
    system_login_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    must_rotate_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None


class MemberOtpCode(Base, TimestampMixin):
    """One-time sign-in code. Only the SHA-256 hash is stored; a code is
    consumed on successful verify or after too many failed attempts."""

    __tablename__ = "member_otp_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_account_id: Mapped[str] = mapped_column(
        ForeignKey("member_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
