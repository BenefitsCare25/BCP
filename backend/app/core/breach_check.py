"""HaveIBeenPwned breach check via k-anonymity (privacy-preserving).

Only the first 5 chars of the password's SHA-1 are sent to the range API; the
full hash never leaves the process. Fail-OPEN (return False) on any network or
API error so a HIBP outage can't lock users out of setting a password — the
failure is logged, and the per-tenant `breach_check_enabled` flag can disable
the call entirely.
"""
from __future__ import annotations

import hashlib
import logging

import httpx

logger = logging.getLogger(__name__)

_RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"
_TIMEOUT = 3.0


def is_breached(password: str, *, timeout: float = _TIMEOUT) -> bool:
    """True if the password appears in the HIBP corpus. False on match-miss OR error."""
    sha1 = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        resp = httpx.get(
            _RANGE_URL.format(prefix=prefix),
            headers={"Add-Padding": "true"},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("HIBP breach check unavailable (%s) — allowing password", exc)
        return False
    for line in resp.text.splitlines():
        hash_suffix, _, count = line.partition(":")
        if hash_suffix.strip().upper() == suffix and count.strip() not in ("", "0"):
            return True
    return False
