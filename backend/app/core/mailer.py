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


class LogMailer:
    def send_otp(self, email: str, code: str, magic_link: str) -> None:
        logger.info("Portal OTP for %s: %s (magic link: %s)", email, code, magic_link)


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

    def send_otp(self, email: str, code: str, magic_link: str) -> None:
        msg = _otp_message(email, code, magic_link, self.sender)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


class AcsMailer:
    def __init__(self) -> None:
        raise RuntimeError(
            "INSPRO_MAIL_MODE=acs is not implemented yet — use smtp or log."
        )

    def send_otp(self, email: str, code: str, magic_link: str) -> None:  # pragma: no cover
        raise NotImplementedError


def get_mailer() -> Mailer:
    mode = get_settings().mail_mode
    if mode == "smtp":
        return SmtpMailer()
    if mode == "acs":
        return AcsMailer()
    return LogMailer()
