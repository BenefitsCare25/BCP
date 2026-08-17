"""Outbound mail for portal OTP delivery.

Mode selected by `INSPRO_MAIL_MODE`:

- `log` (default): the code is logged at INFO — dev/local, and the safe prod
  fallback until SMTP/ACS is configured.
- `smtp`: plain SMTP via `INSPRO_SMTP_HOST/PORT/USER/PASSWORD/FROM`
  (STARTTLS when the server offers it).
- `acs`: Azure Communication Services — stubbed; raises until implemented so a
  misconfigured deploy fails loudly instead of silently dropping codes.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.settings import get_settings

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    def send_otp(self, email: str, code: str, magic_link: str) -> None: ...

    def send_member_invite(
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None: ...

    def send_claim_update(self, email: str, portal_url: str) -> None: ...


def _invite_message(
    email: str, username: str, password: str, sign_in_url: str, sender: str
) -> EmailMessage:
    """Portal welcome: username + a ONE-TIME password.

    The password is single-use by construction — the account is stamped
    rotation-due, so the first sign-in immediately hands the member a
    set-password step and the mailed value dies there. It is the only copy that
    ever exists: no broker, HR user or admin sees it (that is the whole reason
    invites go to the member's own mailbox rather than out as a list).
    """
    msg = EmailMessage()
    msg["Subject"] = "Your employee benefits portal account"
    msg["From"] = sender
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


def _otp_message(email: str, code: str, magic_link: str, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Your Inspro sign-in code: {code}"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        f"Your one-time sign-in code is: {code}\n\n"
        f"Or sign in directly: {magic_link}\n\n"
        "The code expires in 10 minutes. If you didn't request this, "
        "you can ignore this email."
    )
    return msg


def _claim_update_message(email: str, portal_url: str, sender: str) -> EmailMessage:
    """Generic on purpose: no medical or decision detail on a lock screen."""
    msg = EmailMessage()
    msg["Subject"] = "You have an update in your benefits portal"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        "There is an update about one of your claims in the employee benefits "
        "portal.\n\n"
        f"Sign in to view it: {portal_url}\n\n"
        "For your privacy, claim and medical details are not included in email."
    )
    return msg


class LogMailer:
    def send_otp(self, email: str, code: str, magic_link: str) -> None:
        logger.info("Portal OTP for %s: %s (magic link: %s)", email, code, magic_link)

    def send_member_invite(
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None:
        logger.info(
            "Portal invite for %s: username=%s password=%s (%s)",
            email, username, password, sign_in_url,
        )

    def send_claim_update(self, email: str, portal_url: str) -> None:
        logger.info("Claim update email accepted for %s (%s)", email, portal_url)


class SmtpMailer:
    def __init__(self) -> None:
        self.host = os.environ.get("INSPRO_SMTP_HOST", "").strip()
        self.port = int(os.environ.get("INSPRO_SMTP_PORT", "587"))
        self.user = os.environ.get("INSPRO_SMTP_USER", "").strip()
        self.password = os.environ.get("INSPRO_SMTP_PASSWORD", "")
        self.sender = os.environ.get("INSPRO_SMTP_FROM", self.user).strip()
        if not self.host or not self.sender:
            raise RuntimeError(
                "INSPRO_MAIL_MODE=smtp requires INSPRO_SMTP_HOST and "
                "INSPRO_SMTP_FROM (or INSPRO_SMTP_USER)."
            )

    def _send(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)

    def send_otp(self, email: str, code: str, magic_link: str) -> None:
        self._send(_otp_message(email, code, magic_link, self.sender))

    def send_member_invite(
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None:
        self._send(_invite_message(email, username, password, sign_in_url, self.sender))

    def send_claim_update(self, email: str, portal_url: str) -> None:
        self._send(_claim_update_message(email, portal_url, self.sender))


class AcsMailer:
    def __init__(self) -> None:
        raise RuntimeError(
            "INSPRO_MAIL_MODE=acs is not implemented yet — use smtp or log."
        )

    def send_otp(self, email: str, code: str, magic_link: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def send_member_invite(  # pragma: no cover
        self, email: str, username: str, password: str, sign_in_url: str
    ) -> None:
        raise NotImplementedError

    def send_claim_update(self, email: str, portal_url: str) -> None:  # pragma: no cover
        raise NotImplementedError


def get_mailer() -> Mailer:
    mode = get_settings().mail_mode
    if mode == "smtp":
        return SmtpMailer()
    if mode == "acs":
        return AcsMailer()
    return LogMailer()
