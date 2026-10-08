"""Portal invites: a one-time password, mailed to the member and nobody else.

The rollout rule this module exists to enforce:

- **Credentials go to the member's own mailbox, never to a broker or HR user.**
  There is deliberately no bulk export of passwords and no reveal in the UI —
  the plaintext returned by `issue_invite_credential` is handed straight to the
  mailer and dropped.
- **The mailed password is single-use.** The account is stamped rotation-due
  (`must_rotate_after = now`), so `member_login` diverts the very first sign-in
  into set-password and the mailed value dies there.
- **It expires.** An unread invite is a standing credential in an inbox, so it
  is bounded by `INVITE_TTL_DAYS`; every path that sets a real password clears
  the deadline (`clear_invite_expiry`).

Delivery is reported truthfully: `send_member_invite` returns False on any mail
fault, and only a True result may stamp `invite_sent_at` — the column the bulk
send targets on. A failed send therefore stays NULL and is retried by the next
run, while a delivered one is never re-sent.

Links point at the company's own broker (`portal_sign_in_url`), and a broker
with no web address yet gets no mail at all: callers resolve the link BEFORE
issuing a credential and stop on `FirmOriginUnavailable`.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core import passwords as PW
from app.core.mailer import get_mailer
from app.core.settings import get_settings
from app.core.tenant_resolution import public_origin
from app.models import Client, MemberAccount
from app.models.platform import DOMAIN_SURFACE_CLIENT
from app.services.brand import Brand

logger = logging.getLogger(__name__)

# How long a mailed one-time password stays usable. Long enough to survive a
# staggered rollout and a holiday; short enough that an unopened invite doesn't
# remain a live credential indefinitely.
INVITE_TTL_DAYS = 30

# Unambiguous alphabet — the member types this off an email, so no O/0, I/l/1.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_MIN_LENGTH = 14
_MAX_LENGTH = 64


@dataclass(frozen=True)
class CredentialSnapshot:
    """Prior credential state, so a failed send can be rolled back.

    Issuing writes the password hash BEFORE the mail attempt (the member must be
    able to use what we mail). If the send then fails, restoring this leaves the
    account exactly as it was rather than holding a password nobody received.
    """

    password_hash: str | None
    password_updated_at: datetime | None
    must_rotate_after: datetime | None
    invite_expires_at: datetime | None


def snapshot_credential(account: MemberAccount) -> CredentialSnapshot:
    return CredentialSnapshot(
        password_hash=account.password_hash,
        password_updated_at=account.password_updated_at,
        must_rotate_after=account.must_rotate_after,
        invite_expires_at=account.invite_expires_at,
    )


def restore_credential(account: MemberAccount, prior: CredentialSnapshot) -> None:
    account.password_hash = prior.password_hash
    account.password_updated_at = prior.password_updated_at
    account.must_rotate_after = prior.must_rotate_after
    account.invite_expires_at = prior.invite_expires_at


def invite_expired(account: MemberAccount, now: datetime | None = None) -> bool:
    """True when a mailed one-time password has passed its deadline.

    NULL means there is no invite outstanding (never sent, or already replaced
    by a real password) — never expired.
    """
    deadline = account.invite_expires_at
    if deadline is None:
        return False
    if deadline.tzinfo is None:  # SQLite hands back naive datetimes
        deadline = deadline.replace(tzinfo=UTC)
    return deadline <= (now or datetime.now(UTC))


def clear_invite_expiry(account: MemberAccount) -> None:
    """Call from every path that sets a REAL password. The deadline only ever
    applies to a mailed one-time password that hasn't been used yet."""
    account.invite_expires_at = None


def generate_one_time_password(min_entropy_bits: int = 0) -> str:
    """A random password that satisfies the tenant's own strength floor.

    Lengthened until it clears `min_entropy_bits` rather than assuming a fixed
    size is enough: the floor is broker-configurable, and a generated credential
    the tenant's own policy would reject is a rollout that fails at the last
    step for reasons no one can see. Not breach-checked — a fresh random string
    cannot be in a breach corpus, and one HIBP round trip per member would make
    a 500-employee send unusable.
    """
    length = _MIN_LENGTH
    while True:
        password = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        ok, _ = PW.password_meets_policy(password, min_entropy_bits)
        if ok or length >= _MAX_LENGTH:
            return password
        length = min(length + 4, _MAX_LENGTH)


def issue_invite_credential(
    account: MemberAccount, min_entropy_bits: int = 0, now: datetime | None = None
) -> str:
    """Set a fresh one-time password on `account`; return the plaintext.

    Mutates only — the caller owns the transaction, and must commit BEFORE
    mailing so the credential is live by the time it lands. `must_rotate_after`
    is stamped in the past so the first sign-in is diverted into set-password.
    """
    now = now or datetime.now(UTC)
    password = generate_one_time_password(min_entropy_bits)
    account.password_hash = PW.hash_password(password)
    account.password_updated_at = now
    # Already due: `rotation_due` is `deadline <= now`.
    account.must_rotate_after = now
    account.invite_expires_at = now + timedelta(days=INVITE_TTL_DAYS)
    account.failed_attempts = 0
    account.locked_until = None
    return password


def portal_sign_in_url(db: Session, client: Client) -> str:
    """Absolute portal sign-in URL for one company, on its broker's own address.

    The origin is the broker firm's client-facing address
    (`tenant_resolution.public_origin`): its own domain, its neutral fallback
    address, or — for the platform owner's firm — the platform origin. Raises
    `FirmOriginUnavailable` when the firm has none; the caller must not send.

    The company must be IN the link. The host names only the broker, and a
    member arriving without the company is told their details weren't
    recognised — indistinguishable from a wrong password, on the one screen
    where that misdiagnosis is most expensive.

    It goes in the PATH (`/portal/cdl/sign-in`) rather than the old
    `?company=cdl`, so the address a member is emailed is the same one they keep
    using — a query param that the app strips on arrival left them holding a
    link that worked once and then named no company. The old form still resolves
    (`captureTenantSlugFromUrl` promotes it into the path), which it must:
    unopened invites are live credentials for `INVITE_TTL_DAYS`.
    """
    origin = public_origin(db, client.broker_firm_id, DOMAIN_SURFACE_CLIENT)
    slug = client.slug
    return f"{origin}/portal/{slug}/sign-in" if slug else f"{origin}/portal/sign-in"


def mail_deliverable() -> bool:
    """Whether the configured mailer can deliver an invite.

    This is the production failure worth catching before a rollout, not after:
    `INSPRO_MAIL_MODE=smtp` with an empty `INSPRO_SMTP_HOST` raises on
    construction, so every send fails and the run reports hundreds queued while
    delivering none. Which is the live prod configuration today.

    `log` mode deliberately counts as deliverable in dev/staging. Production
    normalizes both `log` and an explicit `disabled` to a fail-closed mailer, so
    the UI reports delivery as unavailable without exposing invite credentials.
    """
    try:
        if get_settings().mail_mode == "disabled":
            return False
        get_mailer()
        return True
    except Exception:
        logger.warning("Mail is not deliverable — the configured mailer failed to build")
        return False


def login_username(account: MemberAccount, source: str | None = None) -> str:
    """What this member should be told to type, per the company's setting.

    `source` is `client_auth_policy.portal_login_source` (email | system_id |
    staff_id) — the broker's answer to "what do employees sign in with". Sign-in
    itself still accepts any of the three (`resolve_member_credential`), so this
    only decides what we PRINT: the invite email and the broker's Portal access
    panel. Both read it here, so they cannot tell a member two different things.

    Every branch falls back rather than returning blank — a company set to
    `email` still has email-less members, and telling one of them their username
    is "" is worse than telling them their system id.
    """
    if source == "system_id":
        return account.system_login_id or account.email or account.staff_id
    if source == "staff_id":
        return account.staff_id
    return account.email or account.system_login_id or account.staff_id


def send_member_invite(
    account: MemberAccount,
    password: str,
    sign_in_url: str,
    login_source: str | None = None,
    *,
    brand: Brand | None = None,
) -> bool:
    """Deliver the invite, sent as the company's `brand`. Mail faults are
    logged, never raised.

    `sign_in_url` is the company's `portal_sign_in_url`, resolved by the caller
    before it issued `password` — a broker with no address must stop the send
    before any credential changes.

    True ONLY when the mailer accepted the message — the caller may stamp
    `invite_sent_at` on True and must not on False, which is what keeps a mail
    outage from masquerading as a completed rollout.
    """
    if not account.email:
        return False
    try:
        get_mailer(brand).send_member_invite(
            account.email,
            login_username(account, login_source),
            password,
            sign_in_url,
        )
        return True
    except Exception:
        logger.exception("Failed to send portal invite to %s", account.email)
        return False
