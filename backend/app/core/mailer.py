"""Outbound mail: portal invites, staff invitations, claim updates and
workflow notices.

Mode selected by `INSPRO_MAIL_MODE`:

- `disabled`: delivery is rejected without logging message contents.
- `log` (default): messages (including portal invite credentials, but never a
  staff invitation link) are logged at INFO for dev/local only. Production
  settings normalize this legacy value to `disabled` during rolling upgrades.
- `smtp`: plain SMTP via `INSPRO_SMTP_HOST/PORT/USER/PASSWORD/FROM`
  (STARTTLS when the server offers it).
- `acs`: Azure Communication Services — stubbed; raises until implemented so a
  misconfigured deploy fails loudly instead of silently dropping mail.

Every message carries the sending firm's brand (`services/brand.Brand`): its
sender display name, its Reply-To when set, and its own From address only once
verified (the resolver withholds an unverified one) — otherwise the platform
sender `INSPRO_SMTP_FROM`. The built-in brand leaves the platform sender as is.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import TYPE_CHECKING, Protocol

from app.core.settings import get_settings

if TYPE_CHECKING:
    from app.services.brand import Brand

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    def send_member_invite(
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None: ...

    def send_staff_invite(self, email: str, firm_name: str, invite_url: str) -> None: ...

    def send_claim_update(self, email: str, portal_url: str) -> None: ...

    def send_claim_digest(self, email: str, claim_urls: list[str]) -> None: ...

    def send_workflow_notice(self, email: str, subject: str, body: str) -> None: ...


def _one_line(value: str) -> str:
    return " ".join(value.split())


def sender_address(brand: Brand | None, platform_sender: str) -> str:
    """The From header: the platform sender for the built-in brand, else the
    brand's display name on its verified address or the platform address."""
    if brand is None or brand.is_default:
        return platform_sender
    address = brand.email_from_address or parseaddr(platform_sender)[1] or platform_sender
    return formataddr((_one_line(brand.email_sender_name), _one_line(address)))


def _stamp_identity(msg: EmailMessage, brand: Brand | None, platform_sender: str) -> None:
    msg["From"] = sender_address(brand, platform_sender)
    if brand is not None and brand.email_reply_to:
        msg["Reply-To"] = _one_line(brand.email_reply_to)


def _invite_message(email: str, username: str, password: str, sign_in_url: str) -> EmailMessage:
    """Portal welcome: username + a ONE-TIME password.

    The password is single-use by construction — the account is stamped
    rotation-due, so the first sign-in immediately hands the member a
    set-password step and the mailed value dies there. It is the only copy that
    ever exists: no broker, HR user or admin sees it (that is the whole reason
    invites go to the member's own mailbox rather than out as a list).
    """
    msg = EmailMessage()
    msg["Subject"] = "Your employee benefits portal account"
    msg["To"] = email
    msg.set_content(
        "Your employee benefits portal account is ready.\n\n"
        f"    Sign in:    {sign_in_url}\n"
        f"    Username:   {username}\n"
        f"    Password:   {password}\n\n"
        "You'll be asked to choose your own password the first time you sign "
        "in — the password above stops working at that point, so there's "
        "nothing to keep.\n\n"
        "From the portal you can see what you're covered for, check what's "
        "left of your limits, submit claims and manage your dependants.\n\n"
        "If you weren't expecting this, please contact your HR team."
    )
    return msg


def _staff_invite_message(email: str, firm_name: str, invite_url: str) -> EmailMessage:
    """A broker firm's invitation to its staff app.

    The link carries a single-use token in its fragment; this message is the
    only place it is sent. Plain copy naming the firm, never the platform.
    """
    msg = EmailMessage()
    msg["Subject"] = f"Your invitation to {firm_name}"
    msg["To"] = email
    msg.set_content(
        f"You have been invited to sign in to {firm_name}'s employee benefits "
        "workspace.\n\n"
        f"Open this link to set up how you sign in:\n{invite_url}\n\n"
        "The link works once and expires in 14 days. If you weren't expecting "
        f"this invitation, ignore this email or contact {firm_name}."
    )
    return msg


def _claim_update_message(email: str, portal_url: str) -> EmailMessage:
    """Generic on purpose: no medical or decision detail on a lock screen."""
    msg = EmailMessage()
    msg["Subject"] = "You have an update in your benefits portal"
    msg["To"] = email
    msg.set_content(
        "There is an update about one of your claims in the employee benefits "
        "portal.\n\n"
        f"Sign in to view it: {portal_url}\n\n"
        "For your privacy, claim and medical details are not included in email."
    )
    return msg


def _claim_digest_message(email: str, claim_urls: list[str]) -> EmailMessage:
    """Only authenticated links; no names, diagnoses, amounts or decisions."""
    msg = EmailMessage()
    msg["Subject"] = "Your benefits portal claim updates"
    msg["To"] = email
    links = "\n".join(f"Claim {index}: {url}" for index, url in enumerate(claim_urls, 1))
    msg.set_content(
        f"There are updates for {len(claim_urls)} of your claims.\n\n"
        f"Sign in to view each conversation:\n{links}\n\n"
        "For your privacy, claim and medical details are not included in email."
    )
    return msg


class LogMailer:
    def send_workflow_notice(self, email: str, subject: str, body: str) -> None:
        logger.info("Workflow notice accepted by local log mailer")

    def send_member_invite(
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None:
        logger.info(
            "Portal invite for %s: username=%s password=%s (%s)",
            email,
            username,
            password,
            sign_in_url,
        )

    def send_staff_invite(self, email: str, firm_name: str, invite_url: str) -> None:
        # Never the link: it carries a single-use sign-in token.
        logger.info("Staff invitation email accepted for %s (%s)", email, firm_name)

    def send_claim_update(self, email: str, portal_url: str) -> None:
        logger.info("Claim update email accepted for %s (%s)", email, portal_url)

    def send_claim_digest(self, email: str, claim_urls: list[str]) -> None:
        logger.info("Claim digest accepted with %s claim links", len(claim_urls))


class DisabledMailer:
    """Fail closed when production mail delivery has not been configured."""

    @staticmethod
    def _raise() -> None:
        raise RuntimeError("Outbound mail is disabled.")

    def send_member_invite(
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None:
        self._raise()

    def send_staff_invite(self, email: str, firm_name: str, invite_url: str) -> None:
        self._raise()

    def send_claim_update(self, email: str, portal_url: str) -> None:
        self._raise()

    def send_claim_digest(self, email: str, claim_urls: list[str]) -> None:
        self._raise()

    def send_workflow_notice(self, email: str, subject: str, body: str) -> None:
        self._raise()


class SmtpMailer:
    def __init__(self, brand: Brand | None = None) -> None:
        self.host = os.environ.get("INSPRO_SMTP_HOST", "").strip()
        self.port = int(os.environ.get("INSPRO_SMTP_PORT", "587"))
        self.user = os.environ.get("INSPRO_SMTP_USER", "").strip()
        self.password = os.environ.get("INSPRO_SMTP_PASSWORD", "")
        self.sender = os.environ.get("INSPRO_SMTP_FROM", self.user).strip()
        self.brand = brand
        if not self.host or not self.sender:
            raise RuntimeError(
                "INSPRO_MAIL_MODE=smtp requires INSPRO_SMTP_HOST and "
                "INSPRO_SMTP_FROM (or INSPRO_SMTP_USER)."
            )

    def _send(self, msg: EmailMessage) -> None:
        _stamp_identity(msg, self.brand, self.sender)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            elif get_settings().env == "prod":
                raise RuntimeError("Production SMTP server does not advertise STARTTLS.")
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)

    def send_member_invite(
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None:
        self._send(_invite_message(email, username, password, sign_in_url))

    def send_staff_invite(self, email: str, firm_name: str, invite_url: str) -> None:
        self._send(_staff_invite_message(email, firm_name, invite_url))

    def send_claim_update(self, email: str, portal_url: str) -> None:
        self._send(_claim_update_message(email, portal_url))

    def send_claim_digest(self, email: str, claim_urls: list[str]) -> None:
        self._send(_claim_digest_message(email, claim_urls))

    def send_workflow_notice(self, email: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["To"] = subject, email
        msg.set_content(body)
        self._send(msg)


class AcsMailer:
    def __init__(self) -> None:
        raise RuntimeError("INSPRO_MAIL_MODE=acs is not implemented yet — use smtp or log.")

    def send_member_invite(  # pragma: no cover
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None:
        raise NotImplementedError

    def send_staff_invite(  # pragma: no cover
        self, email: str, firm_name: str, invite_url: str
    ) -> None:
        raise NotImplementedError

    def send_claim_update(self, email: str, portal_url: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def send_claim_digest(self, email: str, claim_urls: list[str]) -> None:  # pragma: no cover
        raise NotImplementedError

    def send_workflow_notice(self, email: str, subject: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError


def get_mailer(brand: Brand | None = None) -> Mailer:
    """The configured mailer, sending as `brand` (the built-in brand if None)."""
    mode = get_settings().mail_mode
    if mode == "disabled":
        return DisabledMailer()
    if mode == "smtp":
        return SmtpMailer(brand)
    if mode == "acs":
        return AcsMailer()
    return LogMailer()
